import re
import csv
import json
import hashlib
from pathlib import Path
from datetime import datetime
from typing import List, Tuple, Optional, Dict, Any

from .models import RawEvent, ParseError, EventSource, Event, Severity
from .config import RuleConfig


def parse_timestamp(raw: str, formats: List[str]) -> Optional[datetime]:
    if not raw:
        return None
    raw = raw.strip().strip('"').strip("'")
    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        pass
    try:
        if raw.endswith("Z"):
            return datetime.fromisoformat(raw[:-1])
    except ValueError:
        pass
    return None


def compute_file_hash(file_path: str) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


class LogParser:
    def __init__(self, config: RuleConfig):
        self.config = config

    def parse(self, file_path: str) -> Tuple[List[RawEvent], List[ParseError]]:
        raw_events: List[RawEvent] = []
        errors: List[ParseError] = []
        path = Path(file_path)
        filename = path.name
        pattern = re.compile(self.config.log_line_pattern)

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, start=1):
                    line = line.rstrip("\n")
                    if not line.strip():
                        continue
                    match = pattern.match(line)
                    if match:
                        timestamp_raw = match.group("timestamp")
                        severity_raw = match.group("severity")
                        message = match.group("message")
                    else:
                        parts = line.split(None, 2)
                        if len(parts) >= 2:
                            timestamp_raw = parts[0]
                            severity_raw = parts[1] if len(parts) > 1 else None
                            message = parts[2] if len(parts) > 2 else ""
                        else:
                            errors.append(ParseError(
                                source_file=filename,
                                line_number=line_num,
                                error_type="format_error",
                                error_message="无法解析日志行格式",
                                raw_content=line,
                            ))
                            continue

                    ts = parse_timestamp(timestamp_raw, self.config.timestamp_formats)
                    if ts is None:
                        errors.append(ParseError(
                            source_file=filename,
                            line_number=line_num,
                            error_type="timestamp_error",
                            error_message=f"无法识别的时间格式: {timestamp_raw}",
                            raw_content=line,
                        ))
                        continue

                    raw_events.append(RawEvent(
                        source=EventSource.LOG,
                        source_file=filename,
                        line_number=line_num,
                        timestamp_raw=timestamp_raw,
                        severity_raw=severity_raw,
                        message=message.strip(),
                    ))
        except IOError as e:
            errors.append(ParseError(
                source_file=filename,
                line_number=0,
                error_type="io_error",
                error_message=f"文件读取失败: {str(e)}",
            ))

        return raw_events, errors


class CSVParser:
    def __init__(self, config: RuleConfig):
        self.config = config

    def parse(self, file_path: str) -> Tuple[List[RawEvent], List[ParseError]]:
        raw_events: List[RawEvent] = []
        errors: List[ParseError] = []
        path = Path(file_path)
        filename = path.name

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                try:
                    reader = csv.DictReader(f)
                except Exception as e:
                    errors.append(ParseError(
                        source_file=filename,
                        line_number=1,
                        error_type="csv_format_error",
                        error_message=f"CSV 解析失败: {str(e)}",
                    ))
                    return raw_events, errors

                fieldnames = reader.fieldnames or []
                ts_col = self.config.csv_time_column
                msg_col = self.config.csv_message_column
                sev_col = self.config.csv_severity_column

                if ts_col not in fieldnames:
                    for alt in ["time", "date", "datetime", "created_at", "时间", "告警时间"]:
                        if alt in fieldnames:
                            ts_col = alt
                            break

                if msg_col not in fieldnames:
                    for alt in ["content", "description", "detail", "msg", "内容", "告警内容"]:
                        if alt in fieldnames:
                            msg_col = alt
                            break

                for line_num, row in enumerate(reader, start=2):
                    timestamp_raw = str(row.get(ts_col, "")) if row.get(ts_col) else ""
                    message = str(row.get(msg_col, "")) if row.get(msg_col) else ""
                    severity_raw = str(row.get(sev_col, "")) if row.get(sev_col) else None

                    if not timestamp_raw and not message:
                        errors.append(ParseError(
                            source_file=filename,
                            line_number=line_num,
                            error_type="empty_row",
                            error_message="空行或缺少必要字段",
                            raw_content=json.dumps(row, ensure_ascii=False),
                        ))
                        continue

                    ts = parse_timestamp(timestamp_raw, self.config.timestamp_formats)
                    if ts is None:
                        errors.append(ParseError(
                            source_file=filename,
                            line_number=line_num,
                            error_type="timestamp_error",
                            error_message=f"无法识别的时间格式: {timestamp_raw}",
                            raw_content=json.dumps(row, ensure_ascii=False),
                        ))
                        continue

                    extra = {k: v for k, v in row.items()
                             if k not in [ts_col, msg_col, sev_col] and v}
                    raw_events.append(RawEvent(
                        source=EventSource.ALERT,
                        source_file=filename,
                        line_number=line_num,
                        timestamp_raw=timestamp_raw,
                        severity_raw=severity_raw,
                        message=message.strip(),
                        extra=extra,
                    ))
        except IOError as e:
            errors.append(ParseError(
                source_file=filename,
                line_number=0,
                error_type="io_error",
                error_message=f"文件读取失败: {str(e)}",
            ))

        return raw_events, errors


