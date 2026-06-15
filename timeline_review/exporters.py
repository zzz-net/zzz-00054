import csv
import io
from datetime import datetime
from typing import Dict, List, Optional

from .models import Event, EventStatus, Severity, SEVERITY_ORDER
from .timeline import Timeline
from .config import RuleConfig


SEVERITY_ICONS = {
    Severity.DEBUG: "🔍",
    Severity.INFO: "ℹ️",
    Severity.WARNING: "⚠️",
    Severity.ERROR: "❌",
    Severity.CRITICAL: "🔥",
    Severity.FATAL: "💀",
}


STATUS_ICONS = {
    EventStatus.UNCONFIRMED: "❓",
    EventStatus.CONFIRMED: "✅",
    EventStatus.ROOT_CAUSE: "🎯",
    EventStatus.NOISE: "🔇",
}


class MarkdownExporter:
    def __init__(self, timeline: Timeline, config: RuleConfig, batch_meta: Optional[Dict] = None,
                 history_data: Optional[Dict] = None):
        self.timeline = timeline
        self.config = config
        self.batch_meta = batch_meta or {}
        self.history_data = history_data or {}

    def _format_header(self) -> str:
        lines = []
        lines.append(f"# 事件时间线复盘报告")
        lines.append("")
        if self.batch_meta:
            lines.append(f"**批次名称**: {self.batch_meta.get('name', 'N/A')}")
            lines.append(f"**批次 ID**: {self.batch_meta.get('id', 'N/A')}")
            if self.batch_meta.get("description"):
                lines.append(f"**描述**: {self.batch_meta['description']}")
            lines.append(f"**创建时间**: {self.batch_meta.get('created_at', 'N/A')}")
        lines.append(f"**规则版本**: {self.config.rule_version}")
        lines.append(f"**导出时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")
        return "\n".join(lines)

    def _format_stats(self) -> str:
        stats = self.timeline.get_stats()
        lines = []
        lines.append("## 📊 统计概览")
        lines.append("")
        lines.append(f"- **总事件数**: {stats['total']}")
        if stats["start_time"] and stats["end_time"]:
            lines.append(f"- **时间范围**: {stats['start_time']} ~ {stats['end_time']}")
        lines.append(f"- **时间缺口数**: {stats['gap_count']}")
        lines.append(f"- **阶段数**: {stats['phase_count']}")
        lines.append("")
        lines.append("### 按状态分布")
        lines.append("")
        for status, count in stats["by_status"].items():
            icon = " "
            for s, i in STATUS_ICONS.items():
                if s.value == status:
                    icon = i
                    break
            lines.append(f"- {icon} {status}: {count}")
        lines.append("")
        lines.append("### 按严重级别分布")
        lines.append("")
        sorted_sev = sorted(stats["by_severity"].items(),
                            key=lambda x: SEVERITY_ORDER.get(Severity(x[0]), 0), reverse=True)
        for sev, count in sorted_sev:
            icon = SEVERITY_ICONS.get(Severity(sev), " ")
            lines.append(f"- {icon} {sev}: {count}")
        lines.append("")
        lines.append("### 按来源分布")
        lines.append("")
        for src, count in stats["by_source"].items():
            src_label = {"log": "应用日志", "alert": "告警", "note": "人工备注"}.get(src, src)
            lines.append(f"- 📁 {src_label}: {count}")
        lines.append("")
        return "\n".join(lines)

    def _format_gaps(self) -> str:
        gaps = self.timeline.get_gaps()
        if not gaps:
            return ""
        lines = []
        lines.append("## ⏱️ 时间缺口")
        lines.append("")
        lines.append(f"超过阈值 ({self.config.gap_threshold_seconds}s) 的时间间隔:")
        lines.append("")
        lines.append("| # | 开始时间 | 结束时间 | 间隔 |")
        lines.append("|---|---------|---------|------|")
        for i, (start, end, diff) in enumerate(gaps, 1):
            hours, remainder = divmod(int(diff.total_seconds()), 3600)
            minutes, seconds = divmod(remainder, 60)
            duration_parts = []
            if hours > 0:
                duration_parts.append(f"{hours}h")
            if minutes > 0:
                duration_parts.append(f"{minutes}m")
            duration_parts.append(f"{seconds}s")
            lines.append(f"| {i} | {start.strftime('%Y-%m-%d %H:%M:%S')} | {end.strftime('%Y-%m-%d %H:%M:%S')} | {''.join(duration_parts)} |")
        lines.append("")
        return "\n".join(lines)

    def _format_phases(self) -> str:
        if not self.timeline.phases:
            return ""
        lines = []
        lines.append("## 🎬 事件阶段")
        lines.append("")
        lines.append("| 阶段 | 开始时间 | 结束时间 | 描述 |")
        lines.append("|-----|---------|---------|------|")
        for phase in self.timeline.phases:
            start = phase.start_time.strftime("%Y-%m-%d %H:%M:%S") if phase.start_time else "-"
            end = phase.end_time.strftime("%Y-%m-%d %H:%M:%S") if phase.end_time else "-"
            lines.append(f"| {phase.name} | {start} | {end} | {phase.description} |")
        lines.append("")
        return "\n".join(lines)

    def _format_event_row(self, event: Event, idx: int) -> str:
        sev_icon = SEVERITY_ICONS.get(event.severity, " ")
        status_icon = STATUS_ICONS.get(event.status, " ")
        src_label = {"log": "日志", "alert": "告警", "note": "备注"}.get(event.source.value, event.source.value)
        lines = []
        lines.append(f"### {idx}. {sev_icon} {status_icon} [{event.severity.value}] {event.message[:80]}{'...' if len(event.message) > 80 else ''}")
        lines.append("")
        lines.append(f"- **时间**: {event.timestamp.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
        lines.append(f"- **ID**: `{event.id}`")
        lines.append(f"- **来源**: {src_label} (`{event.source_file}`:{event.line_number})")
        lines.append(f"- **状态**: {event.status.value}")
        phase = self.timeline.get_phase(event.timestamp)
        if phase:
            lines.append(f"- **阶段**: {phase.name}")
        lines.append("")
        lines.append(f"**详细信息**:")
        lines.append("")
        lines.append(f"> {event.message}")
        lines.append("")
        if event.notes:
            lines.append(f"**备注**: {event.notes}")
            lines.append("")
        if event.raw_events and len(event.raw_events) > 1:
            lines.append(f"**合并来源** ({len(event.raw_events)} 条):")
            lines.append("")
            for re in event.raw_events:
                lines.append(f"- `{re.source_file}`:{re.line_number}")
            lines.append("")
        if event.extra:
            lines.append("**扩展字段**:")
            lines.append("")
            for k, v in event.extra.items():
                lines.append(f"- `{k}`: {v}")
            lines.append("")
        lines.append("---")
        lines.append("")
        return "\n".join(lines)

    def _format_events_by_group(self) -> str:
        lines = []
        lines.append("## 📋 事件详情")
        lines.append("")
        if self.timeline.phases:
            grouped = self.timeline.group_by_phase()
            for phase_name, events in grouped.items():
                if not events:
                    continue
                lines.append(f"### {phase_name}")
                lines.append("")
                lines.append(f"共 {len(events)} 条事件")
                lines.append("")
                for idx, event in enumerate(sorted(events, key=lambda e: e.timestamp), 1):
                    lines.append(self._format_event_row(event, idx))
        else:
            grouped = self.timeline.group_by_date()
            for date_str, events in grouped.items():
                lines.append(f"### 📅 {date_str}")
                lines.append("")
                lines.append(f"共 {len(events)} 条事件")
                lines.append("")
                for idx, event in enumerate(events, 1):
                    lines.append(self._format_event_row(event, idx))
        return "\n".join(lines)

    def _format_config(self) -> str:
        lines = []
        lines.append("## ⚙️ 规则配置")
        lines.append("")
        lines.append(f"- **规则版本**: {self.config.rule_version}")
        lines.append(f"- **去重时间窗口**: {self.config.dedup_window_seconds}s")
        lines.append(f"- **缺口阈值**: {self.config.gap_threshold_seconds}s")
        lines.append(f"- **去重相似度阈值**: {self.config.dedup_similarity_threshold}")
        lines.append("")
        return "\n".join(lines)

    def _format_history_summary(self) -> str:
        if not self.history_data:
            return ""

        trigger_labels = {
            "create": "创建批次",
            "import": "导入数据",
            "reimport": "重新导入",
            "undo_import": "撤销导入",
            "config": "配置变更",
            "label": "标注事件",
            "undo_label": "撤销标注",
            "export": "导出报告",
            "manual": "手动刷新",
            "repair": "修复快照",
            "restore": "恢复快照",
        }

        lines = []
        lines.append("## 📜 操作历史摘要")
        lines.append("")

        rounds = self.history_data.get("rounds", [])
        if rounds:
            lines.append(f"共执行 **{len(rounds)}** 轮操作:")
            lines.append("")
            lines.append("| 轮次 | 触发原因 | 事件数(前→后) | 导入文件(前→后) | 规则版本 | 时间 |")
            lines.append("|-----|---------|--------------|----------------|---------|------|")
            for r in rounds:
                trigger = trigger_labels.get(r.get("trigger", "unknown"), r.get("trigger", "unknown"))
                ev_before = r.get("before_event_count", 0)
                ev_after = r.get("after_event_count", 0)
                ev_change = ev_after - ev_before
                ev_change_str = f"+{ev_change}" if ev_change > 0 else str(ev_change)
                imp_before = r.get("before_import_count", 0)
                imp_after = r.get("after_import_count", 0)
                imp_change = imp_after - imp_before
                imp_change_str = f"+{imp_change}" if imp_change > 0 else str(imp_change)
                created_at = r.get("created_at", "").replace("T", " ")[:19]
                rule_ver = r.get("rule_version", "unknown")
                lines.append(f"| {r.get('round_number', 0)} | {trigger} | {ev_before}→{ev_after}({ev_change_str}) | {imp_before}→{imp_after}({imp_change_str}) | {rule_ver} | {created_at} |")
            lines.append("")

        recent_changes = self.history_data.get("recent_changes", [])
        if recent_changes:
            lines.append("### 主要变更摘要")
            lines.append("")
            for change in recent_changes[:10]:
                lines.append(f"- {change}")
            lines.append("")

        exports = self.history_data.get("exports", [])
        if exports:
            lines.append("### 导出历史")
            lines.append("")
            lines.append("| # | 文件名 | 大小 | 导出时间 |")
            lines.append("|---|-------|------|---------|")
            for i, exp in enumerate(exports[:5], 1):
                modified_at = exp.get("modified_at", "").replace("T", " ")[:19]
                lines.append(f"| {i} | {exp.get('filename', '')} | {exp.get('size', 0)} 字节 | {modified_at} |")
            lines.append("")

        consistency = self.history_data.get("consistency", {})
        if consistency:
            lines.append("### 数据一致性检查")
            lines.append("")
            if consistency.get("consistent", False):
                lines.append("✅ 数据一致性检查通过")
            else:
                lines.append("⚠️  **警告**: 检测到数据不一致")
                inconsistencies = consistency.get("inconsistencies", [])
                if inconsistencies:
                    lines.append("")
                    lines.append("| 字段 | 快照值 | 真实值 | 差异 |")
                    lines.append("|-----|-------|-------|------|")
                    for inc in inconsistencies:
                        diff = inc.get("diff", 0)
                        diff_str = f"+{diff}" if diff > 0 else str(diff)
                        lines.append(f"| {inc.get('field', '')} | {inc.get('snapshot', 0)} | {inc.get('real', 0)} | {diff_str} |")
            lines.append("")

        return "\n".join(lines)

    def export(self) -> str:
        parts = []
        parts.append(self._format_header())
        parts.append(self._format_stats())
        history_section = self._format_history_summary()
        if history_section:
            parts.append(history_section)
        phases_section = self._format_phases()
        if phases_section:
            parts.append(phases_section)
        gaps_section = self._format_gaps()
        if gaps_section:
            parts.append(gaps_section)
        parts.append(self._format_events_by_group())
        parts.append(self._format_config())
        return "\n".join(parts)


class CSVExporter:
    def __init__(self, timeline: Timeline, config: RuleConfig, batch_meta: Optional[Dict] = None,
                 history_data: Optional[Dict] = None):
        self.timeline = timeline
        self.config = config
        self.batch_meta = batch_meta or {}
        self.history_data = history_data or {}

    def _format_history_summary_csv(self) -> str:
        if not self.history_data:
            return ""
        import csv
        import io
        output = io.StringIO()
        output.write("\n\n# === 操作历史摘要 ===\n")

        rounds = self.history_data.get("rounds", [])
        if rounds:
            output.write(f"# 总操作轮次: {len(rounds)}\n")
            writer = csv.writer(output)
            writer.writerow(["轮次", "触发原因", "事件数(前→后)", "导入文件(前→后)", "规则版本", "时间"])
            for r in rounds:
                ev_before = r.get("before_event_count", 0)
                ev_after = r.get("after_event_count", 0)
                ev_change = ev_after - ev_before
                ev_change_str = f"+{ev_change}" if ev_change > 0 else str(ev_change)
                imp_before = r.get("before_import_count", 0)
                imp_after = r.get("after_import_count", 0)
                imp_change = imp_after - imp_before
                imp_change_str = f"+{imp_change}" if imp_change > 0 else str(imp_change)
                created_at = r.get("created_at", "").replace("T", " ")[:19]
                writer.writerow([
                    r.get("round_number", 0),
                    r.get("trigger", "unknown"),
                    f"{ev_before}→{ev_after}({ev_change_str})",
                    f"{imp_before}→{imp_after}({imp_change_str})",
                    r.get("rule_version", "unknown"),
                    created_at,
                ])
        return output.getvalue()

    def export(self) -> str:
        output = io.StringIO()
        fieldnames = [
            "id",
            "timestamp",
            "source",
            "source_file",
            "line_number",
            "severity",
            "status",
            "phase",
            "message",
            "notes",
            "raw_event_count",
            "dedup_key",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        events = self.timeline.sort()
        for event in events:
            phase = self.timeline.get_phase(event.timestamp)
            writer.writerow({
                "id": event.id,
                "timestamp": event.timestamp.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                "source": event.source.value,
                "source_file": event.source_file,
                "line_number": event.line_number,
                "severity": event.severity.value,
                "status": event.status.value,
                "phase": phase.name if phase else "",
                "message": event.message,
                "notes": event.notes,
                "raw_event_count": len(event.raw_events),
                "dedup_key": event.dedup_key,
            })
        history_summary = self._format_history_summary_csv()
        if history_summary:
            output.write(history_summary)
        return output.getvalue()


def export_report(timeline: Timeline, config: RuleConfig, format: str,
                  batch_meta: Optional[Dict] = None,
                  history_data: Optional[Dict] = None) -> str:
    if format.lower() == "csv":
        exporter = CSVExporter(timeline, config, batch_meta, history_data)
    else:
        exporter = MarkdownExporter(timeline, config, batch_meta, history_data)
    return exporter.export()
