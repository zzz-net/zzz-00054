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

    def _import_index_map_path(self, batch_id: str) -> Path:
        return self._get_batch_dir(batch_id) / "import_index_map.json"

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
        self._write_import_index_map(batch_id, {})
        self._set_active_batch(batch_id)
        self.refresh_overview_snapshot(batch_id, trigger="create")
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
            self.refresh_overview_snapshot(batch_id, trigger="config")
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
            self.refresh_overview_snapshot(batch_id, trigger="import")
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

    def _read_import_index_map(self, batch_id: str) -> Dict:
        path = self._import_index_map_path(batch_id)
        if not path.exists():
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}

    def _write_import_index_map(self, batch_id: str, mapping: Dict) -> None:
        self._ensure_batch_dir(batch_id)
        with open(self._import_index_map_path(batch_id), "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False, indent=2)

    def _refresh_import_index_map(self, batch_id: str, index: List[Dict] = None) -> Dict:
        if index is None:
            index = self._read_imports_index(batch_id)
        mapping = {}
        needs_write_back = False
        used_indices = set()
        for entry in index:
            didx = entry.get("display_index", 0)
            if didx and didx not in used_indices:
                mapping[str(didx)] = entry.get("import_id", "")
                used_indices.add(didx)
            else:
                needs_write_back = True
        if needs_write_back:
            next_idx = 1
            for entry in index:
                didx = entry.get("display_index", 0)
                if not didx or didx in used_indices:
                    while next_idx in used_indices:
                        next_idx += 1
                    entry["display_index"] = next_idx
                    mapping[str(next_idx)] = entry.get("import_id", "")
                    used_indices.add(next_idx)
                    next_idx += 1
            self._write_imports_index(batch_id, index)
        self._write_import_index_map(batch_id, mapping)
        return mapping

    def get_all_imports_with_index(self, batch_id: str) -> List[Dict]:
        all_entries = self._read_imports_index(batch_id)
        self._refresh_import_index_map(batch_id, all_entries)
        events = self.load_events(batch_id)
        event_map = {e.id: e for e in events}
        for entry in all_entries:
            bound_ids = set(entry.get("event_ids", []))
            import_id = entry.get("import_id", "")
            matched = 0
            for eid in bound_ids:
                if eid in event_map and (import_id in event_map[eid].import_ids or len(event_map[eid].import_ids) == 0):
                    matched += 1
            for e in events:
                if import_id in e.import_ids and e.id not in bound_ids:
                    matched += 1
            entry["matched_event_count"] = matched
        self._write_imports_index(batch_id, all_entries)
        self.log_change(batch_id, "import_index_map_refreshed", {
            "total_entries": len(all_entries),
            "mapped_indices": len([e for e in all_entries if e.get("display_index")]),
        }, severity="debug")
        return all_entries

    def resolve_import_by_display_index(self, batch_id: str, display_index: int) -> Optional[Dict]:
        all_entries = self.get_all_imports_with_index(batch_id)
        for entry in all_entries:
            if entry.get("display_index") == display_index:
                return entry
        old_map = self._read_import_index_map(batch_id)
        import_id = old_map.get(str(display_index))
        if import_id:
            for entry in all_entries:
                if entry.get("import_id") == import_id:
                    return entry
        return None

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
            self.refresh_overview_snapshot(batch_id, trigger="label")
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
            self.refresh_overview_snapshot(batch_id, trigger="undo_label")
        except Exception:
            pass
        return last

    def save_parse_errors(self, batch_id: str, errors: List[ParseError]) -> None:
        self._ensure_batch_dir(batch_id)
        data = [e.to_dict() for e in errors]
        with open(self._parse_errors_path(batch_id), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        try:
            self.refresh_overview_snapshot(batch_id, trigger="import")
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

    def get_imported_files(self, batch_id: str, include_undone: bool = False) -> List[Dict]:
        all_entries = self._read_imports_index(batch_id)
        if include_undone:
            return all_entries
        return [e for e in all_entries if e.get("status") == "active"]

    def get_active_imports(self, batch_id: str) -> List[Dict]:
        return self.get_imported_files(batch_id, include_undone=False)

    def get_undone_imports(self, batch_id: str) -> List[Dict]:
        all_entries = self._read_imports_index(batch_id)
        return [e for e in all_entries if e.get("status") == "undone"]

    def get_round_imports(self, batch_id: str, round_number: int) -> List[Dict]:
        all_entries = self._read_imports_index(batch_id)
        return [e for e in all_entries if e.get("round_number") == round_number]

    def rebuild_state_after_restart(self, batch_id: str) -> Dict:
        result = {
            "rebuilt": False,
            "actions": [],
            "errors": [],
        }
        try:
            consistency = self.check_snapshot_consistency(batch_id)
            if not consistency.get("consistent"):
                result["actions"].append("检测到不一致，自动刷新快照")
                self.refresh_overview_snapshot(batch_id, trigger="auto_refresh")
                consistency2 = self.check_snapshot_consistency(batch_id)
                if consistency2.get("consistent"):
                    result["actions"].append("快照刷新后已一致")
                    result["rebuilt"] = True
                else:
                    result["errors"].append("快照刷新后仍不一致")
            else:
                result["rebuilt"] = True
                result["actions"].append("重启后状态一致，无需重建")

            index = self._read_imports_index(batch_id)
            repaired = 0
            for entry in index:
                needs_repair = False
                if "status" not in entry:
                    entry["status"] = "active"
                    needs_repair = True
                entry.setdefault("import_id", self._generate_import_id())
                entry.setdefault("round_number", 0)
                entry.setdefault("event_ids", [])
                entry.setdefault("parse_error_refs", [])
                entry.setdefault("undone_at", None)
                entry.setdefault("last_restored_at", None)
                entry.setdefault("restored_count", 0)
                entry.setdefault("display_index", 0)
                if "matched_event_count" not in entry:
                    entry["matched_event_count"] = entry.get("event_count", 0)
                    needs_repair = True
                if "last_operation" not in entry:
                    if entry.get("status") == "undone":
                        entry["last_operation"] = "undo"
                    elif entry.get("restored_count", 0) > 0:
                        entry["last_operation"] = "restore"
                    else:
                        entry["last_operation"] = "import"
                    needs_repair = True
                if "last_operation_at" not in entry:
                    entry["last_operation_at"] = (
                        entry.get("last_restored_at")
                        or entry.get("undone_at")
                        or entry.get("imported_at", "")
                    )
                    needs_repair = True
                if "last_operation_result" not in entry:
                    entry["last_operation_result"] = "success"
                    needs_repair = True
                if "last_operation_detail" not in entry:
                    entry["last_operation_detail"] = {
                        "action": entry["last_operation"],
                        "event_count": entry.get("event_count", 0),
                        "round_number": entry.get("round_number", 0),
                    }
                    needs_repair = True
                if needs_repair:
                    repaired += 1
            if repaired:
                self._write_imports_index(batch_id, index)
                result["actions"].append(f"升级了 {repaired} 条导入记录的结构")
                result["rebuilt"] = True

            try:
                mapping = self._refresh_import_index_map(batch_id, index)
                result["actions"].append(f"已重建导入显示索引映射({len(mapping)}条)")
                self.get_all_imports_with_index(batch_id)
            except Exception as e:
                result["errors"].append(f"重建导入索引映射失败: {e}")

            self.log_change(batch_id, "state_rebuilt_after_restart", result, severity="info")
        except Exception as e:
            result["errors"].append(str(e))
            self.log_change(batch_id, "state_rebuild_failed", {"error": str(e)}, severity="error")
        return result

    def verify_export_consistency(self, batch_id: str, export_content: str, export_format: str = "markdown") -> Dict:
        result = {
            "consistent": True,
            "checks": [],
            "mismatches": [],
            "audit_rules_applied": [],
            "failed_checks": [],
        }
        try:
            config = self.load_config(batch_id)
            audit_rules = config.audit_rules
            result["audit_rules_enabled"] = audit_rules.enabled

            if not audit_rules.enabled:
                result["checks"].append("核对规则已禁用，跳过校验")
                result["consistent"] = True
                return result

            real_events = self.load_events(batch_id)
            real_count = len(real_events)
            valid_events = [e for e in real_events if e.status.value != "噪声"]
            valid_count = len(valid_events)
            result["checks"].append(f"实际事件总数: {real_count}")
            result["checks"].append(f"库内有效事件数(非噪声): {valid_count}")
            result["actual_total_count"] = real_count
            result["actual_valid_count"] = valid_count

            count_in_export = 0
            if export_format == "markdown":
                import re
                patterns = audit_rules.export_count_patterns
                for line in export_content.splitlines():
                    found = False
                    for pattern in patterns:
                        pattern_clean = pattern.replace(":", "")
                        if pattern_clean in line:
                            match = re.search(r'(\d+)', line)
                            if match:
                                n = int(match.group(1))
                                if n >= 0 and n < 1000000:
                                    count_in_export = n
                                    found = True
                                    break
                    if found:
                        break
            elif export_format == "csv":
                csv_lines = [l for l in export_content.splitlines() if l.strip()]
                if csv_lines:
                    count_in_export = max(0, len(csv_lines) - 1)

            result["checks"].append(f"导出文件中事件数: {count_in_export}")
            result["export_count"] = count_in_export

            if audit_rules.is_check_enabled("empty_export"):
                result["audit_rules_applied"].append("empty_export")
                tolerance = audit_rules.get_tolerance("empty_export")
                if count_in_export <= tolerance and valid_count > tolerance:
                    result["consistent"] = False
                    mismatch = {
                        "field": "event_count",
                        "check_type": "empty_export",
                        "export": count_in_export,
                        "actual": valid_count,
                        "diff": valid_count - count_in_export,
                        "reason": f"空导出校验失败: 导出事件数为{count_in_export}(容忍度:{tolerance})，但库内有效事件数为{valid_count}",
                        "severity": "error",
                    }
                    result["mismatches"].append(mismatch)
                    result["failed_checks"].append("empty_export")
                    if audit_rules.log_to_change_log:
                        self.log_change(batch_id, "audit_empty_export_failed", mismatch,
                                      severity="error" if audit_rules.log_level == "info" else audit_rules.log_level)

            if audit_rules.is_check_enabled("event_count_mismatch") and result["consistent"]:
                result["audit_rules_applied"].append("event_count_mismatch")
                tolerance = audit_rules.get_tolerance("event_count_mismatch")
                diff = abs(valid_count - count_in_export)
                if count_in_export > 0 and diff > tolerance:
                    result["consistent"] = False
                    mismatch = {
                        "field": "event_count",
                        "check_type": "event_count_mismatch",
                        "export": count_in_export,
                        "actual": valid_count,
                        "diff": valid_count - count_in_export,
                        "abs_diff": diff,
                        "tolerance": tolerance,
                        "reason": f"数量不一致校验失败: 导出{count_in_export}条，实际{valid_count}条，差异{diff}条(容忍度:{tolerance})",
                        "severity": "error",
                    }
                    result["mismatches"].append(mismatch)
                    result["failed_checks"].append("event_count_mismatch")
                    if audit_rules.log_to_change_log:
                        self.log_change(batch_id, "audit_count_mismatch_failed", mismatch,
                                      severity="error" if audit_rules.log_level == "info" else audit_rules.log_level)

            consistency = self.check_snapshot_consistency(batch_id)
            if not consistency.get("consistent"):
                if audit_rules.auto_fix_snapshot:
                    self.log_change(batch_id, "audit_auto_fixing_snapshot", consistency, severity="warning")
                    fix_result = self.fix_snapshot_inconsistencies(batch_id)
                    result["checks"].append(f"自动修复快照: {fix_result.get('fixed', False)}")
                    consistency = self.check_snapshot_consistency(batch_id)

                if not consistency.get("consistent"):
                    result["consistent"] = False
                    mismatch = {
                        "type": "snapshot_inconsistent",
                        "check_type": "snapshot_integrity",
                        "details": consistency.get("inconsistencies", []),
                        "reason": "快照与真实数据不一致",
                        "severity": "warning",
                    }
                    result["mismatches"].append(mismatch)
                    result["failed_checks"].append("snapshot_integrity")
                    if audit_rules.log_to_change_log:
                        self.log_change(batch_id, "audit_snapshot_inconsistent", mismatch, severity="warning")

            if result["consistent"]:
                result["checks"].append("✅ 所有核对规则通过")
                if audit_rules.log_to_change_log:
                    self.log_change(batch_id, "export_consistency_verified", {
                        "result": "passed",
                        "export_count": count_in_export,
                        "actual_valid_count": valid_count,
                        "rules_applied": result["audit_rules_applied"],
                    }, severity="info")
            else:
                result["checks"].append(f"❌ {len(result['failed_checks'])} 项核对规则未通过")
                if audit_rules.log_to_change_log:
                    self.log_change(batch_id, "export_consistency_failed", {
                        "result": "failed",
                        "export_count": count_in_export,
                        "actual_valid_count": valid_count,
                        "failed_checks": result["failed_checks"],
                        "mismatches": result["mismatches"],
                    }, severity="error")
        except Exception as e:
            result["consistent"] = False
            result["checks"].append(f"校验异常: {e}")
            result["failed_checks"].append("exception")
            import traceback
            self.log_change(batch_id, "audit_verification_exception", {
                "error": str(e),
                "traceback": traceback.format_exc(),
            }, severity="error")
        return result

    def check_duplicate_restore(self, batch_id: str, import_id: str) -> Dict:
        result = {
            "has_conflict": False,
            "conflict_type": None,
            "message": "",
            "details": {},
        }
        try:
            config = self.load_config(batch_id)
            audit_rules = config.audit_rules

            if not audit_rules.check_duplicate_restore:
                return result

            index = self._read_imports_index(batch_id)
            target_entry = None
            for entry in index:
                if entry.get("import_id") == import_id:
                    target_entry = entry
                    break

            if not target_entry:
                result["message"] = "导入记录不存在"
                return result

            if target_entry.get("status") == "active":
                result["has_conflict"] = True
                result["conflict_type"] = "already_active"
                result["message"] = "该导入已处于激活状态，无需重复恢复"
                result["details"] = {
                    "import_id": import_id,
                    "filename": target_entry.get("filename"),
                    "current_status": target_entry.get("status"),
                    "restored_count": target_entry.get("restored_count", 0),
                }
                if audit_rules.log_to_change_log:
                    self.log_change(batch_id, "audit_duplicate_restore_detected", result["details"], severity="error")

            abs_path = target_entry.get("abs_path")
            file_hash = target_entry.get("file_hash")
            for entry in index:
                if entry.get("import_id") != import_id and entry.get("status") == "active":
                    if entry.get("abs_path") == abs_path or entry.get("file_hash") == file_hash:
                        result["has_conflict"] = True
                        result["conflict_type"] = "duplicate_file_active"
                        result["message"] = "存在另一个相同文件的激活导入记录，恢复会导致重复数据"
                        result["details"] = {
                            "import_id": import_id,
                            "filename": target_entry.get("filename"),
                            "conflicting_import_id": entry.get("import_id"),
                            "conflicting_imported_at": entry.get("imported_at"),
                        }
                        if audit_rules.log_to_change_log:
                            self.log_change(batch_id, "audit_restore_conflict_detected", result["details"], severity="error")
                        break

        except Exception as e:
            result["has_conflict"] = True
            result["conflict_type"] = "error"
            result["message"] = f"冲突检查异常: {e}"
            self.log_change(batch_id, "audit_conflict_check_error", {"error": str(e)}, severity="error")

        return result

    def get_audit_operations_list(self, batch_id: str, limit: int = 20) -> List[Dict]:
        all_entries = self.get_all_imports_with_index(batch_id)
        operations = []

        for entry in all_entries:
            import_id = entry.get("import_id", "")
            status = entry.get("status", "unknown")
            last_op = entry.get("last_operation", "")
            last_op_at = entry.get("last_operation_at", "")
            last_processed_at = (
                entry.get("last_restored_at")
                or entry.get("undone_at")
                or entry.get("imported_at", "")
                or last_op_at
            )

            operations.append({
                "display_index": entry.get("display_index", 0),
                "import_id": import_id,
                "filename": entry.get("filename", ""),
                "abs_path": entry.get("abs_path", ""),
                "file_hash": entry.get("file_hash", "")[:16] if entry.get("file_hash") else "",
                "round_number": entry.get("round_number", 0),
                "status": status,
                "event_count": entry.get("event_count", 0),
                "matched_event_count": entry.get("matched_event_count", 0),
                "error_count": entry.get("error_count", 0),
                "imported_at": entry.get("imported_at", ""),
                "undone_at": entry.get("undone_at", ""),
                "last_restored_at": entry.get("last_restored_at", ""),
                "restored_count": entry.get("restored_count", 0),
                "last_processed_at": last_processed_at,
                "operation_type": self._get_operation_type(status, entry),
                "last_operation": last_op,
                "last_operation_at": last_op_at,
                "last_operation_result": entry.get("last_operation_result", ""),
            })

        operations.sort(key=lambda x: x.get("last_processed_at", ""), reverse=True)
        return operations[:limit]

    def _get_operation_type(self, status: str, entry: Dict) -> str:
        last_op = entry.get("last_operation", "")
        if last_op == "import":
            return "已导入"
        elif last_op == "undo":
            if entry.get("restored_count", 0) > 0:
                return "已撤销(曾恢复)"
            return "已撤销"
        elif last_op == "restore":
            return "已恢复"
        if status == "undone":
            if entry.get("restored_count", 0) > 0:
                return "已撤销(曾恢复)"
            return "已撤销"
        elif status == "active":
            if entry.get("restored_count", 0) > 0:
                return "已恢复"
            return "已导入"
        return "未知"

    def get_audit_operation_detail(self, batch_id: str, display_index: int) -> Optional[Dict]:
        resolved = self.resolve_import_by_display_index(batch_id, display_index)
        if not resolved:
            return None

        import_id = resolved.get("import_id")
        detail = self.get_import_detail(batch_id, import_id=import_id)
        if not detail:
            return None

        events = self.load_events(batch_id)
        bound_ids = set(detail.get("event_ids", []))
        event_stats = {
            "total": 0,
            "by_status": {},
            "by_severity": {},
        }

        for e in events:
            if e.id in bound_ids or import_id in e.import_ids:
                event_stats["total"] += 1
                status_val = e.status.value
                event_stats["by_status"][status_val] = event_stats["by_status"].get(status_val, 0) + 1
                sev_val = e.severity.value
                event_stats["by_severity"][sev_val] = event_stats["by_severity"].get(sev_val, 0) + 1

        change_log = self.get_change_log(batch_id, limit=50)
        related_logs = []
        for log in change_log:
            log_detail = log.get("detail", {})
            if (log_detail.get("import_id") == import_id
                    or log_detail.get("filename") == detail.get("filename")
                    or log_detail.get("display_index") == display_index):
                related_logs.append(log)

        last_op = resolved.get("last_operation", "")
        last_op_at = resolved.get("last_operation_at", "")
        last_op_result = resolved.get("last_operation_result", "")
        last_op_detail = resolved.get("last_operation_detail", {})

        action_display_map = {
            "import": "导入",
            "undo": "撤销",
            "restore": "恢复",
        }

        last_processed_result = None
        if last_op:
            last_processed_result = {
                "action": last_op,
                "action_display": action_display_map.get(last_op, last_op),
                "result": last_op_result if last_op_result else "success",
                "timestamp": last_op_at,
                "details": last_op_detail,
            }
        elif related_logs:
            last_log = related_logs[0]
            action_type_map = {
                "import_registered": "导入",
                "import_attached_to_events": "导入",
                "undo_import_completed": "撤销",
                "restore_import_completed": "恢复",
                "audit_undo_import_success": "撤销",
                "audit_restore_import_success": "恢复",
                "audit_undo_invalid_status": "撤销(失败)",
                "audit_restore_invalid_status": "恢复(失败)",
                "audit_restore_conflict_blocked": "恢复(冲突)",
                "audit_undo_conflict_warning": "撤销(警告)",
                "audit_export_with_audit_success": "导出核对",
                "audit_export_with_audit_failed": "导出核对(失败)",
            }
            raw_action = last_log.get("change_type", "")
            action_display = action_type_map.get(raw_action, raw_action)
            last_processed_result = {
                "action": raw_action,
                "action_display": action_display,
                "result": "success" if last_log.get("severity") != "error" else "failed",
                "timestamp": last_log.get("created_at", ""),
                "details": last_log.get("detail", {}),
            }

        operation_type = self._get_operation_type(
            detail.get("status", "unknown"),
            resolved
        )

        return {
            **detail,
            "display_index": display_index,
            "operation_type": operation_type,
            "event_stats": event_stats,
            "source_file": detail.get("abs_path", ""),
            "last_processed_result": last_processed_result,
            "recent_related_logs": related_logs,
        }

    def get_import_detail(self, batch_id: str, import_id: str = None,
                           round_number: int = None) -> Optional[Dict]:
        entry = self._find_import_entry(batch_id, import_id=import_id, round_number=round_number)
        if not entry:
            return None
        events = self.load_events(batch_id)
        bound_ids = set(entry.get("event_ids", []))
        matched_events = []
        for e in events:
            if e.id in bound_ids or entry.get("import_id") in e.import_ids:
                matched_events.append({
                    "id": e.id[:16] + "..." if len(e.id) > 16 else e.id,
                    "timestamp": e.timestamp.isoformat(),
                    "source": e.source.value,
                    "severity": e.severity.value,
                    "status": e.status.value,
                    "message": e.message[:80],
                    "active_in_event": entry.get("import_id") in e.import_ids,
                })
        return {
            **entry,
            "matched_event_count": len(matched_events),
            "matched_events_sample": matched_events[:10],
        }

    def get_config_conflict_reasons(self, batch_id: str, new_config: RuleConfig) -> Dict:
        base = self.check_config_conflict(batch_id, new_config)
        result = {
            "has_conflict": base.get("has_conflict", False),
            "conflicts": [],
            "recommendations": [],
        }
        for c in base.get("conflicts", []):
            field = c.get("field", "")
            if field == "dedup_window_seconds":
                result["recommendations"].append(
                    "去重窗口变更会影响事件合并结果，建议重新导入或刷新快照确认"
                )
            if field == "gap_threshold_seconds":
                result["recommendations"].append(
                    "缺口阈值变更会影响时间缺口识别，建议重新生成时间线"
                )
            if field == "rule_version":
                result["recommendations"].append(
                    "规则版本不一致，建议 bump-version 明确记录变更"
                )
            result["conflicts"].append({
                **c,
                "reason": {
                    "dedup_window_seconds": "去重窗口直接影响事件合并数量",
                    "gap_threshold_seconds": "缺口阈值直接影响时间缺口判定",
                    "dedup_similarity_threshold": "相似度阈值影响去重判定",
                    "rule_version": "规则版本号用于追溯配置变更",
                }.get(field, "配置字段已修改"),
            })
        return result

    def _generate_import_id(self) -> str:
        return f"imp_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:6]}"

    def mark_file_imported(self, batch_id: str, file_path: str, file_hash: str,
                           event_count: int, error_count: int,
                           event_ids: List[str] = None,
                           parse_error_refs: List[Tuple[str, int]] = None,
                           round_number: int = None) -> Dict:
        abs_path = str(Path(file_path).resolve())
        import_id = self._generate_import_id()
        if round_number is None:
            round_number = self._get_next_round_number(batch_id) - 1
            if round_number < 1:
                round_number = 1
        now_iso = datetime.now().isoformat()
        entry = {
            "import_id": import_id,
            "abs_path": abs_path,
            "filename": os.path.basename(file_path),
            "file_hash": file_hash,
            "event_count": event_count,
            "error_count": error_count,
            "event_ids": list(event_ids) if event_ids else [],
            "parse_error_refs": list(parse_error_refs) if parse_error_refs else [],
            "imported_at": now_iso,
            "round_number": round_number,
            "status": "active",
            "undone_at": None,
            "last_restored_at": None,
            "restored_count": 0,
            "display_index": 0,
            "matched_event_count": event_count,
            "last_operation": "import",
            "last_operation_at": now_iso,
            "last_operation_result": "success",
            "last_operation_detail": {
                "action": "import",
                "event_count": event_count,
                "error_count": error_count,
                "round_number": round_number,
            },
        }
        index = self._read_imports_index(batch_id)
        next_disp_idx = 1
        for existing in index:
            if existing.get("display_index", 0) >= next_disp_idx:
                next_disp_idx = existing["display_index"] + 1
        entry["display_index"] = next_disp_idx
        index.append(entry)
        self._write_imports_index(batch_id, index)
        self._refresh_import_index_map(batch_id, index)
        self.log_change(batch_id, "import_registered", {
            "import_id": import_id,
            "filename": entry["filename"],
            "event_count": event_count,
            "round_number": round_number,
            "display_index": next_disp_idx,
        }, severity="info")
        try:
            self.refresh_overview_snapshot(batch_id, trigger="import")
        except Exception:
            pass
        return entry

    def _attach_import_id_to_events(self, batch_id: str, import_id: str, event_ids: List[str],
                                   round_number: int) -> int:
        if not event_ids:
            return 0
        events = self.load_events(batch_id)
        id_set = set(event_ids)
        modified = 0
        for e in events:
            if e.id in id_set:
                if import_id not in e.import_ids:
                    e.import_ids.append(import_id)
                if round_number not in e.import_rounds:
                    e.import_rounds.append(round_number)
                modified += 1
        if modified:
            data = [e.to_dict() for e in events]
            self._ensure_batch_dir(batch_id)
            with open(self._events_path(batch_id), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        self.log_change(batch_id, "import_attached_to_events", {
            "import_id": import_id,
            "attached_count": modified,
            "round_number": round_number,
        }, severity="debug")
        return modified

    def _attach_import_id_to_errors(self, batch_id: str, import_id: str,
                                     error_refs: List[Tuple[str, int]]) -> int:
        if not error_refs:
            return 0
        errors = self.load_parse_errors(batch_id)
        ref_set = set((f, ln) for f, ln in error_refs)
        modified = 0
        for err in errors:
            if (err.source_file, err.line_number) in ref_set:
                if not err.import_id:
                    err.import_id = import_id
                    modified += 1
        if modified:
            data = [e.to_dict() for e in errors]
            self._ensure_batch_dir(batch_id)
            with open(self._parse_errors_path(batch_id), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        return modified

    def _update_imports_index_entry(self, batch_id: str, import_id: str, **updates) -> Optional[Dict]:
        index = self._read_imports_index(batch_id)
        for i, entry in enumerate(index):
            if entry.get("import_id") == import_id:
                index[i].update(updates)
                self._write_imports_index(batch_id, index)
                return index[i]
        return None

    def _find_import_entry(self, batch_id: str, import_id: str = None,
                             round_number: int = None,
                             status: str = None) -> Optional[Dict]:
        index = self._read_imports_index(batch_id)
        if import_id:
            for entry in reversed(index):
                if entry.get("import_id") == import_id:
                    if status is None or entry.get("status") == status:
                        return entry
        if round_number is not None:
            for entry in reversed(index):
                if entry.get("round_number") == round_number:
                    if status is None or entry.get("status") == status:
                        return entry
        return None

    def undo_last_import(self, batch_id: str, import_id: str = None,
                          round_number: int = None) -> Optional[Dict]:
        index = self._read_imports_index(batch_id)
        if not index:
            return None

        target_entry = None
        target_idx = -1
        if import_id or round_number is not None:
            for i, entry in enumerate(index):
                if (import_id and entry.get("import_id") == import_id) or \
                   (round_number is not None and entry.get("round_number") == round_number and entry.get("status") == "active"):
                    target_entry = entry
                    target_idx = i
                    break
        else:
            for i in range(len(index) - 1, -1, -1):
                if index[i].get("status") == "active":
                    target_entry = index[i]
                    target_idx = i
                    break

        if target_entry is None or target_idx < 0:
            self.log_change(batch_id, "undo_import_skipped", {
                            "reason": "no_active_import_found",
                            "import_id": import_id,
                            "round_number": round_number,
                        }, severity="warning")
            return None

        removed_import_id = target_entry.get("import_id", "")
        removed_filename = target_entry.get("filename", "")
        removed_abs_path = target_entry.get("abs_path", "")
        bound_event_ids = set(target_entry.get("event_ids", []))
        bound_error_refs = target_entry.get("parse_error_refs", [])
        removed_round = target_entry.get("round_number", 0)

        self.log_change(batch_id, "undo_import_started", {
            "import_id": removed_import_id,
            "filename": removed_filename,
            "round_number": removed_round,
            "bound_event_count": len(bound_event_ids),
        }, severity="info")

        removed_event_count = 0
        kept_events = []
        orphaned_events = []
        orphaned_map = {}
        removed_events_snapshot = []
        events = self.load_events(batch_id)
        for e in events:
            has_other_active = False
            has_this_import = removed_import_id in e.import_ids
            other_import_ids = [iid for iid in e.import_ids if iid != removed_import_id]
            for other_iid in other_import_ids:
                for entry2 in index:
                    if entry2.get("import_id") == other_iid and entry2.get("status") == "active":
                        has_other_active = True
                        break
                if has_other_active:
                    break
            if has_this_import and not has_other_active:
                removed_event_count += 1
                orphaned_events.append(e.id)
                try:
                    removed_events_snapshot.append(e.to_dict())
                except Exception:
                    pass
            else:
                if has_this_import:
                    new_import_ids = other_import_ids
                    new_rounds = [r for r in e.import_rounds if r != removed_round]
                    e.import_ids = new_import_ids
                    e.import_rounds = new_rounds
                    if e.id not in orphaned_map:
                        orphaned_map[e.id] = {
                            "kept_other_imports": len(other_import_ids),
                        }
                kept_events.append(e)

        data = [e.to_dict() for e in kept_events]
        self._ensure_batch_dir(batch_id)
        with open(self._events_path(batch_id), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self.update_batch_meta(batch_id, event_count=len(kept_events))

        removed_error_count = 0
        kept_errors = []
        errors = self.load_parse_errors(batch_id)
        for err in errors:
            if err.import_id == removed_import_id:
                removed_error_count += 1
            else:
                kept_errors.append(err)
        data = [e.to_dict() for e in kept_errors]
        self._ensure_batch_dir(batch_id)
        with open(self._parse_errors_path(batch_id), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        now_iso = datetime.now().isoformat()
        index[target_idx]["status"] = "undone"
        index[target_idx]["undone_at"] = now_iso
        index[target_idx]["removed_event_count_actual"] = removed_event_count
        index[target_idx]["removed_error_count_actual"] = removed_error_count
        index[target_idx]["orphaned_events_due_to_dedup"] = list(orphaned_map)
        index[target_idx]["removed_events_snapshot"] = removed_events_snapshot
        index[target_idx]["last_operation"] = "undo"
        index[target_idx]["last_operation_at"] = now_iso
        index[target_idx]["last_operation_result"] = "success"
        index[target_idx]["last_operation_detail"] = {
            "action": "undo",
            "removed_event_count": removed_event_count,
            "removed_error_count": removed_error_count,
            "bound_event_count": len(bound_event_ids),
            "orphaned_count": len(orphaned_map),
            "round_number": removed_round,
        }
        index[target_idx]["matched_event_count"] = 0
        self._write_imports_index(batch_id, index)
        self._refresh_import_index_map(batch_id, index)

        source_type = self._infer_source_type(removed_filename)
        undo_detail = {
            "import_id": removed_import_id,
            "filename": removed_filename,
            "abs_path": removed_abs_path,
            "source_type": source_type,
            "file_hash": target_entry.get("file_hash", "")[:16] if target_entry.get("file_hash") else "",
            "imported_at": target_entry.get("imported_at", ""),
            "round_number": removed_round,
            "imported_event_count": target_entry.get("event_count", 0),
            "imported_error_count": target_entry.get("error_count", 0),
            "removed_event_count": removed_event_count,
            "removed_error_count": removed_error_count,
            "bound_event_count": len(bound_event_ids),
            "events_orphaned_count": len(orphaned_map),
        }
        self._record_undo(batch_id, "undo_import", undo_detail)
        self.log_change(batch_id, "undo_import_completed", undo_detail, severity="info")

        try:
            self.refresh_overview_snapshot(batch_id, trigger="undo_import")
        except Exception:
            pass
        return target_entry

    def restore_import(self, batch_id: str, import_id: str = None,
                       round_number: int = None) -> Optional[Dict]:
        index = self._read_imports_index(batch_id)
        if not index:
            return None

        target_entry = None
        target_idx = -1
        for i, entry in enumerate(index):
            if (import_id and entry.get("import_id") == import_id) or \
               (round_number is not None and entry.get("round_number") == round_number and entry.get("status") == "undone"):
                target_entry = entry
                target_idx = i
                break
        if target_entry is None or target_idx < 0:
            for i in range(len(index) - 1, -1, -1):
                if index[i].get("status") == "undone":
                    target_entry = index[i]
                    target_idx = i
                    break

        if target_entry is None:
            self.log_change(batch_id, "restore_import_skipped", {
                            "reason": "no_undone_import_found",
                            "import_id": import_id,
                            "round_number": round_number,
                        }, severity="warning")
            return None

        restore_import_id = target_entry.get("import_id", "")
        restore_filename = target_entry.get("filename", "")
        restore_round = target_entry.get("round_number", 0)
        bound_event_ids = target_entry.get("event_ids", [])
        bound_error_refs = target_entry.get("parse_error_refs", [])

        self.log_change(batch_id, "restore_import_started", {
            "import_id": restore_import_id,
            "filename": restore_filename,
            "round_number": restore_round,
        }, severity="info")

        restored_event_count = 0
        already_present_count = 0
        recreated_count = 0
        events = self.load_events(batch_id)
        existing_ids = {e.id for e in events}
        from .models import Event

        snapshot = target_entry.get("removed_events_snapshot", [])
        snapshot_by_id = {}
        if snapshot:
            for sd in snapshot:
                if isinstance(sd, dict) and "id" in sd:
                    snapshot_by_id[sd["id"]] = sd

        bound_event_id_set = set(bound_event_ids)
        recovered_kept_ids = set()
        for ev in events:
            if ev.id in bound_event_id_set:
                if restore_import_id not in ev.import_ids:
                    ev.import_ids.append(restore_import_id)
                if restore_round not in ev.import_rounds:
                    ev.import_rounds.append(restore_round)
        for eid in bound_event_ids:
            if eid in existing_ids:
                already_present_count += 1
                recovered_kept_ids.add(eid)
            elif eid in snapshot_by_id:
                try:
                    sd = snapshot_by_id[eid]
                    recovered_event = Event.from_dict(sd)
                    if restore_import_id not in recovered_event.import_ids:
                        recovered_event.import_ids.append(restore_import_id)
                    if restore_round not in recovered_event.import_rounds:
                        recovered_event.import_rounds.append(restore_round)
                    events.append(recovered_event)
                    recreated_count += 1
                except Exception as ex:
                    pass
        restored_event_count = already_present_count + recreated_count
        existing_ids_after = {e.id for e in events}
        data = [e.to_dict() for e in events]
        self._ensure_batch_dir(batch_id)
        with open(self._events_path(batch_id), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        restored_error_count = 0
        errors = self.load_parse_errors(batch_id)
        existing_err_refs = set((e.source_file, e.line_number) for e in errors)
        for (sf, ln) in bound_error_refs:
            if (sf, ln) not in existing_err_refs:
                from .models import ParseError
                new_err = ParseError(
                    source_file=sf,
                    line_number=ln,
                    error_type="restored",
                    error_message="恢复撤销时缺少详细错误信息（原始记录不可用）",
                    raw_content="",
                    import_id=restore_import_id,
                )
                errors.append(new_err)
                restored_error_count += 1
            else:
                for err in errors:
                    if err.source_file == sf and err.line_number == ln:
                        if not err.import_id:
                            err.import_id = restore_import_id
                        restored_error_count += 1
                        break
        data = [e.to_dict() for e in errors]
        self._ensure_batch_dir(batch_id)
        with open(self._parse_errors_path(batch_id), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        now_iso = datetime.now().isoformat()
        new_restored_count = index[target_idx].get("restored_count", 0) + 1
        index[target_idx]["status"] = "active"
        index[target_idx]["undone_at"] = None
        index[target_idx]["restored_count"] = new_restored_count
        index[target_idx]["last_restored_at"] = now_iso
        index[target_idx]["last_operation"] = "restore"
        index[target_idx]["last_operation_at"] = now_iso
        index[target_idx]["last_operation_result"] = "success"
        index[target_idx]["last_operation_detail"] = {
            "action": "restore",
            "restored_event_count": restored_event_count,
            "restored_error_count": restored_error_count,
            "recreated_event_count": recreated_count,
            "already_present_event_count": already_present_count,
            "restore_count_total": new_restored_count,
            "round_number": restore_round,
        }
        index[target_idx]["matched_event_count"] = restored_event_count
        index[target_idx]["restored_event_count"] = restored_event_count
        self._write_imports_index(batch_id, index)
        self._refresh_import_index_map(batch_id, index)
        self.update_batch_meta(batch_id, event_count=len(self.load_events(batch_id)))

        source_type = self._infer_source_type(restore_filename)
        restore_detail = {
            "import_id": restore_import_id,
            "filename": restore_filename,
            "source_type": source_type,
            "round_number": restore_round,
            "restored_event_count": restored_event_count,
            "restored_error_count": restored_error_count,
            "recreated_event_count": recreated_count,
            "already_present_event_count": already_present_count,
            "restore_count_total": index[target_idx]["restored_count"],
        }
        self._record_undo(batch_id, "restore_import", restore_detail)
        self.log_change(batch_id, "restore_import_completed", restore_detail, severity="info")

        try:
            self.refresh_overview_snapshot(batch_id, trigger="reimport")
        except Exception:
            pass
        return target_entry

    def _infer_source_type(self, filename: str) -> str:
        ext = Path(filename).suffix.lower()
        if ext == ".csv":
            return "告警(CSV)"
        elif ext == ".json":
            return "备注(JSON)"
        else:
            return "日志(LOG)"



    def load_overview_snapshot(self, batch_id: str, auto_refresh: bool = True) -> Dict:
        path = self._overview_snapshot_path(batch_id)
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        if auto_refresh:
            return self.refresh_overview_snapshot(batch_id, trigger="auto_refresh")
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
            self.refresh_overview_snapshot(batch_id, trigger="export")
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

    def _historical_snapshots_dir(self, batch_id: str) -> Path:
        return self._get_batch_dir(batch_id) / "snapshots_history"

    def _change_log_path(self, batch_id: str) -> Path:
        return self._get_batch_dir(batch_id) / "change_log.json"

    def _snapshot_consistency_marker_path(self, batch_id: str) -> Path:
        return self._get_batch_dir(batch_id) / "snapshot_consistency.json"

    def _round_index_path(self, batch_id: str) -> Path:
        return self._get_batch_dir(batch_id) / "round_index.json"

    def _read_round_index(self, batch_id: str) -> List[Dict]:
        path = self._round_index_path(batch_id)
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []

    def _write_round_index(self, batch_id: str, index: List[Dict]) -> None:
        self._ensure_batch_dir(batch_id)
        path = self._round_index_path(batch_id)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)

    def _get_next_round_number(self, batch_id: str) -> int:
        index = self._read_round_index(batch_id)
        if not index:
            return 1
        return index[-1].get("round_number", 0) + 1

    def _register_round(self, batch_id: str, trigger: str, before_snapshot_id: str,
                        after_snapshot_id: str, operation_detail: Dict = None) -> Dict:
        index = self._read_round_index(batch_id)
        round_number = self._get_next_round_number(batch_id)
        round_entry = {
            "round_number": round_number,
            "trigger": trigger,
            "before_snapshot_id": before_snapshot_id,
            "after_snapshot_id": after_snapshot_id,
            "created_at": datetime.now().isoformat(),
            "detail": operation_detail or {},
        }
        index.append(round_entry)
        self._write_round_index(batch_id, index)
        self.log_change(batch_id, "round_created", {
            "round_number": round_number,
            "trigger": trigger,
            "before_snapshot_id": before_snapshot_id,
            "after_snapshot_id": after_snapshot_id,
            "operation_detail": operation_detail,
        }, severity="info")
        return round_entry

    def _save_historical_snapshot(self, batch_id: str, snapshot: Dict, trigger: str,
                                  round_number: int = None) -> str:
        import shutil
        try:
            snapshots_dir = self._historical_snapshots_dir(batch_id)
            snapshots_dir.mkdir(exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            snapshot_id = f"snap_{ts}"
            if round_number is not None:
                snapshot_id = f"snap_r{round_number:06d}_{ts}"
            filename = f"{snapshot_id}.json"
            filepath = snapshots_dir / filename
            backup_filepath = snapshots_dir / f"{snapshot_id}.json.bak"
            historical_copy = copy.deepcopy(snapshot)
            historical_copy["snapshot_id"] = snapshot_id
            historical_copy["trigger"] = trigger
            historical_copy["saved_at"] = datetime.now().isoformat()
            if round_number is not None:
                historical_copy["round_number"] = round_number
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(historical_copy, f, ensure_ascii=False, indent=2)
            try:
                shutil.copy2(str(filepath), str(backup_filepath))
            except Exception:
                pass
            self.log_change(batch_id, "snapshot_saved", {
                "snapshot_id": snapshot_id,
                "trigger": trigger,
                "round_number": round_number,
                "event_count": historical_copy.get("event_count", 0),
                "imported_file_count": historical_copy.get("imported_file_count", 0),
            }, severity="debug")
            return snapshot_id
        except Exception as e:
            self.log_change(batch_id, "snapshot_save_failed", {
                "trigger": trigger,
                "error": str(e),
            }, severity="error")
            return ""

    def list_historical_snapshots(self, batch_id: str, limit: int = 20) -> List[Dict]:
        snapshots_dir = self._historical_snapshots_dir(batch_id)
        if not snapshots_dir.exists():
            return []
        result = []
        for f in sorted(snapshots_dir.iterdir(), reverse=True):
            if f.is_file() and f.suffix == ".json":
                try:
                    with open(f, "r", encoding="utf-8") as fp:
                        data = json.load(fp)
                    result.append({
                        "snapshot_id": data.get("snapshot_id", f.stem),
                        "trigger": data.get("trigger", "unknown"),
                        "saved_at": data.get("saved_at", ""),
                        "event_count": data.get("event_count", 0),
                        "imported_file_count": data.get("imported_file_count", 0),
                        "label_action_count": data.get("label_action_count", 0),
                        "export_count": data.get("export_count", 0),
                        "rule_version": data.get("rule_version", "unknown"),
                        "filepath": str(f),
                    })
                except (json.JSONDecodeError, IOError):
                    continue
            if len(result) >= limit:
                break
        return result

    def load_historical_snapshot(self, batch_id: str, snapshot_id: str) -> Optional[Dict]:
        snapshots_dir = self._historical_snapshots_dir(batch_id)
        filepath = snapshots_dir / f"{snapshot_id}.json"
        if not filepath.exists():
            return None
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data
        except (json.JSONDecodeError, IOError) as e:
            self.log_change(batch_id, "snapshot_load_failed", {
                "snapshot_id": snapshot_id,
                "error": str(e),
            }, severity="error")
            return self._recover_snapshot_from_backup(batch_id, snapshot_id)

    def _recover_snapshot_from_backup(self, batch_id: str, snapshot_id: str) -> Optional[Dict]:
        snapshots_dir = self._historical_snapshots_dir(batch_id)
        backup_path = snapshots_dir / f"{snapshot_id}.json.bak"
        if backup_path.exists():
            try:
                with open(backup_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.log_change(batch_id, "snapshot_recovered_from_backup", {
                    "snapshot_id": snapshot_id,
                }, severity="warning")
                original_path = snapshots_dir / f"{snapshot_id}.json"
                with open(original_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                return data
            except (json.JSONDecodeError, IOError):
                pass
        return None

    def list_rounds(self, batch_id: str, limit: int = 50) -> List[Dict]:
        index = self._read_round_index(batch_id)
        result = []
        for entry in reversed(index[-limit:]):
            before_snap = self.load_historical_snapshot(batch_id, entry["before_snapshot_id"])
            after_snap = self.load_historical_snapshot(batch_id, entry["after_snapshot_id"])
            round_info = {
                "round_number": entry["round_number"],
                "trigger": entry["trigger"],
                "created_at": entry["created_at"],
                "before_snapshot_id": entry["before_snapshot_id"],
                "after_snapshot_id": entry["after_snapshot_id"],
                "before_event_count": before_snap.get("event_count", 0) if before_snap else 0,
                "after_event_count": after_snap.get("event_count", 0) if after_snap else 0,
                "before_import_count": before_snap.get("imported_file_count", 0) if before_snap else 0,
                "after_import_count": after_snap.get("imported_file_count", 0) if after_snap else 0,
                "rule_version": after_snap.get("rule_version", "unknown") if after_snap else "unknown",
                "detail": entry.get("detail", {}),
            }
            if before_snap and after_snap:
                diff = self._compare_snapshots(before_snap, after_snap)
                round_info["summary"] = diff.get("summary", [])
                round_info["event_count_change"] = diff.get("event_count_change", 0)
            result.append(round_info)
        return result

    def get_round(self, batch_id: str, round_number: int) -> Optional[Dict]:
        index = self._read_round_index(batch_id)
        for entry in index:
            if entry["round_number"] == round_number:
                before_snap = self.load_historical_snapshot(batch_id, entry["before_snapshot_id"])
                after_snap = self.load_historical_snapshot(batch_id, entry["after_snapshot_id"])
                return {
                    "round_number": entry["round_number"],
                    "trigger": entry["trigger"],
                    "created_at": entry["created_at"],
                    "before_snapshot": before_snap,
                    "after_snapshot": after_snap,
                    "before_snapshot_id": entry["before_snapshot_id"],
                    "after_snapshot_id": entry["after_snapshot_id"],
                    "detail": entry.get("detail", {}),
                    "diff": self._compare_snapshots(before_snap, after_snap) if (before_snap and after_snap) else None,
                }
        return None

    def get_round_diff(self, batch_id: str, round_number: int) -> Optional[Dict]:
        round_info = self.get_round(batch_id, round_number)
        if not round_info:
            return None
        before = round_info.get("before_snapshot")
        after = round_info.get("after_snapshot")
        if not before or not after:
            return None
        return {
            "round_number": round_number,
            "trigger": round_info["trigger"],
            "created_at": round_info["created_at"],
            "diff": self._compare_snapshots(before, after),
            "before_snapshot_id": round_info["before_snapshot_id"],
            "after_snapshot_id": round_info["after_snapshot_id"],
        }

    def check_config_conflict(self, batch_id: str, new_config: RuleConfig) -> Dict:
        result = {
            "has_conflict": False,
            "conflicts": [],
            "current_config": None,
            "new_config": None,
        }
        try:
            current_config = self.load_config(batch_id)
            result["current_config"] = current_config.to_dict()
            result["new_config"] = new_config.to_dict()
            fields_to_check = [
                ("rule_version", "规则版本"),
                ("dedup_window_seconds", "去重窗口"),
                ("gap_threshold_seconds", "缺口阈值"),
                ("dedup_similarity_threshold", "去重相似度"),
            ]
            for field, label in fields_to_check:
                current_val = getattr(current_config, field)
                new_val = getattr(new_config, field)
                if current_val != new_val:
                    result["has_conflict"] = True
                    result["conflicts"].append({
                        "field": field,
                        "label": label,
                        "current": current_val,
                        "new": new_val,
                    })
        except Exception as e:
            result["has_conflict"] = True
            result["error"] = str(e)
        return result

    def check_database_state_lag(self, batch_id: str) -> Dict:
        result = {
            "is_lagged": False,
            "details": [],
            "snapshot_event_count": 0,
            "actual_event_count": 0,
        }
        try:
            snapshot = self.load_overview_snapshot(batch_id, auto_refresh=False)
            actual_events = len(self.load_events(batch_id))
            snap_events = snapshot.get("event_count", 0)
            result["snapshot_event_count"] = snap_events
            result["actual_event_count"] = actual_events
            if snap_events != actual_events:
                result["is_lagged"] = True
                result["details"].append(f"快照事件数({snap_events})与实际事件数({actual_events})不一致")
        except Exception as e:
            result["is_lagged"] = True
            result["error"] = str(e)
        return result

    def repair_snapshot_file(self, batch_id: str, snapshot_id: str) -> Dict:
        result = {
            "repaired": False,
            "message": "",
            "actions": [],
        }
        snapshots_dir = self._historical_snapshots_dir(batch_id)
        filepath = snapshots_dir / f"{snapshot_id}.json"
        if not filepath.exists():
            result["message"] = f"快照文件不存在: {snapshot_id}"
            return result
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            result["message"] = "快照文件完整，无需修复"
            result["repaired"] = True
            return result
        except json.JSONDecodeError as e:
            result["actions"].append(f"检测到JSON损坏: {e}")
            backup = self._recover_snapshot_from_backup(batch_id, snapshot_id)
            if backup:
                result["repaired"] = True
                result["actions"].append("已从备份恢复")
                result["message"] = "快照已从备份恢复"
            else:
                result["actions"].append("尝试从相邻快照重建")
                all_snaps = self.list_historical_snapshots(batch_id, limit=1000)
                snap_ids = [s["snapshot_id"] for s in all_snaps]
                if snapshot_id in snap_ids:
                    idx = snap_ids.index(snapshot_id)
                    if idx > 0:
                        prev_snap = self.load_historical_snapshot(batch_id, snap_ids[idx - 1])
                        if prev_snap:
                            with open(filepath, "w", encoding="utf-8") as f:
                                json.dump(prev_snap, f, ensure_ascii=False, indent=2)
                            result["repaired"] = True
                            result["actions"].append(f"已使用前一快照 {snap_ids[idx-1]} 重建")
                            result["message"] = "快照已从前一相邻快照重建"
            if not result["repaired"]:
                result["message"] = "无法修复快照，建议从更早的历史状态恢复"
        return result

    def restore_to_snapshot(self, batch_id: str, snapshot_id: str,
                             recover_events: bool = True) -> Dict:
        result = {
            "success": False,
            "message": "",
            "restored_from": snapshot_id,
            "restored_events": 0,
            "restored_imports": 0,
            "restored_errors": 0,
            "actions": [],
        }
        try:
            snapshot = self.load_historical_snapshot(batch_id, snapshot_id)
            if not snapshot:
                result["message"] = f"无法加载快照: {snapshot_id}"
                return result

            self.log_change(batch_id, "snapshot_restore_started", {
                "snapshot_id": snapshot_id,
                "recover_events": recover_events,
            }, severity="warning")

            current_snap = self.load_overview_snapshot(batch_id, auto_refresh=False)
            before_id = self._save_historical_snapshot(batch_id, current_snap, "restore_before")

            restored_files_info = snapshot.get("imported_files", [])
            if recover_events and restored_files_info:
                all_imports = self._read_imports_index(batch_id)
                target_filenames = {f.get("filename") for f in restored_files_info}
                target_hashes = {f.get("file_hash", "") for f in restored_files_info if f.get("file_hash")}

                for imp in all_imports:
                    imp_fname = imp.get("filename", "")
                    imp_hash = imp.get("file_hash", "")[:16] if imp.get("file_hash") else ""
                    should_be_active = False
                    for sf in restored_files_info:
                        sf_name = sf.get("filename", "")
                        sf_hash = sf.get("file_hash", "")
                        if sf_name == imp_fname and (not sf_hash or sf_hash[:16] == imp_hash):
                            should_be_active = True
                            break
                    imp["status"] = "active" if should_be_active else "undone"
                    if should_be_active:
                        imp["undone_at"] = None
                    else:
                        if not imp.get("undone_at"):
                            imp["undone_at"] = datetime.now().isoformat()
                self._write_imports_index(batch_id, all_imports)
                result["restored_imports"] = sum(
                    1 for imp in all_imports if imp.get("status") == "active"
                )
                result["actions"].append(
                    f"已调整 {len(all_imports)} 条导入记录的激活状态"
                )

                active_import_ids = {
                    imp.get("import_id") for imp in all_imports
                    if imp.get("status") == "active" and imp.get("import_id")
                }
                events = self.load_events(batch_id)
                kept_events = []
                removed_count = 0
                for e in events:
                    if not e.import_ids:
                        kept_events.append(e)
                        continue
                    has_active = any(iid in active_import_ids for iid in e.import_ids)
                    if has_active:
                        new_import_ids = [iid for iid in e.import_ids if iid in active_import_ids]
                        new_rounds = list({
                            r for iid, r in zip(e.import_ids, e.import_rounds)
                            if iid in active_import_ids
                        })
                        if not new_import_ids:
                            new_import_ids = list(active_import_ids & set(e.import_ids))
                        e.import_ids = new_import_ids
                        e.import_rounds = new_rounds
                        kept_events.append(e)
                    else:
                        removed_count += 1
                data = [e.to_dict() for e in kept_events]
                self._ensure_batch_dir(batch_id)
                with open(self._events_path(batch_id), "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                self.update_batch_meta(batch_id, event_count=len(kept_events))
                result["restored_events"] = len(kept_events)
                result["actions"].append(f"保留 {len(kept_events)} 条事件，移除 {removed_count} 条")

                errors = self.load_parse_errors(batch_id)
                kept_errors = [e for e in errors if not e.import_id or e.import_id in active_import_ids]
                data = [e.to_dict() for e in kept_errors]
                self._ensure_batch_dir(batch_id)
                with open(self._parse_errors_path(batch_id), "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                result["restored_errors"] = len(kept_errors)
                result["actions"].append(
                    f"保留 {len(kept_errors)} 条解析错误，移除 {len(errors) - len(kept_errors)} 条"
                )

            snap_path = self._overview_snapshot_path(batch_id)
            with open(snap_path, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, ensure_ascii=False, indent=2)
            self._update_snapshot_consistency_marker(batch_id, snapshot)

            after_snap = self.refresh_overview_snapshot(batch_id, trigger="restore")
            after_id = self._save_historical_snapshot(batch_id, after_snap, "restore_after")

            round_number = self._get_next_round_number(batch_id)
            self._register_round(batch_id, "restore", before_id, after_id, {
                "restored_from_snapshot": snapshot_id,
                "recover_events": recover_events,
                "restored_event_count": result["restored_events"],
                "restored_import_count": result["restored_imports"],
            })
            self.log_change(batch_id, "snapshot_restored", {
                "snapshot_id": snapshot_id,
                "round_number": round_number,
                "restored_events": result["restored_events"],
                "restored_imports": result["restored_imports"],
            }, severity="warning")
            result["success"] = True
            result["message"] = f"已恢复到快照 {snapshot_id}，底层数据同步重建完成"
            result["round_number"] = round_number

            verify = self.check_snapshot_consistency(batch_id)
            if not verify.get("consistent"):
                result["actions"].append(
                    f"警告: 恢复后发现 {len(verify.get('inconsistencies', []))} 处不一致"
                )
                self.fix_snapshot_inconsistencies(batch_id)
        except Exception as e:
            result["message"] = f"恢复失败: {e}"
            result["error"] = str(e)
            import traceback
            self.log_change(batch_id, "snapshot_restore_failed", {
                "snapshot_id": snapshot_id,
                "error": str(e),
                "traceback": traceback.format_exc()[:500],
            }, severity="error")
        return result

    def _compare_snapshots(self, old_snap: Dict, new_snap: Dict) -> Dict:
        diff = {
            "changes": {},
            "added_events": 0,
            "removed_events": 0,
            "status_changes": {},
            "source_changes": {},
            "severity_changes": {},
            "config_changes": {},
            "import_changes": [],
            "export_changes": [],
            "label_changes": [],
            "summary": [],
        }

        old_events = old_snap.get("event_count", 0)
        new_events = new_snap.get("event_count", 0)
        if old_events != new_events:
            diff["event_count_change"] = new_events - old_events
            if new_events > old_events:
                diff["added_events"] = new_events - old_events
                diff["summary"].append(f"事件数变化: +{new_events - old_events}")
            else:
                diff["removed_events"] = old_events - new_events
                diff["summary"].append(f"事件数变化: -{old_events - new_events}")

        old_by_status = old_snap.get("events_by_status", {})
        new_by_status = new_snap.get("events_by_status", {})
        all_statuses = set(old_by_status.keys()) | set(new_by_status.keys())
        for s in all_statuses:
            old_val = old_by_status.get(s, 0)
            new_val = new_by_status.get(s, 0)
            if old_val != new_val:
                diff["status_changes"][s] = new_val - old_val

        old_by_source = old_snap.get("events_by_source", {})
        new_by_source = new_snap.get("events_by_source", {})
        all_sources = set(old_by_source.keys()) | set(new_by_source.keys())
        for s in all_sources:
            old_val = old_by_source.get(s, 0)
            new_val = new_by_source.get(s, 0)
            if old_val != new_val:
                diff["source_changes"][s] = new_val - old_val

        old_by_severity = old_snap.get("events_by_severity", {})
        new_by_severity = new_snap.get("events_by_severity", {})
        all_severities = set(old_by_severity.keys()) | set(new_by_severity.keys())
        for s in all_severities:
            old_val = old_by_severity.get(s, 0)
            new_val = new_by_severity.get(s, 0)
            if old_val != new_val:
                diff["severity_changes"][s] = new_val - old_val

        config_fields = ["rule_version", "dedup_window_seconds", "gap_threshold_seconds",
                         "dedup_similarity_threshold", "phase_count"]
        for field in config_fields:
            old_val = old_snap.get(field)
            new_val = new_snap.get(field)
            if old_val != new_val:
                diff["config_changes"][field] = {"old": old_val, "new": new_val}
                diff["summary"].append(f"配置变更: {field}: {old_val} → {new_val}")

        old_files = {f.get("filename"): f for f in old_snap.get("imported_files", [])}
        new_files = {f.get("filename"): f for f in new_snap.get("imported_files", [])}
        for fname in new_files:
            if fname not in old_files:
                nf = new_files[fname]
                diff["import_changes"].append({
                    "type": "added",
                    "filename": fname,
                    "source_type": nf.get("source_type"),
                    "event_count": nf.get("event_count", 0),
                    "error_count": nf.get("error_count", 0),
                    "imported_at": nf.get("imported_at"),
                })
                diff["summary"].append(f"新增导入: {fname} ({nf.get('source_type')}, {nf.get('event_count', 0)}事件)")
        for fname in old_files:
            if fname not in new_files:
                of = old_files[fname]
                diff["import_changes"].append({
                    "type": "removed",
                    "filename": fname,
                    "source_type": of.get("source_type"),
                    "event_count": of.get("event_count", 0),
                    "error_count": of.get("error_count", 0),
                })
                diff["summary"].append(f"移除导入: {fname}")

        old_export_count = old_snap.get("export_count", 0)
        new_export_count = new_snap.get("export_count", 0)
        if new_export_count > old_export_count:
            last_exp = new_snap.get("last_export", {})
            diff["export_changes"].append({
                "type": "new_export",
                "filename": last_exp.get("filename"),
                "size": last_exp.get("size"),
                "exported_at": last_exp.get("exported_at"),
            })
            diff["summary"].append(f"新增导出: {last_exp.get('filename')} ({last_exp.get('size')}字节)")

        old_label_count = old_snap.get("label_action_count", 0)
        new_label_count = new_snap.get("label_action_count", 0)
        old_undo_count = old_snap.get("undo_action_count", 0)
        new_undo_count = new_snap.get("undo_action_count", 0)

        if new_label_count != old_label_count:
            last_label = new_snap.get("last_label_action")
            if last_label and new_label_count > old_label_count:
                diff["label_changes"].append({
                    "type": "label",
                    "operation": last_label.get("operation"),
                    "event_id_short": last_label.get("event_id_short"),
                    "old_status": last_label.get("old_status"),
                    "new_status": last_label.get("new_status"),
                    "acted_at": last_label.get("acted_at"),
                })
                diff["summary"].append(f"新标注: {last_label.get('operation')} -> {last_label.get('new_status')}")

        if new_undo_count != old_undo_count:
            last_undo = new_snap.get("last_undo_action")
            if last_undo and new_undo_count > old_undo_count:
                diff["label_changes"].append({
                    "type": "undo",
                    "undo_type_desc": last_undo.get("undo_type_desc"),
                    "acted_at": last_undo.get("acted_at"),
                })
                diff["summary"].append(f"撤销操作: {last_undo.get('undo_type_desc')}")

        return diff

    def get_change_summary(self, batch_id: str, compare_with: str = "previous") -> Dict:
        current = self.load_overview_snapshot(batch_id, auto_refresh=False)
        if not current:
            return {"error": "no_current_snapshot"}

        if compare_with == "previous":
            snapshots = self.list_historical_snapshots(batch_id, limit=2)
            if len(snapshots) < 1:
                return {
                    "current": current,
                    "previous": None,
                    "diff": None,
                    "note": "没有历史快照可供对比，当前为初始状态",
                }
            prev_id = snapshots[0]["snapshot_id"]
            previous = self.load_historical_snapshot(batch_id, prev_id)
        elif compare_with == "first":
            all_snaps = self.list_historical_snapshots(batch_id, limit=1000)
            if not all_snaps:
                return {
                    "current": current,
                    "previous": None,
                    "diff": None,
                    "note": "没有历史快照",
                }
            first_id = all_snaps[-1]["snapshot_id"]
            previous = self.load_historical_snapshot(batch_id, first_id)
        else:
            previous = self.load_historical_snapshot(batch_id, compare_with)

        if not previous:
            return {
                "current": current,
                "previous": None,
                "diff": None,
                "note": f"找不到快照: {compare_with}",
            }

        diff = self._compare_snapshots(previous, current)
        return {
            "current": current,
            "previous": previous,
            "diff": diff,
            "note": f"对比快照 {previous.get('snapshot_id')} (触发: {previous.get('trigger', 'unknown')})",
        }

    def _read_change_log(self, batch_id: str) -> List[Dict]:
        path = self._change_log_path(batch_id)
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []

    def _write_change_log(self, batch_id: str, log: List[Dict]) -> None:
        self._ensure_batch_dir(batch_id)
        with open(self._change_log_path(batch_id), "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False, indent=2)

    def log_change(self, batch_id: str, change_type: str, detail: Dict, severity: str = "info") -> None:
        log = self._read_change_log(batch_id)
        entry = {
            "id": str(uuid.uuid4())[:8],
            "change_type": change_type,
            "detail": detail,
            "severity": severity,
            "created_at": datetime.now().isoformat(),
        }
        log.append(entry)
        if len(log) > 500:
            log = log[-500:]
        self._write_change_log(batch_id, log)

    def get_change_log(self, batch_id: str, limit: int = 50, change_type: str = None) -> List[Dict]:
        log = self._read_change_log(batch_id)
        if change_type:
            log = [e for e in log if e.get("change_type") == change_type]
        return log[-limit:][::-1]

    def _update_snapshot_consistency_marker(self, batch_id: str, snapshot: Dict) -> None:
        try:
            marker = {
                "event_count": snapshot.get("event_count", 0),
                "imported_file_count": snapshot.get("imported_file_count", 0),
                "parse_error_count": snapshot.get("parse_error_count", 0),
                "label_action_count": snapshot.get("label_action_count", 0),
                "export_count": snapshot.get("export_count", 0),
                "rule_version": snapshot.get("rule_version", ""),
                "dedup_window_seconds": snapshot.get("dedup_window_seconds", 0),
                "gap_threshold_seconds": snapshot.get("gap_threshold_seconds", 0),
                "updated_at": datetime.now().isoformat(),
            }
            with open(self._snapshot_consistency_marker_path(batch_id), "w", encoding="utf-8") as f:
                json.dump(marker, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def check_snapshot_consistency(self, batch_id: str) -> Dict:
        result = {
            "consistent": True,
            "inconsistencies": [],
            "current_snapshot": None,
            "real_data": None,
        }

        try:
            snapshot = self.load_overview_snapshot(batch_id, auto_refresh=False)
            result["current_snapshot"] = snapshot

            real_events = len(self.load_events(batch_id))
            real_imports = len(self.get_imported_files(batch_id))
            real_errors = len(self.load_parse_errors(batch_id))
            real_config = self.load_config(batch_id)
            real_exports = len(self.get_exports(batch_id))
            real_history = len(self.get_label_history(batch_id))

            result["real_data"] = {
                "event_count": real_events,
                "imported_file_count": real_imports,
                "parse_error_count": real_errors,
                "label_action_count": real_history,
                "export_count": real_exports,
                "rule_version": real_config.rule_version,
                "dedup_window_seconds": real_config.dedup_window_seconds,
                "gap_threshold_seconds": real_config.gap_threshold_seconds,
            }

            snap_events = snapshot.get("event_count", 0)
            if snap_events != real_events:
                result["consistent"] = False
                result["inconsistencies"].append({
                    "field": "event_count",
                    "snapshot": snap_events,
                    "real": real_events,
                    "diff": real_events - snap_events,
                })

            snap_imports = snapshot.get("imported_file_count", 0)
            if snap_imports != real_imports:
                result["consistent"] = False
                result["inconsistencies"].append({
                    "field": "imported_file_count",
                    "snapshot": snap_imports,
                    "real": real_imports,
                    "diff": real_imports - snap_imports,
                })

            snap_errors = snapshot.get("parse_error_count", 0)
            if snap_errors != real_errors:
                result["consistent"] = False
                result["inconsistencies"].append({
                    "field": "parse_error_count",
                    "snapshot": snap_errors,
                    "real": real_errors,
                    "diff": real_errors - snap_errors,
                })

            snap_version = snapshot.get("rule_version", "")
            if snap_version != real_config.rule_version:
                result["consistent"] = False
                result["inconsistencies"].append({
                    "field": "rule_version",
                    "snapshot": snap_version,
                    "real": real_config.rule_version,
                })

            snap_dedup = snapshot.get("dedup_window_seconds", 0)
            if snap_dedup != real_config.dedup_window_seconds:
                result["consistent"] = False
                result["inconsistencies"].append({
                    "field": "dedup_window_seconds",
                    "snapshot": snap_dedup,
                    "real": real_config.dedup_window_seconds,
                })

            snap_gap = snapshot.get("gap_threshold_seconds", 0)
            if snap_gap != real_config.gap_threshold_seconds:
                result["consistent"] = False
                result["inconsistencies"].append({
                    "field": "gap_threshold_seconds",
                    "snapshot": snap_gap,
                    "real": real_config.gap_threshold_seconds,
                })

            snap_exports = snapshot.get("export_count", 0)
            if snap_exports != real_exports:
                result["consistent"] = False
                result["inconsistencies"].append({
                    "field": "export_count",
                    "snapshot": snap_exports,
                    "real": real_exports,
                    "diff": real_exports - snap_exports,
                })

            snap_history = snapshot.get("label_action_count", 0)
            if snap_history != real_history:
                result["consistent"] = False
                result["inconsistencies"].append({
                    "field": "label_action_count",
                    "snapshot": snap_history,
                    "real": real_history,
                    "diff": real_history - snap_history,
                })

        except Exception as e:
            result["consistent"] = False
            result["error"] = str(e)

        return result

    def check_duplicate_import(self, batch_id: str, file_path: str, file_hash: str = None) -> Dict:
        result = {
            "is_duplicate": False,
            "existing_entry": None,
            "hash_changed": False,
            "recommendation": "import",
        }

        abs_path = str(Path(file_path).resolve())
        filename = os.path.basename(file_path)

        imported = self.get_imported_files(batch_id)
        for entry in imported:
            if entry.get("abs_path") == abs_path or entry.get("filename") == filename:
                result["is_duplicate"] = True
                result["existing_entry"] = {
                    "filename": entry.get("filename"),
                    "abs_path": entry.get("abs_path"),
                    "file_hash": entry.get("file_hash", "")[:16],
                    "event_count": entry.get("event_count", 0),
                    "error_count": entry.get("error_count", 0),
                    "imported_at": entry.get("imported_at"),
                }
                if file_hash:
                    existing_hash = entry.get("file_hash", "")
                    if existing_hash and existing_hash != file_hash:
                        result["hash_changed"] = True
                        result["recommendation"] = "force_reimport"
                    else:
                        result["recommendation"] = "skip"
                else:
                    result["recommendation"] = "check_hash"
                break

        if not result["is_duplicate"]:
            undone = self.get_undone_imports(batch_id)
            for entry in undone:
                if entry.get("abs_path") == abs_path or entry.get("filename") == filename:
                    result["is_duplicate"] = True
                    result["existing_entry"] = {
                        "filename": entry.get("filename"),
                        "abs_path": entry.get("abs_path"),
                        "file_hash": entry.get("file_hash", "")[:16],
                        "event_count": entry.get("event_count", 0),
                        "error_count": entry.get("error_count", 0),
                        "imported_at": entry.get("imported_at"),
                        "status": "undone",
                    }
                    result["recommendation"] = "restore_or_force"
                    break

        self.log_change(batch_id, "duplicate_import_check", {
            "file_path": abs_path,
            "is_duplicate": result["is_duplicate"],
            "recommendation": result["recommendation"],
            "hash_changed": result["hash_changed"],
        }, severity="debug")

        return result

    def get_export_comparison(self, batch_id: str) -> Dict:
        exports = self.get_exports(batch_id)
        if not exports:
            return {"exports": [], "comparison": None}

        result = {
            "exports": exports[:10],
            "comparison": None,
        }

        if len(exports) >= 2:
            latest = exports[0]
            previous = exports[1]
            size_diff = latest.get("size", 0) - previous.get("size", 0)
            result["comparison"] = {
                "latest": latest,
                "previous": previous,
                "size_diff": size_diff,
                "size_diff_percent": round((size_diff / previous.get("size", 1)) * 100, 1) if previous.get("size", 0) > 0 else 0,
                "time_diff_seconds": None,
            }
            try:
                from datetime import datetime as dt
                latest_dt = dt.fromisoformat(latest.get("modified_at", ""))
                prev_dt = dt.fromisoformat(previous.get("modified_at", ""))
                result["comparison"]["time_diff_seconds"] = int((latest_dt - prev_dt).total_seconds())
            except Exception:
                pass

        return result

    def refresh_overview_snapshot(self, batch_id: str, trigger: str = "manual") -> Dict:
        old_snapshot = None
        snapshot_path = self._overview_snapshot_path(batch_id)
        if snapshot_path.exists():
            try:
                with open(snapshot_path, "r", encoding="utf-8") as f:
                    old_snapshot = json.load(f)
            except (json.JSONDecodeError, IOError):
                pass

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
            all_imported = self.get_imported_files(batch_id, include_undone=True)
            active_imported = [f for f in all_imported if f.get("status") == "active"]
            imported_files = []
            undone_count = 0
            restored_total_count = 0
            for f in all_imported:
                is_active = f.get("status") == "active"
                entry = {
                    "import_id": f.get("import_id", ""),
                    "filename": f.get("filename", "未知文件"),
                    "abs_path": f.get("abs_path", ""),
                    "source_type": self._infer_source_type(f.get("filename", "")),
                    "event_count": f.get("event_count", 0),
                    "error_count": f.get("error_count", 0),
                    "file_hash": f.get("file_hash", "")[:16] if f.get("file_hash") else "",
                    "imported_at": f.get("imported_at", ""),
                    "round_number": f.get("round_number", 0),
                    "status": f.get("status", "active"),
                    "bound_event_count": len(f.get("event_ids", [])),
                }
                if not is_active:
                    undone_count += 1
                    entry["undone_at"] = f.get("undone_at", "")
                    entry["removed_event_count_actual"] = f.get("removed_event_count_actual", 0)
                    entry["orphaned_events_due_to_dedup"] = len(f.get("orphaned_events_due_to_dedup", []))
                    entry["restored_count"] = f.get("restored_count", 0)
                    restored_total_count += f.get("restored_count", 0)
                imported_files.append(entry)
            snapshot["imported_files"] = imported_files
            snapshot["imported_file_count"] = len(active_imported)
            snapshot["total_import_entries"] = len(all_imported)
            snapshot["undone_import_count"] = undone_count
            snapshot["restored_import_total_count"] = restored_total_count
            if undone_count > 0:
                snapshot["active_imported_files"] = [
                    f for f in imported_files if f.get("status") == "active"
                ]
                snapshot["undone_imported_files"] = [
                    f for f in imported_files if f.get("status") == "undone"
                ]
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

        snapshot_path = self._overview_snapshot_path(batch_id)
        try:
            if snapshot_path.exists():
                backup_path = snapshot_path.with_suffix(snapshot_path.suffix + ".bak")
                import shutil
                shutil.copy2(str(snapshot_path), str(backup_path))
            self._ensure_batch_dir(batch_id)
            with open(snapshot_path, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, ensure_ascii=False, indent=2)
            self.log_change(batch_id, "overview_snapshot_written", {
                "trigger": trigger,
                "event_count": snapshot.get("event_count", 0),
            }, severity="debug")
        except Exception as e:
            snapshot["_write_error"] = str(e)
            self.log_change(batch_id, "overview_snapshot_write_failed", {
                "trigger": trigger,
                "error": str(e),
            }, severity="error")

        if old_snapshot:
            has_changes = False
            try:
                key_fields = ["event_count", "imported_file_count", "parse_error_count",
                              "export_count", "label_action_count", "undo_action_count",
                              "rule_version", "dedup_window_seconds", "gap_threshold_seconds"]
                for field in key_fields:
                    if old_snapshot.get(field) != snapshot.get(field):
                        has_changes = True
                        break
                if not has_changes:
                    old_imports = {f.get("filename") for f in old_snapshot.get("imported_files", [])}
                    new_imports = {f.get("filename") for f in snapshot.get("imported_files", [])}
                    if old_imports != new_imports:
                        has_changes = True
            except Exception:
                has_changes = True

            if has_changes:
                try:
                    diff = self._compare_snapshots(old_snapshot, snapshot)
                    change_type_map = {
                        "import": "import_change",
                        "undo_import": "import_change",
                        "config": "config_change",
                        "export": "export_change",
                        "manual": "manual_refresh",
                        "reimport": "import_change",
                        "undo_label": "label_change",
                        "label": "label_change",
                    }
                    change_type = change_type_map.get(trigger, "other_change")
                    self.log_change(batch_id, change_type, {
                        "trigger": trigger,
                        "diff_summary": diff.get("summary", []),
                        "event_count_change": diff.get("event_count_change", 0),
                        "status_changes": diff.get("status_changes", {}),
                        "source_changes": diff.get("source_changes", {}),
                        "config_changes": diff.get("config_changes", {}),
                    }, severity="info")

                    round_number = self._get_next_round_number(batch_id)
                    before_id = self._save_historical_snapshot(batch_id, old_snapshot, trigger, round_number)
                    after_id = self._save_historical_snapshot(batch_id, snapshot, trigger, round_number)
                    operation_detail = {
                        "files_imported": [f.get("filename") for f in diff.get("import_changes", []) if f.get("type") == "added"],
                        "files_removed": [f.get("filename") for f in diff.get("import_changes", []) if f.get("type") == "removed"],
                        "event_count_change": diff.get("event_count_change", 0),
                        "config_changes": diff.get("config_changes", {}),
                    }
                    self._register_round(batch_id, trigger, before_id, after_id, operation_detail)

                    self.log_change(batch_id, "round_saved", {
                        "round_number": round_number,
                        "trigger": trigger,
                        "before_snapshot_id": before_id,
                        "after_snapshot_id": after_id,
                        "has_changes": has_changes,
                    }, severity="info")
                except Exception as e:
                    self.log_change(batch_id, "round_save_failed", {
                        "trigger": trigger,
                        "error": str(e),
                    }, severity="error")

        try:
            self._update_snapshot_consistency_marker(batch_id, snapshot)
        except Exception:
            pass

        return snapshot

    def fix_snapshot_inconsistencies(self, batch_id: str) -> Dict:
        check_result = self.check_snapshot_consistency(batch_id)
        if check_result.get("consistent", True):
            return {
                "fixed": False,
                "message": "快照一致，无需修复",
                "check_result": check_result,
            }

        try:
            inconsistencies = check_result.get("inconsistencies", [])
            self.log_change(batch_id, "snapshot_repair", {
                "inconsistencies_found": inconsistencies,
                "action": "auto_refresh",
            }, severity="warning")

            new_snapshot = self.refresh_overview_snapshot(batch_id, trigger="repair")

            verify_result = self.check_snapshot_consistency(batch_id)
            return {
                "fixed": verify_result.get("consistent", False),
                "message": "已执行快照修复",
                "inconsistencies_fixed": inconsistencies,
                "new_snapshot": new_snapshot,
                "verify_result": verify_result,
            }
        except Exception as e:
            return {
                "fixed": False,
                "message": f"修复失败: {e}",
                "error": str(e),
                "check_result": check_result,
            }