class JSONParser:
    def __init__(self, config: RuleConfig):
        self.config = config

    def parse(self, file_path: str) -> Tuple[List[RawEvent], List[ParseError]]:
        raw_events: List[RawEvent] = []
        errors: List[ParseError] = []
        path = Path(file_path)
        filename = path.name

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except IOError as e:
            errors.append(ParseError(
                source_file=filename,
                line_number=0,
                error_type="io_error",
                error_message=f"文件读取失败: {str(e)}",
            ))
            return raw_events, errors

        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            errors.append(ParseError(
                source_file=filename,
                line_number=e.lineno if hasattr(e, "lineno") else 0,
                error_type="json_format_error",
                error_message=f"JSON 解析失败: {str(e)}",
            ))
            return raw_events, errors

        if isinstance(data, dict):
            if "notes" in data and isinstance(data["notes"], list):
                items = data["notes"]
            elif "events" in data and isinstance(data["events"], list):
                items = data["events"]
            else:
                items = [data]
        elif isinstance(data, list):
            items = data
        else:
            errors.append(ParseError(
                source_file=filename,
                line_number=1,
                error_type="json_structure_error",
                error_message="JSON 顶层结构必须是对象或数组",
            ))
            return raw_events, errors

        for idx, item in enumerate(items):
            line_num = idx + 1
            if not isinstance(item, dict):
                errors.append(ParseError(
                    source_file=filename,
                    line_number=line_num,
                    error_type="item_type_error",
                    error_message=f"备注项必须是对象，实际类型: {type(item).__name__}",
                    raw_content=str(item),
                ))
                continue

            timestamp_raw = ""
            for key in ["timestamp", "time", "date", "datetime", "created_at", "时间"]:
                if key in item and item[key]:
                    timestamp_raw = str(item[key])
                    break

            message = ""
            for key in ["message", "content", "note", "description", "备注", "内容"]:
                if key in item and item[key]:
                    message = str(item[key])
                    break

            severity_raw = None
            for key in ["severity", "level", "级别", "严重程度"]:
                if key in item and item[key]:
                    severity_raw = str(item[key])
                    break

            if not timestamp_raw and not message:
                errors.append(ParseError(
                    source_file=filename,
                    line_number=line_num,
                    error_type="missing_fields",
                    error_message="缺少时间和内容字段",
                    raw_content=json.dumps(item, ensure_ascii=False),
                ))
                continue

            ts = parse_timestamp(timestamp_raw, self.config.timestamp_formats)
            if ts is None and timestamp_raw:
                errors.append(ParseError(
                    source_file=filename,
                    line_number=line_num,
                    error_type="timestamp_error",
                    error_message=f"无法识别的时间格式: {timestamp_raw}",
                    raw_content=json.dumps(item, ensure_ascii=False),
                ))
                continue
            if ts is None:
                ts = datetime.now()
                timestamp_raw = ts.isoformat()

            extra = {k: v for k, v in item.items()
                     if k not in ["timestamp", "time", "date", "datetime", "created_at",
                                  "message", "content", "note", "description",
                                  "severity", "level", "级别", "严重程度"] and v}
            raw_events.append(RawEvent(
                source=EventSource.NOTE,
                source_file=filename,
                line_number=line_num,
                timestamp_raw=timestamp_raw,
                severity_raw=severity_raw,
                message=message.strip(),
                extra=extra,
            ))

        return raw_events, errors


def get_parser_by_extension(file_path: str, config: RuleConfig):
    ext = Path(file_path).suffix.lower()
    if ext == ".csv":
        return CSVParser(config)
    elif ext == ".json":
        return JSONParser(config)
    else:
        return LogParser(config)


def get_parser_by_type(file_type: str, config: RuleConfig):
    if file_type == "csv":
        return CSVParser(config)
    elif file_type == "json":
        return JSONParser(config)
    else:
        return LogParser(config)


def raw_events_to_events(raw_events: List[RawEvent], config: RuleConfig) -> List[Event]:
    events = []
    for re in raw_events:
        ts = parse_timestamp(re.timestamp_raw, config.timestamp_formats)
        if ts is None:
            continue
        severity = config.map_severity(re.severity_raw)
        event_id = Event.generate_id(ts, re.source, re.message, severity)
        dedup_key_parts = [re.message, severity.value]
        if config.include_source_in_dedup:
            dedup_key_parts.insert(0, re.source.value)
        dedup_key = hashlib.sha256("|".join(dedup_key_parts).encode("utf-8")).hexdigest()[:16]
        events.append(Event(
            id=event_id,
            timestamp=ts,
            source=re.source,
            source_file=re.source_file,
            line_number=re.line_number,
            severity=severity,
            message=re.message,
            raw_events=[re],
            extra=re.extra,
            dedup_key=dedup_key,
        ))
    return events
