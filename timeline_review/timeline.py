from datetime import datetime, timedelta
from typing import List, Tuple, Dict, Optional
from difflib import SequenceMatcher

from .models import Event, EventStatus, Severity, SEVERITY_ORDER, Phase
from .config import RuleConfig


def text_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def merge_events(primary: Event, secondary: Event, config: RuleConfig) -> Event:
    for re in secondary.raw_events:
        exists = any(
            r.source_file == re.source_file and r.line_number == re.line_number
            for r in primary.raw_events
        )
        if not exists:
            primary.raw_events.append(re)
    if SEVERITY_ORDER.get(secondary.severity, 0) > SEVERITY_ORDER.get(primary.severity, 0):
        primary.severity = secondary.severity
    if config.source_priority.get(secondary.source, 99) < config.source_priority.get(primary.source, 99):
        primary.source = secondary.source
        primary.source_file = secondary.source_file
        primary.line_number = secondary.line_number
    if secondary.timestamp < primary.timestamp:
        primary.timestamp = secondary.timestamp
    if secondary.extra:
        for k, v in secondary.extra.items():
            if k not in primary.extra:
                primary.extra[k] = v
    primary.updated_at = datetime.now()
    return primary


def dedupe_events(events: List[Event], config: RuleConfig) -> Tuple[List[Event], List[Tuple[str, str]]]:
    if not events:
        return [], []

    window = config.get_dedup_timedelta()
    threshold = config.dedup_similarity_threshold

    sorted_events = sorted(events, key=lambda e: e.timestamp)
    merged: Dict[str, Event] = {}
    merged_ids: List[Tuple[str, str]] = []

    for event in sorted_events:
        matched = None
        for existing_id, existing in merged.items():
            if abs((event.timestamp - existing.timestamp).total_seconds()) > window.total_seconds():
                continue
            if event.dedup_key == existing.dedup_key:
                matched = existing_id
                break
            if text_similarity(event.message, existing.message) >= threshold:
                if event.severity == existing.severity:
                    matched = existing_id
                    break
        if matched:
            existing_event = merged[matched]
            merged[matched] = merge_events(existing_event, event, config)
            merged_ids.append((event.id, matched))
        else:
            merged[event.id] = event

    result = sorted(merged.values(), key=lambda e: e.timestamp)
    return result, merged_ids


class Timeline:
    def __init__(self, events: List[Event], config: RuleConfig):
        self.config = config
        self.events = sorted(events, key=lambda e: e.timestamp)
        self.phases: List[Phase] = []
        self._load_phases()

    def _load_phases(self) -> None:
        for p_data in self.config.phases:
            try:
                self.phases.append(Phase.from_dict(p_data))
            except Exception:
                continue

    def get_phase(self, t: datetime) -> Optional[Phase]:
        for phase in self.phases:
            if phase.contains(t):
                return phase
        return None

    def sort(self, reverse: bool = False) -> List[Event]:
        return sorted(self.events, key=lambda e: e.timestamp, reverse=reverse)

    def sort_by_severity(self, reverse: bool = True) -> List[Event]:
        return sorted(
            self.events,
            key=lambda e: (SEVERITY_ORDER.get(e.severity, 0), e.timestamp),
            reverse=reverse,
        )

    def filter_by_status(self, statuses: List[EventStatus]) -> "Timeline":
        filtered = [e for e in self.events if e.status in statuses]
        return Timeline(filtered, self.config)

    def filter_by_severity(self, severities: List[Severity]) -> "Timeline":
        filtered = [e for e in self.events if e.severity in severities]
        return Timeline(filtered, self.config)

    def filter_by_source(self, sources) -> "Timeline":
        filtered = [e for e in self.events if e.source in sources]
        return Timeline(filtered, self.config)

    def filter_by_time_range(self, start: Optional[datetime] = None, end: Optional[datetime] = None) -> "Timeline":
        filtered = []
        for e in self.events:
            if start and e.timestamp < start:
                continue
            if end and e.timestamp > end:
                continue
            filtered.append(e)
        return Timeline(filtered, self.config)

    def search(self, keyword: str) -> "Timeline":
        kw = keyword.lower()
        filtered = []
        for e in self.events:
            if kw in e.message.lower() or kw in e.notes.lower():
                filtered.append(e)
            for k, v in e.extra.items():
                if kw in str(k).lower() or kw in str(v).lower():
                    filtered.append(e)
                    break
        return Timeline(filtered, self.config)

    def get_gaps(self) -> List[Tuple[datetime, datetime, timedelta]]:
        gaps = []
        threshold = self.config.get_gap_timedelta()
        sorted_events = self.sort()
        for i in range(1, len(sorted_events)):
            prev = sorted_events[i - 1].timestamp
            curr = sorted_events[i].timestamp
            diff = curr - prev
            if diff > threshold:
                gaps.append((prev, curr, diff))
        return gaps

    def get_time_range(self) -> Tuple[Optional[datetime], Optional[datetime]]:
        if not self.events:
            return None, None
        sorted_events = self.sort()
        return sorted_events[0].timestamp, sorted_events[-1].timestamp

    def get_stats(self) -> Dict:
        total = len(self.events)
        by_status: Dict[str, int] = {}
        by_severity: Dict[str, int] = {}
        by_source: Dict[str, int] = {}

        for e in self.events:
            s = e.status.value
            by_status[s] = by_status.get(s, 0) + 1
            sev = e.severity.value
            by_severity[sev] = by_severity.get(sev, 0) + 1
            src = e.source.value
            by_source[src] = by_source.get(src, 0) + 1

        start, end = self.get_time_range()
        gaps = self.get_gaps()

        return {
            "total": total,
            "by_status": by_status,
            "by_severity": by_severity,
            "by_source": by_source,
            "start_time": start.isoformat() if start else None,
            "end_time": end.isoformat() if end else None,
            "gap_count": len(gaps),
            "phase_count": len(self.phases),
        }

    def group_by_phase(self) -> Dict[str, List[Event]]:
        grouped: Dict[str, List[Event]] = {"未分类": []}
        for phase in self.phases:
            grouped[phase.name] = []
        for e in self.events:
            phase = self.get_phase(e.timestamp)
            if phase:
                grouped[phase.name].append(e)
            else:
                grouped["未分类"].append(e)
        return grouped

    def group_by_date(self) -> Dict[str, List[Event]]:
        grouped: Dict[str, List[Event]] = {}
        for e in self.events:
            date_str = e.timestamp.strftime("%Y-%m-%d")
            if date_str not in grouped:
                grouped[date_str] = []
            grouped[date_str].append(e)
        return dict(sorted(grouped.items()))

    def group_by_hour(self) -> Dict[str, List[Event]]:
        grouped: Dict[str, List[Event]] = {}
        for e in self.events:
            hour_str = e.timestamp.strftime("%Y-%m-%d %H:00")
            if hour_str not in grouped:
                grouped[hour_str] = []
            grouped[hour_str].append(e)
        return dict(sorted(grouped.items()))
