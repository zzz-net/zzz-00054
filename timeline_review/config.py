from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any
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
class AuditRuleConfig:
    enabled: bool = True
    check_empty_export: bool = True
    check_event_count_mismatch: bool = True
    check_duplicate_restore: bool = True
    check_import_conflict: bool = True
    allow_force_reimport: bool = True
    empty_export_tolerance: int = 0
    count_mismatch_tolerance: int = 0
    auto_fix_snapshot: bool = False
    log_to_change_log: bool = True
    log_level: str = "info"
    export_count_patterns: List[str] = field(default_factory=lambda: [
        "总事件数:",
        "事件总数:",
        "事件数:",
        "event_count:",
        "total_events:",
    ])
    additional_checks: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict) -> "AuditRuleConfig":
        return cls(
            enabled=d.get("enabled", True),
            check_empty_export=d.get("check_empty_export", True),
            check_event_count_mismatch=d.get("check_event_count_mismatch", True),
            check_duplicate_restore=d.get("check_duplicate_restore", True),
            check_import_conflict=d.get("check_import_conflict", True),
            allow_force_reimport=d.get("allow_force_reimport", True),
            empty_export_tolerance=d.get("empty_export_tolerance", 0),
            count_mismatch_tolerance=d.get("count_mismatch_tolerance", 0),
            auto_fix_snapshot=d.get("auto_fix_snapshot", False),
            log_to_change_log=d.get("log_to_change_log", True),
            log_level=d.get("log_level", "info"),
            export_count_patterns=d.get("export_count_patterns", [
                "总事件数:",
                "事件总数:",
                "事件数:",
                "event_count:",
                "total_events:",
            ]),
            additional_checks=d.get("additional_checks", {}),
        )

    def is_check_enabled(self, check_name: str) -> bool:
        check_map = {
            "empty_export": self.check_empty_export,
            "event_count_mismatch": self.check_event_count_mismatch,
            "duplicate_restore": self.check_duplicate_restore,
            "import_conflict": self.check_import_conflict,
        }
        return check_map.get(check_name, False)

    def get_tolerance(self, check_name: str) -> int:
        tol_map = {
            "empty_export": self.empty_export_tolerance,
            "event_count_mismatch": self.count_mismatch_tolerance,
        }
        return tol_map.get(check_name, 0)


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
    audit_rules: AuditRuleConfig = field(default_factory=AuditRuleConfig)

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["severity_mapping"] = {k: v.value for k, v in self.severity_mapping.items()}
        d["source_priority"] = {k.value: v for k, v in self.source_priority.items()}
        d["audit_rules"] = self.audit_rules.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: Dict) -> "RuleConfig":
        sev_map = {}
        for k, v in d.get("severity_mapping", {}).items():
            sev_map[k] = Severity(v)
        src_pri = {}
        for k, v in d.get("source_priority", {}).items():
            src_pri[EventSource(k)] = v
        audit_rules = AuditRuleConfig.from_dict(d.get("audit_rules", {})) if d.get("audit_rules") else AuditRuleConfig()
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
            audit_rules=audit_rules,
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
