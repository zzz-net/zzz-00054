from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum
import uuid
import hashlib


class EventSource(str, Enum):
    LOG = "log"
    ALERT = "alert"
    NOTE = "note"


class EventStatus(str, Enum):
    UNCONFIRMED = "待确认"
    CONFIRMED = "已确认"
    ROOT_CAUSE = "根因"
    NOISE = "噪声"


class Severity(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"
    FATAL = "FATAL"


SEVERITY_ORDER = {
    Severity.DEBUG: 0,
    Severity.INFO: 1,
    Severity.WARNING: 2,
    Severity.ERROR: 3,
    Severity.CRITICAL: 4,
    Severity.FATAL: 5,
}


@dataclass
class RawEvent:
    source: EventSource
    source_file: str
    line_number: int
    timestamp_raw: str
    message: str
    severity_raw: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["source"] = self.source.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RawEvent":
        source = EventSource(d["source"])
        return cls(
            source=source,
            source_file=d["source_file"],
            line_number=d["line_number"],
            timestamp_raw=d["timestamp_raw"],
            message=d["message"],
            severity_raw=d.get("severity_raw"),
            extra=d.get("extra", {}),
        )


@dataclass
class Event:
    id: str
    timestamp: datetime
    source: EventSource
    source_file: str
    line_number: int
    severity: Severity
    message: str
    status: EventStatus = EventStatus.UNCONFIRMED
    notes: str = ""
    raw_events: List[RawEvent] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)
    dedup_key: str = ""
    import_ids: List[str] = field(default_factory=list)
    import_rounds: List[int] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source.value,
            "source_file": self.source_file,
            "line_number": self.line_number,
            "severity": self.severity.value,
            "message": self.message,
            "status": self.status.value,
            "notes": self.notes,
            "raw_events": [re.to_dict() for re in self.raw_events],
            "extra": self.extra,
            "dedup_key": self.dedup_key,
            "import_ids": self.import_ids,
            "import_rounds": self.import_rounds,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Event":
        return cls(
            id=d["id"],
            timestamp=datetime.fromisoformat(d["timestamp"]),
            source=EventSource(d["source"]),
            source_file=d["source_file"],
            line_number=d["line_number"],
            severity=Severity(d["severity"]),
            message=d["message"],
            status=EventStatus(d["status"]),
            notes=d.get("notes", ""),
            raw_events=[RawEvent.from_dict(re) for re in d.get("raw_events", [])],
            extra=d.get("extra", {}),
            dedup_key=d.get("dedup_key", ""),
            import_ids=d.get("import_ids", []),
            import_rounds=d.get("import_rounds", []),
            created_at=datetime.fromisoformat(d.get("created_at", datetime.now().isoformat())),
            updated_at=datetime.fromisoformat(d.get("updated_at", datetime.now().isoformat())),
        )

    @staticmethod
    def generate_id(timestamp: datetime, source: EventSource, message: str, severity: Severity) -> str:
        content = f"{timestamp.isoformat()}|{source.value}|{severity.value}|{message}"
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


@dataclass
class ParseError:
    source_file: str
    line_number: int
    error_type: str
    error_message: str
    raw_content: str = ""
    import_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Phase:
    name: str
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    description: str = ""

    def contains(self, t: datetime) -> bool:
        if self.start_time and t < self.start_time:
            return False
        if self.end_time and t > self.end_time:
            return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Phase":
        return cls(
            name=d["name"],
            start_time=datetime.fromisoformat(d["start_time"]) if d.get("start_time") else None,
            end_time=datetime.fromisoformat(d["end_time"]) if d.get("end_time") else None,
            description=d.get("description", ""),
        )


@dataclass
class LabelHistory:
    id: str
    event_id: str
    operation: str
    old_status: Optional[EventStatus]
    new_status: Optional[EventStatus]
    old_notes: Optional[str]
    new_notes: Optional[str]
    config_version: str
    created_at: datetime

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "event_id": self.event_id,
            "operation": self.operation,
            "old_status": self.old_status.value if self.old_status else None,
            "new_status": self.new_status.value if self.new_status else None,
            "old_notes": self.old_notes,
            "new_notes": self.new_notes,
            "config_version": self.config_version,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "LabelHistory":
        return cls(
            id=d["id"],
            event_id=d["event_id"],
            operation=d["operation"],
            old_status=EventStatus(d["old_status"]) if d.get("old_status") else None,
            new_status=EventStatus(d["new_status"]) if d.get("new_status") else None,
            old_notes=d.get("old_notes"),
            new_notes=d.get("new_notes"),
            config_version=d["config_version"],
            created_at=datetime.fromisoformat(d["created_at"]),
        )
