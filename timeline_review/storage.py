import json
import os
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import copy

from .models import Event, EventStatus, ParseError, Phase
from .config import RuleConfig


STORAGE_DIR_NAME = ".timeline_review"


class StorageError(Exception):
    pass


class BatchNotFoundError(StorageError):
    pass


class StateStore:
    def __init__(self, base_dir: Optional[str] = None):
        if base_dir:
            self.base_path = Path(base_dir)
        else:
            self.base_path = Path.cwd()
        self.storage_dir = self.base_path / STORAGE_DIR_NAME
        self.storage_dir.mkdir(exist_ok=True)
        self._active_batch: Optional[str] = None

    def _get_batch_dir(self, batch_id: str) -> Path:
        return self.storage_dir / f"batch_{batch_id}"

    def _ensure_batch_dir(self, batch_id: str) -> Path:
        batch_dir = self._get_batch_dir(batch_id)
        batch_dir.mkdir(exist_ok=True)
        (batch_dir / "events").mkdir(exist_ok=True)
        (batch_dir / "imports").mkdir(exist_ok=True)
        (batch_dir / "exports").mkdir(exist_ok=True)
        return batch_dir

    def _batch_meta_path(self, batch_id: str) -> Path:
        return self._get_batch_dir(batch_id) / "batch_meta.json"

    def _events_path(self, batch_id: str) -> Path:
        return self._get_batch_dir(batch_id) / "events.json"

    def _parse_errors_path(self, batch_id: str) -> Path:
        return self._get_batch_dir(batch_id) / "parse_errors.json"

    def _config_path(self, batch_id: str) -> Path:
        return self._get_batch_dir(batch_id) / "rules_config.json"

    def _imports_index_path(self, batch_id: str) -> Path:
        return self._get_batch_dir(batch_id) / "imports_index.json"

    def _active_batch_path(self) -> Path:
        return self.storage_dir / "active_batch.json"

    def list_batches(self) -> List[Dict]:
        batches = []
        if not self.storage_dir.exists():
            return batches
        for item in self.storage_dir.iterdir():
            if item.is_dir() and item.name.startswith("batch_"):
                meta_path = item / "batch_meta.json"
                if meta_path.exists():
                    try:
                        with open(meta_path, "r", encoding="utf-8") as f:
                            meta = json.load(f)
                        batches.append(meta)
                    except (json.JSONDecodeError, IOError):
                        pass
        batches.sort(key=lambda b: b.get("created_at", ""), reverse=True)
        return batches

    def create_batch(self, name: str, description: str = "") -> Dict:
        batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._ensure_batch_dir(batch_id)
        meta = {
            "id": batch_id,
            "name": name,
            "description": description,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "event_count": 0,
            "status": "active",
        }
        with open(self._batch_meta_path(batch_id), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        self.save_config(batch_id, RuleConfig())
        self.save_events(batch_id, [])
        self.save_parse_errors(batch_id, [])
        self._write_imports_index(batch_id, [])
        self._set_active_batch(batch_id)
        return meta

    def get_batch_meta(self, batch_id: str) -> Dict:
        path = self._batch_meta_path(batch_id)
        if not path.exists():
            raise BatchNotFoundError(f"批次不存在: {batch_id}")
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def update_batch_meta(self, batch_id: str, **kwargs) -> Dict:
        meta = self.get_batch_meta(batch_id)
        meta.update(kwargs)
        meta["updated_at"] = datetime.now().isoformat()
        with open(self._batch_meta_path(batch_id), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        return meta

    def _set_active_batch(self, batch_id: str) -> None:
        data = {"active_batch": batch_id, "updated_at": datetime.now().isoformat()}
        with open(self._active_batch_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self._active_batch = batch_id

    def get_active_batch(self) -> Optional[str]:
        if self._active_batch:
            return self._active_batch
        path = self._active_batch_path()
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._active_batch = data.get("active_batch")
                return self._active_batch
            except (json.JSONDecodeError, IOError):
                pass
        return None

    def switch_batch(self, batch_id: str) -> Dict:
        if not self._batch_meta_path(batch_id).exists():
            raise BatchNotFoundError(f"批次不存在: {batch_id}")
        self._set_active_batch(batch_id)
        return self.get_batch_meta(batch_id)

    def save_config(self, batch_id: str, config: RuleConfig) -> None:
        self._ensure_batch_dir(batch_id)
        with open(self._config_path(batch_id), "w", encoding="utf-8") as f:
            json.dump(config.to_dict(), f, ensure_ascii=False, indent=2)

    def load_config(self, batch_id: str) -> RuleConfig:
        path = self._config_path(batch_id)
        if not path.exists():
            return RuleConfig()
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return RuleConfig.from_dict(data)

    def save_events(self, batch_id: str, events: List[Event]) -> None:
        self._ensure_batch_dir(batch_id)
        data = [e.to_dict() for e in events]
        with open(self._events_path(batch_id), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self.update_batch_meta(batch_id, event_count=len(events))

    def load_events(self, batch_id: str) -> List[Event]:
        path = self._events_path(batch_id)
        if not path.exists():
            return []
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [Event.from_dict(d) for d in data]

    def get_event_by_id(self, batch_id: str, event_id: str) -> Optional[Event]:
        events = self.load_events(batch_id)
        for e in events:
            if e.id == event_id:
                return e
        return None

    def update_event(self, batch_id: str, event_id: str, **kwargs) -> Optional[Event]:
        events = self.load_events(batch_id)
        updated = None
        for i, e in enumerate(events):
            if e.id == event_id:
                for key, value in kwargs.items():
                    if hasattr(e, key):
                        setattr(e, key, value)
                e.updated_at = datetime.now()
                updated = e
                events[i] = e
                break
        if updated:
            self.save_events(batch_id, events)
        return updated

    def set_event_status(self, batch_id: str, event_id: str, status: EventStatus) -> Optional[Event]:
        return self.update_event(batch_id, event_id, status=status)

    def set_event_notes(self, batch_id: str, event_id: str, notes: str) -> Optional[Event]:
        return self.update_event(batch_id, event_id, notes=notes)

    def save_parse_errors(self, batch_id: str, errors: List[ParseError]) -> None:
        self._ensure_batch_dir(batch_id)
        data = [e.to_dict() for e in errors]
        with open(self._parse_errors_path(batch_id), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load_parse_errors(self, batch_id: str) -> List[ParseError]:
        path = self._parse_errors_path(batch_id)
        if not path.exists():
            return []
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        result = []
        for d in data:
            result.append(ParseError(
                source_file=d["source_file"],
                line_number=d["line_number"],
                error_type=d["error_type"],
                error_message=d["error_message"],
                raw_content=d.get("raw_content", ""),
            ))
        return result

    def _read_imports_index(self, batch_id: str) -> List[Dict]:
        path = self._imports_index_path(batch_id)
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []

    def _write_imports_index(self, batch_id: str, index: List[Dict]) -> None:
        self._ensure_batch_dir(batch_id)
        with open(self._imports_index_path(batch_id), "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)

    def is_file_imported(self, batch_id: str, file_path: str) -> bool:
        abs_path = str(Path(file_path).resolve())
        index = self._read_imports_index(batch_id)
        for entry in index:
            if entry.get("abs_path") == abs_path:
                return True
        return False

    def get_imported_files(self, batch_id: str) -> List[Dict]:
        return self._read_imports_index(batch_id)

    def mark_file_imported(self, batch_id: str, file_path: str, file_hash: str,
                           event_count: int, error_count: int) -> Dict:
        abs_path = str(Path(file_path).resolve())
        entry = {
            "abs_path": abs_path,
            "filename": os.path.basename(file_path),
            "file_hash": file_hash,
            "event_count": event_count,
            "error_count": error_count,
            "imported_at": datetime.now().isoformat(),
        }
        index = self._read_imports_index(batch_id)
        index.append(entry)
        self._write_imports_index(batch_id, index)
        return entry

    def undo_last_import(self, batch_id: str) -> Optional[Dict]:
        index = self._read_imports_index(batch_id)
        if not index:
            return None
        last = index.pop()
        self._write_imports_index(batch_id, index)
        return last

    def save_export(self, batch_id: str, export_type: str, content: str, filename: str) -> str:
        batch_dir = self._ensure_batch_dir(batch_id)
        exports_dir = batch_dir / "exports"
        exports_dir.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        name, ext = os.path.splitext(filename)
        export_filename = f"{name}_{ts}{ext}"
        export_path = exports_dir / export_filename
        with open(export_path, "w", encoding="utf-8") as f:
            f.write(content)
        return str(export_path)

    def get_exports(self, batch_id: str) -> List[Dict]:
        batch_dir = self._get_batch_dir(batch_id)
        exports_dir = batch_dir / "exports"
        result = []
        if exports_dir.exists():
            for f in exports_dir.iterdir():
                if f.is_file():
                    result.append({
                        "filename": f.name,
                        "path": str(f),
                        "size": f.stat().st_size,
                        "modified_at": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                    })
        result.sort(key=lambda x: x["modified_at"], reverse=True)
        return result
