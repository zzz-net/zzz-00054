from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional
from datetime import timedelta
from .models import Severity, EventSource


DEFAULT_TIMESTAMP_FORMATS = [
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y/%m/%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%d/%b/%Y:%H:%M:%S %z",
    "%Y-%m-%d %H:%M:%S,%f",
]


@dataclass
class RuleConfig:
    rule_version: str = "1.0.0"
    dedup_window_seconds: int = 300
    severity_mapping: Dict[str, Severity] = field(default_factory=lambda: {
        "debug": Severity.DEBUG,
        "DEBUG": Severity.DEBUG,
        "info": Severity.INFO,
        "INFO": Severity.INFO,
        "notice": Severity.INFO,
        "NOTICE": Severity.INFO,
        "warning": Severity.WARNING,
        "WARN": Severity.WARNING,
        "WARNING": Severity.WARNING,
        "warn": Severity.WARNING,
        "error": Severity.ERROR,
        "ERROR": Severity.ERROR,
        "err": Severity.ERROR,
        "ERR": Severity.ERROR,
        "critical": Severity.CRITICAL,
        "CRITICAL": Severity.CRITICAL,
        "CRIT": Severity.CRITICAL,
        "fatal": Severity.FATAL,
        "FATAL": Severity.FATAL,
        "P1": Severity.CRITICAL,
        "P2": Severity.ERROR,
        "P3": Severity.WARNING,
        "P4": Severity.INFO,
        "P5": Severity.DEBUG,
    })
    timestamp_formats: List[str] = field(default_factory=lambda: list(DEFAULT_TIMESTAMP_FORMATS))
    gap_threshold_seconds: int = 600
    phases: List[Dict] = field(default_factory=list)
    source_priority: Dict[EventSource, int] = field(default_factory=lambda: {
        EventSource.ALERT: 0,
        EventSource.NOTE: 1,
        EventSource.LOG: 2,
    })
    dedup_similarity_threshold: float = 0.8
    include_source_in_dedup: bool = True
    log_line_pattern: str = r'^(?P<timestamp>\S+(?:\s+\S+)?)\s+(?P<severity>[A-Z]+)\s+(?P<message>.*)$'
    csv_time_column: str = "timestamp"
    csv_message_column: str = "message"
    csv_severity_column: str = "severity"

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["severity_mapping"] = {k: v.value for k, v in self.severity_mapping.items()}
        d["source_priority"] = {k.value: v for k, v in self.source_priority.items()}
        return d

    @classmethod
    def from_dict(cls, d: Dict) -> "RuleConfig":
        sev_map = {}
        for k, v in d.get("severity_mapping", {}).items():
            sev_map[k] = Severity(v)
        src_pri = {}
        for k, v in d.get("source_priority", {}).items():
            src_pri[EventSource(k)] = v
        return cls(
            rule_version=d.get("rule_version", "1.0.0"),
            dedup_window_seconds=d.get("dedup_window_seconds", 300),
            severity_mapping=sev_map,
            timestamp_formats=d.get("timestamp_formats", list(DEFAULT_TIMESTAMP_FORMATS)),
            gap_threshold_seconds=d.get("gap_threshold_seconds", 600),
            phases=d.get("phases", []),
            source_priority=src_pri,
            dedup_similarity_threshold=d.get("dedup_similarity_threshold", 0.8),
            include_source_in_dedup=d.get("include_source_in_dedup", True),
            log_line_pattern=d.get("log_line_pattern", r'^(?P<timestamp>\S+(?:\s+\S+)?)\s+(?P<severity>[A-Z]+)\s+(?P<message>.*)$'),
            csv_time_column=d.get("csv_time_column", "timestamp"),
            csv_message_column=d.get("csv_message_column", "message"),
            csv_severity_column=d.get("csv_severity_column", "severity"),
        )

    def get_dedup_timedelta(self) -> timedelta:
        return timedelta(seconds=self.dedup_window_seconds)

    def get_gap_timedelta(self) -> timedelta:
        return timedelta(seconds=self.gap_threshold_seconds)

    def map_severity(self, raw: Optional[str]) -> Severity:
        if raw is None:
            return Severity.INFO
        if raw in self.severity_mapping:
            return self.severity_mapping[raw]
        stripped = raw.strip()
        if stripped in self.severity_mapping:
            return self.severity_mapping[stripped]
        upper = stripped.upper()
        if upper in self.severity_mapping:
            return self.severity_mapping[upper]
        return Severity.INFO

    def add_severity_mapping(self, raw: str, severity: Severity) -> None:
        self.severity_mapping[raw] = severity

    def add_timestamp_format(self, fmt: str) -> None:
        if fmt not in self.timestamp_formats:
            self.timestamp_formats.append(fmt)

    def add_phase(self, name: str, start_time=None, end_time=None, description: str = "") -> None:
        self.phases.append({
            "name": name,
            "start_time": start_time.isoformat() if start_time else None,
            "end_time": end_time.isoformat() if end_time else None,
            "description": description,
        })
