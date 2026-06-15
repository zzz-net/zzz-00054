import json
import os
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import copy

from .models import Event, EventStatus, ParseError, Phase, LabelHistory
from .config import RuleConfig
import uuid


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

    def _label_history_path(self, batch_id: str) -> Path:
        return self._get_batch_dir(batch_id) / "label_history.json"

    def _overview_snapshot_path(self, batch_id: str) -> Path:
        return self._get_batch_dir(batch_id) / "overview_snapshot.json"

    def _undo_history_path(self, batch_id: str) -> Path:
        return self._get_batch_dir(batch_id) / "undo_history.json"

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
        self._write_label_history(batch_id, [])
        self._write_undo_history(batch_id, [])
        self._set_active_batch(batch_id)
        self.refresh_overview_snapshot(batch_id)
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
        try:
            self.refresh_overview_snapshot(batch_id)
        except Exception:
            pass

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
        try:
            self.refresh_overview_snapshot(batch_id)
        except Exception:
            pass

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

    def _read_label_history(self, batch_id: str) -> List[LabelHistory]:
        path = self._label_history_path(batch_id)
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return [LabelHistory.from_dict(d) for d in data]
        except (json.JSONDecodeError, IOError):
            return []

    def _write_label_history(self, batch_id: str, history: List[LabelHistory]) -> None:
        self._ensure_batch_dir(batch_id)
        data = [h.to_dict() for h in history]
        with open(self._label_history_path(batch_id), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _read_undo_history(self, batch_id: str) -> List[Dict]:
        path = self._undo_history_path(batch_id)
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []

    def _write_undo_history(self, batch_id: str, history: List[Dict]) -> None:
        self._ensure_batch_dir(batch_id)
        with open(self._undo_history_path(batch_id), "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)

    def _record_undo(self, batch_id: str, undo_type: str, detail: Dict) -> None:
        history = self._read_undo_history(batch_id)
        entry = {
            "id": str(uuid.uuid4())[:8],
            "undo_type": undo_type,
            "detail": detail,
            "created_at": datetime.now().isoformat(),
        }
        history.append(entry)
        self._write_undo_history(batch_id, history)

    def get_undo_history(self, batch_id: str) -> List[Dict]:
        return self._read_undo_history(batch_id)

    def _record_label_change(self, batch_id: str, event_id: str, operation: str,
                             old_status: Optional[EventStatus] = None,
                             new_status: Optional[EventStatus] = None,
                             old_notes: Optional[str] = None,
                             new_notes: Optional[str] = None) -> None:
        config = self.load_config(batch_id)
        history_entry = LabelHistory(
            id=str(uuid.uuid4())[:8],
            event_id=event_id,
            operation=operation,
            old_status=old_status,
            new_status=new_status,
            old_notes=old_notes,
            new_notes=new_notes,
            config_version=config.rule_version,
            created_at=datetime.now(),
        )
        history = self._read_label_history(batch_id)
        history.append(history_entry)
        self._write_label_history(batch_id, history)
        try:
            self.refresh_overview_snapshot(batch_id)
        except Exception:
            pass

    def get_label_history(self, batch_id: str) -> List[LabelHistory]:
        return self._read_label_history(batch_id)

    def set_event_status(self, batch_id: str, event_id: str, status: EventStatus) -> Optional[Event]:
        old_event = self.get_event_by_id(batch_id, event_id)
        old_status = old_event.status if old_event else None
        updated = self.update_event(batch_id, event_id, status=status)
        if updated and old_status != status:
            self._record_label_change(
                batch_id, event_id, "set_status",
                old_status=old_status, new_status=status
            )
        return updated

    def set_event_notes(self, batch_id: str, event_id: str, notes: str) -> Optional[Event]:
        old_event = self.get_event_by_id(batch_id, event_id)
        old_notes = old_event.notes if old_event else None
        updated = self.update_event(batch_id, event_id, notes=notes)
        if updated and old_notes != notes:
            self._record_label_change(
                batch_id, event_id, "set_notes",
                old_notes=old_notes, new_notes=notes
            )
        return updated

    def set_event_status_and_notes(self, batch_id: str, event_id: str,
                                    status: EventStatus, notes: str) -> Optional[Event]:
        old_event = self.get_event_by_id(batch_id, event_id)
        old_status = old_event.status if old_event else None
        old_notes = old_event.notes if old_event else None
        updated = self.update_event(batch_id, event_id, status=status, notes=notes)
        if updated:
            if old_status != status or old_notes != notes:
                self._record_label_change(
                    batch_id, event_id, "set_both",
                    old_status=old_status, new_status=status,
                    old_notes=old_notes, new_notes=notes
                )
        return updated

    def undo_last_label(self, batch_id: str) -> Optional[LabelHistory]:
        history = self._read_label_history(batch_id)
        if not history:
            return None
        last = history.pop()
        updates = {}
        if last.operation in ("set_status", "set_both") and last.old_status is not None:
            updates["status"] = last.old_status
        if last.operation in ("set_notes", "set_both") and last.old_notes is not None:
            updates["notes"] = last.old_notes
        if updates:
            self.update_event(batch_id, last.event_id, **updates)
        self._write_label_history(batch_id, history)
        try:
            op_desc = {
                "set_status": f"修改状态({last.old_status.value if last.old_status else '无'}→{last.new_status.value if last.new_status else '无'})",
                "set_notes": "修改备注",
                "set_both": "修改状态+备注",
            }.get(last.operation, last.operation)
            self._record_undo(batch_id, "undo_label", {
                "event_id": last.event_id,
                "event_id_short": last.event_id[:12] + "..." if len(last.event_id) > 12 else last.event_id,
                "operation_description": op_desc,
                "operation_raw": last.operation,
                "old_status": last.old_status.value if last.old_status else None,
                "new_status": last.new_status.value if last.new_status else None,
                "restored_status": last.old_status.value if last.old_status else None,
                "old_notes_preview": (last.old_notes[:40] + "...") if last.old_notes and len(last.old_notes) > 40 else (last.old_notes or ""),
                "new_notes_preview": (last.new_notes[:40] + "...") if last.new_notes and len(last.new_notes) > 40 else (last.new_notes or ""),
                "config_version": last.config_version,
                "original_acted_at": last.created_at.isoformat(),
            })
        except Exception:
            pass
        try:
            self.refresh_overview_snapshot(batch_id)
        except Exception:
            pass
        return last

    def save_parse_errors(self, batch_id: str, errors: List[ParseError]) -> None:
        self._ensure_batch_dir(batch_id)
        data = [e.to_dict() for e in errors]
        with open(self._parse_errors_path(batch_id), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        try:
            self.refresh_overview_snapshot(batch_id)
        except Exception:
            pass

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
        try:
            self.refresh_overview_snapshot(batch_id)
        except Exception:
            pass
        return entry

    def undo_last_import(self, batch_id: str) -> Optional[Dict]:
        index = self._read_imports_index(batch_id)
        if not index:
            return None
        last = index.pop()
        self._write_imports_index(batch_id, index)
        removed_filename = last.get("filename", "")
        removed_abs_path = last.get("abs_path", "")
        removed_event_count = 0
        removed_error_count = 0
        try:
            events = self.load_events(batch_id)
            kept_events = []
            for e in events:
                if e.source_file == removed_filename or e.source_file == removed_abs_path:
                    removed_event_count += 1
                else:
                    kept_events.append(e)
            if removed_event_count > 0 or len(kept_events) != len(events):
                data = [e.to_dict() for e in kept_events]
                self._ensure_batch_dir(batch_id)
                with open(self._events_path(batch_id), "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                self.update_batch_meta(batch_id, event_count=len(kept_events))
        except Exception:
            pass
        try:
            errors = self.load_parse_errors(batch_id)
            kept_errors = []
            for err in errors:
                if err.source_file == removed_filename or err.source_file == removed_abs_path:
                    removed_error_count += 1
                else:
                    kept_errors.append(err)
            if removed_error_count > 0 or len(kept_errors) != len(errors):
                data = [e.to_dict() for e in kept_errors]
                self._ensure_batch_dir(batch_id)
                with open(self._parse_errors_path(batch_id), "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        try:
            source_type = self._infer_source_type(removed_filename)
            self._record_undo(batch_id, "undo_import", {
                "filename": removed_filename,
                "abs_path": removed_abs_path,
                "source_type": source_type,
                "file_hash": last.get("file_hash", "")[:16] if last.get("file_hash") else "",
                "imported_at": last.get("imported_at", ""),
                "imported_event_count": last.get("event_count", 0),
                "imported_error_count": last.get("error_count", 0),
                "removed_event_count": removed_event_count,
                "removed_error_count": removed_error_count,
            })
        except Exception:
            pass
        try:
            self.refresh_overview_snapshot(batch_id)
        except Exception:
            pass
        return last

    def _infer_source_type(self, filename: str) -> str:
        ext = Path(filename).suffix.lower()
        if ext == ".csv":
            return "告警(CSV)"
        elif ext == ".json":
            return "备注(JSON)"
        else:
            return "日志(LOG)"

    def refresh_overview_snapshot(self, batch_id: str) -> Dict:
        snapshot = {
            "snapshot_version": 1,
            "updated_at": datetime.now().isoformat(),
        }
        try:
            meta = self.get_batch_meta(batch_id)
            snapshot["batch_id"] = meta.get("id", batch_id)
            snapshot["batch_name"] = meta.get("name", "未知批次")
            snapshot["description"] = meta.get("description", "")
            snapshot["created_at"] = meta.get("created_at", "")
            snapshot["batch_updated_at"] = meta.get("updated_at", "")
        except (BatchNotFoundError, json.JSONDecodeError, IOError) as e:
            snapshot["batch_id"] = batch_id
            snapshot["batch_name"] = "批次元数据缺失"
            snapshot["description"] = f"警告: 无法读取批次元数据 ({e})"
            snapshot["created_at"] = ""
            snapshot["batch_updated_at"] = ""
            snapshot["_meta_error"] = str(e)

        try:
            events = self.load_events(batch_id)
            snapshot["event_count"] = len(events)
            by_status: Dict[str, int] = {}
            by_severity: Dict[str, int] = {}
            by_source: Dict[str, int] = {}
            for e in events:
                s = e.status.value
                by_status[s] = by_status.get(s, 0) + 1
                sev = e.severity.value
                by_severity[sev] = by_severity.get(sev, 0) + 1
                src = e.source.value
                by_source[src] = by_source.get(src, 0) + 1
            snapshot["events_by_status"] = by_status
            snapshot["events_by_severity"] = by_severity
            snapshot["events_by_source"] = by_source
            if events:
                sorted_events = sorted(events, key=lambda e: e.timestamp)
                snapshot["time_range_start"] = sorted_events[0].timestamp.isoformat()
                snapshot["time_range_end"] = sorted_events[-1].timestamp.isoformat()
            else:
                snapshot["time_range_start"] = None
                snapshot["time_range_end"] = None
        except Exception as e:
            snapshot["event_count"] = 0
            snapshot["events_by_status"] = {}
            snapshot["events_by_severity"] = {}
            snapshot["events_by_source"] = {}
            snapshot["time_range_start"] = None
            snapshot["time_range_end"] = None
            snapshot["_events_error"] = str(e)

        try:
            errors = self.load_parse_errors(batch_id)
            snapshot["parse_error_count"] = len(errors)
            error_by_file: Dict[str, int] = {}
            for err in errors:
                error_by_file[err.source_file] = error_by_file.get(err.source_file, 0) + 1
            snapshot["parse_errors_by_file"] = error_by_file
        except Exception as e:
            snapshot["parse_error_count"] = 0
            snapshot["parse_errors_by_file"] = {}
            snapshot["_parse_errors_error"] = str(e)

        try:
            imported = self.get_imported_files(batch_id)
            imported_files = []
            for f in imported:
                imported_files.append({
                    "filename": f.get("filename", "未知文件"),
                    "abs_path": f.get("abs_path", ""),
                    "source_type": self._infer_source_type(f.get("filename", "")),
                    "event_count": f.get("event_count", 0),
                    "error_count": f.get("error_count", 0),
                    "file_hash": f.get("file_hash", "")[:16] if f.get("file_hash") else "",
                    "imported_at": f.get("imported_at", ""),
                })
            snapshot["imported_files"] = imported_files
            snapshot["imported_file_count"] = len(imported_files)
        except Exception as e:
            snapshot["imported_files"] = []
            snapshot["imported_file_count"] = 0
            snapshot["_imports_error"] = str(e)

        try:
            config = self.load_config(batch_id)
            snapshot["rule_version"] = config.rule_version
            snapshot["dedup_window_seconds"] = config.dedup_window_seconds
            snapshot["gap_threshold_seconds"] = config.gap_threshold_seconds
            snapshot["dedup_similarity_threshold"] = config.dedup_similarity_threshold
            snapshot["phase_count"] = len(config.phases)
        except Exception as e:
            snapshot["rule_version"] = "未知"
            snapshot["dedup_window_seconds"] = 0
            snapshot["gap_threshold_seconds"] = 0
            snapshot["dedup_similarity_threshold"] = 0.0
            snapshot["phase_count"] = 0
            snapshot["_config_error"] = str(e)

        try:
            exports = self.get_exports(batch_id)
            snapshot["export_count"] = len(exports)
            if exports:
                last = exports[0]
                snapshot["last_export"] = {
                    "filename": last.get("filename", ""),
                    "path": last.get("path", ""),
                    "size": last.get("size", 0),
                    "exported_at": last.get("modified_at", ""),
                }
            else:
                snapshot["last_export"] = None
        except Exception as e:
            snapshot["export_count"] = 0
            snapshot["last_export"] = None
            snapshot["_exports_error"] = str(e)

        try:
            history = self.get_label_history(batch_id)
            snapshot["label_action_count"] = len(history)
            if history:
                last = history[-1]
                op_desc = {
                    "set_status": "修改状态",
                    "set_notes": "修改备注",
                    "set_both": "修改状态+备注",
                }.get(last.operation, last.operation)
                snapshot["last_label_action"] = {
                    "operation": op_desc,
                    "operation_raw": last.operation,
                    "event_id": last.event_id,
                    "event_id_short": last.event_id[:12] + "..." if len(last.event_id) > 12 else last.event_id,
                    "old_status": last.old_status.value if last.old_status else None,
                    "new_status": last.new_status.value if last.new_status else None,
                    "old_notes_preview": (last.old_notes[:40] + "...") if last.old_notes and len(last.old_notes) > 40 else (last.old_notes or ""),
                    "new_notes_preview": (last.new_notes[:40] + "...") if last.new_notes and len(last.new_notes) > 40 else (last.new_notes or ""),
                    "config_version": last.config_version,
                    "acted_at": last.created_at.isoformat(),
                }
            else:
                snapshot["last_label_action"] = None
        except Exception as e:
            snapshot["label_action_count"] = 0
            snapshot["last_label_action"] = None
            snapshot["_label_history_error"] = str(e)

        try:
            undo_history = self.get_undo_history(batch_id)
            snapshot["undo_action_count"] = len(undo_history)
            if undo_history:
                last_undo = undo_history[-1]
                undo_type = last_undo.get("undo_type", "")
                detail = last_undo.get("detail", {})
                if undo_type == "undo_label":
                    snapshot["last_undo_action"] = {
                        "undo_type": "undo_label",
                        "undo_type_desc": "撤销标注",
                        **detail,
                        "acted_at": last_undo.get("created_at", ""),
                    }
                elif undo_type == "undo_import":
                    snapshot["last_undo_action"] = {
                        "undo_type": "undo_import",
                        "undo_type_desc": "撤销导入",
                        **detail,
                        "acted_at": last_undo.get("created_at", ""),
                    }
                else:
                    snapshot["last_undo_action"] = {
                        "undo_type": undo_type,
                        "undo_type_desc": f"撤销({undo_type})",
                        **detail,
                        "acted_at": last_undo.get("created_at", ""),
                    }
            else:
                snapshot["last_undo_action"] = None
        except Exception as e:
            snapshot["undo_action_count"] = 0
            snapshot["last_undo_action"] = None
            snapshot["_undo_history_error"] = str(e)

        label_time = ""
        if snapshot.get("last_label_action"):
            label_time = snapshot["last_label_action"].get("acted_at", "")
        undo_time = ""
        if snapshot.get("last_undo_action"):
            undo_time = snapshot["last_undo_action"].get("acted_at", "")
        if label_time and undo_time:
            if undo_time >= label_time:
                snapshot["latest_action_kind"] = "undo"
                snapshot["latest_action"] = snapshot["last_undo_action"]
            else:
                snapshot["latest_action_kind"] = "label"
                snapshot["latest_action"] = snapshot["last_label_action"]
        elif label_time:
            snapshot["latest_action_kind"] = "label"
            snapshot["latest_action"] = snapshot["last_label_action"]
        elif undo_time:
            snapshot["latest_action_kind"] = "undo"
            snapshot["latest_action"] = snapshot["last_undo_action"]
        else:
            snapshot["latest_action_kind"] = None
            snapshot["latest_action"] = None

        try:
            self._ensure_batch_dir(batch_id)
            with open(self._overview_snapshot_path(batch_id), "w", encoding="utf-8") as f:
                json.dump(snapshot, f, ensure_ascii=False, indent=2)
        except Exception as e:
            snapshot["_write_error"] = str(e)

        return snapshot

    def load_overview_snapshot(self, batch_id: str, auto_refresh: bool = True) -> Dict:
        path = self._overview_snapshot_path(batch_id)
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        if auto_refresh:
            return self.refresh_overview_snapshot(batch_id)
        return {}

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
        try:
            self.refresh_overview_snapshot(batch_id)
        except Exception:
            pass
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
