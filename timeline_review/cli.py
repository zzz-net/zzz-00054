import argparse
import sys
import os
import io
import copy
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Dict, Tuple

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from .storage import StateStore, BatchNotFoundError
from .config import RuleConfig
from .models import Event, EventStatus, Severity, EventSource
from .importers import (
    get_parser_by_extension,
    get_parser_by_type,
    raw_events_to_events,
    compute_file_hash,
)
from .timeline import Timeline, dedupe_events
from .exporters import export_report, SEVERITY_ICONS, STATUS_ICONS


class CLIApp:
    def __init__(self):
        self.store = StateStore()
        self.config = RuleConfig()

    def _require_batch(self) -> str:
        batch_id = self.store.get_active_batch()
        if not batch_id:
            print("❌ 没有活动批次，请先使用 'create' 创建批次或 'switch' 切换批次", file=sys.stderr)
            sys.exit(1)
        self.config = self.store.load_config(batch_id)
        try:
            rebuild = self.store.rebuild_state_after_restart(batch_id)
            if rebuild.get("actions"):
                self.store.log_change(batch_id, "cli_require_batch_rebuild", {
                    "actions": rebuild.get("actions"),
                }, severity="debug")
        except Exception:
            pass
        return batch_id

    def _print_event_short(self, event: Event, idx: int = 0) -> None:
        sev_icon = SEVERITY_ICONS.get(event.severity, " ")
        status_icon = STATUS_ICONS.get(event.status, " ")
        src_label = {"log": "日志", "alert": "告警", "note": "备注"}.get(event.source.value, event.source.value)
        ts = event.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        msg = event.message[:60] + ("..." if len(event.message) > 60 else "")
        prefix = f"[{idx:>3}] " if idx > 0 else "      "
        print(f"{prefix}{sev_icon} {status_icon} {ts} [{event.severity.value:<8}] [{src_label:<4}] {msg}")
        print(f"        ID: {event.id}  状态: {event.status.value}")

    def cmd_create(self, args) -> None:
        meta = self.store.create_batch(args.name, args.description or "")
        print(f"✅ 已创建批次: {meta['name']} (ID: {meta['id']})")
        print(f"   存储路径: {self.store._get_batch_dir(meta['id'])}")

    def cmd_list(self, args) -> None:
        batches = self.store.list_batches()
        active = self.store.get_active_batch()
        if not batches:
            print("📭 没有找到任何批次")
            return
        print(f"📋 共找到 {len(batches)} 个批次:")
        print()
        for b in batches:
            marker = " ◀ 当前" if b["id"] == active else ""
            print(f"  [{b['id']}] {b['name']}{marker}")
            print(f"      创建: {b.get('created_at', 'N/A')}  事件: {b.get('event_count', 0)}  状态: {b.get('status', 'N/A')}")
            if b.get("description"):
                print(f"      描述: {b['description']}")
            print()

    def cmd_switch(self, args) -> None:
        try:
            meta = self.store.switch_batch(args.batch_id)
            print(f"✅ 已切换到批次: {meta['name']} (ID: {meta['id']})")
        except BatchNotFoundError as e:
            print(f"❌ {e}", file=sys.stderr)
            sys.exit(1)

    def cmd_status(self, args) -> None:
        batch_id = self._require_batch()
        meta = self.store.get_batch_meta(batch_id)
        events = self.store.load_events(batch_id)
        errors = self.store.load_parse_errors(batch_id)
        imported = self.store.get_imported_files(batch_id)
        config = self.store.load_config(batch_id)

        print(f"📊 批次: {meta['name']} (ID: {meta['id']})")
        if meta.get("description"):
            print(f"   描述: {meta['description']}")
        print(f"   创建时间: {meta.get('created_at', 'N/A')}")
        print(f"   更新时间: {meta.get('updated_at', 'N/A')}")
        print()
        print(f"📈 统计:")
        print(f"   事件总数: {len(events)}")
        by_status = {}
        by_severity = {}
        by_source = {}
        for e in events:
            by_status[e.status.value] = by_status.get(e.status.value, 0) + 1
            by_severity[e.severity.value] = by_severity.get(e.severity.value, 0) + 1
            by_source[e.source.value] = by_source.get(e.source.value, 0) + 1
        print(f"   按状态: {by_status}")
        print(f"   按严重级别: {by_severity}")
        print(f"   按来源: {by_source}")
        print()
        print(f"📁 已导入文件 ({len(imported)} 个):")
        for f in imported:
            print(f"   - {f['filename']} ({f['event_count']} 事件, {f['error_count']} 错误)")
        print()
        if errors:
            print(f"⚠️  解析错误 ({len(errors)} 个):")
            for err in errors[:10]:
                print(f"   - {err.source_file}:{err.line_number} [{err.error_type}] {err.error_message}")
            if len(errors) > 10:
                print(f"   ... 还有 {len(errors) - 10} 个错误")
        print()
        print(f"⚙️  规则版本: {config.rule_version}")
        print(f"   去重窗口: {config.dedup_window_seconds}s  缺口阈值: {config.gap_threshold_seconds}s")

    def cmd_overview(self, args) -> None:
        batch_id = self.store.get_active_batch()
        if not batch_id:
            print("📭 没有活动批次")
            print("   提示: 使用 'create' 创建批次，或 'switch' 切换到已有批次")
            return

        if args.rounds:
            self._print_rounds(batch_id, args.round_limit)
            return

        if args.round is not None:
            self._print_round_detail(batch_id, args.round)
            return

        if args.round_diff is not None:
            self._print_round_diff(batch_id, args.round_diff)
            return

        if args.history:
            self._print_snapshot_history(batch_id, args.history_limit)
            return

        if args.check_consistency:
            self._check_and_print_consistency(batch_id, args.fix)
            return

        if args.fix:
            self._fix_and_print_snapshot(batch_id)
            return

        if args.change_log:
            self._print_change_log(batch_id, args.log_limit, args.log_type)
            return

        if args.export_diff:
            self._print_export_diff(batch_id)
            return

        if args.compare_with:
            self._print_change_summary(batch_id, args.compare_with, args.refresh)
            return

        if args.refresh:
            snapshot = self.store.refresh_overview_snapshot(batch_id, trigger="manual")
            print("🔄 已强制刷新快照")
            print()
        else:
            try:
                snapshot = self.store.load_overview_snapshot(batch_id)
            except Exception as e:
                print(f"⚠️  概览数据加载失败，正在重建: {e}")
                snapshot = self.store.refresh_overview_snapshot(batch_id, trigger="repair")

        self._print_basic_overview(batch_id, snapshot)

    def _print_basic_overview(self, batch_id: str, snapshot: Dict) -> None:
        if not snapshot:
            print("⚠️  无法获取批次概览，批次目录可能已损坏")
            return

        warnings = []
        for key in ["_meta_error", "_events_error", "_parse_errors_error",
                    "_imports_error", "_config_error", "_exports_error",
                    "_label_history_error", "_write_error"]:
            if key in snapshot:
                warnings.append(f"   ⚠️  {key[1:]}: {snapshot[key]}")

        print("=" * 62)
        batch_name = snapshot.get("batch_name", "未知批次")
        batch_id_display = snapshot.get("batch_id", batch_id)
        print(f"📋 批次概览: {batch_name}")
        print(f"   批次 ID:  {batch_id_display}")
        if snapshot.get("description"):
            print(f"   描    述: {snapshot['description']}")
        if snapshot.get("created_at"):
            print(f"   创建时间: {snapshot['created_at']}")
        updated = snapshot.get("updated_at", "")
        if updated:
            print(f"   概览刷新: {updated}")
        print("=" * 62)

        print()
        print("📁 已导入数据:")
        imported_files = snapshot.get("imported_files", [])
        imported_count = snapshot.get("imported_file_count", len(imported_files))
        if imported_count == 0 or not imported_files:
            print("   (暂无已导入文件，使用 'import' 命令导入数据)")
        else:
            for i, f in enumerate(imported_files, 1):
                fname = f.get("filename", "未知文件")
                stype = f.get("source_type", "未知类型")
                ec = f.get("event_count", 0)
                errc = f.get("error_count", 0)
                ts = f.get("imported_at", "")
                hash_short = f.get("file_hash", "")
                line = f"   {i:>2}. [{stype:<10}] {fname}"
                if ec or errc:
                    line += f"  ({ec} 事件"
                    if errc > 0:
                        line += f", {errc} 错误"
                    line += ")"
                print(line)
                if ts:
                    print(f"       导入时间: {ts}")
                if hash_short:
                    print(f"       文件哈希: {hash_short}...")

        print()
        print("📊 数据统计:")
        event_count = snapshot.get("event_count", 0)
        parse_error_count = snapshot.get("parse_error_count", 0)
        src_label = {"log": "日志", "alert": "告警", "note": "备注"}
        by_source = snapshot.get("events_by_source", {})
        source_parts = []
        for k, v in by_source.items():
            source_parts.append(f"{src_label.get(k, k)} {v}")
        source_str = "  ".join(source_parts) if source_parts else "无"
        print(f"   事件总数:     {event_count}  ({source_str})")

        by_status = snapshot.get("events_by_status", {})
        if by_status:
            status_parts = [f"{k} {v}" for k, v in by_status.items()]
            print(f"   状态分布:     {'  '.join(status_parts)}")

        by_severity = snapshot.get("events_by_severity", {})
        if by_severity:
            sev_parts = [f"{k} {v}" for k, v in sorted(by_severity.items())]
            print(f"   严重级别:     {'  '.join(sev_parts)}")

        if snapshot.get("time_range_start") and snapshot.get("time_range_end"):
            ts_start = snapshot["time_range_start"].replace("T", " ")[:19]
            ts_end = snapshot["time_range_end"].replace("T", " ")[:19]
            print(f"   时间范围:     {ts_start}  ~  {ts_end}")

        print(f"   解析错误数:   {parse_error_count}")
        pe_by_file = snapshot.get("parse_errors_by_file", {})
        if pe_by_file:
            for fname, cnt in pe_by_file.items():
                print(f"     - {fname}: {cnt} 个")

        print()
        print("🏷️  最近动作:")
        latest_kind = snapshot.get("latest_action_kind")
        latest = snapshot.get("latest_action")
        label_count = snapshot.get("label_action_count", 0)
        undo_count = snapshot.get("undo_action_count", 0)
        last_label = snapshot.get("last_label_action")
        last_undo = snapshot.get("last_undo_action")
        if not latest:
            if label_count == 0 and undo_count == 0:
                print("   (暂无标注或撤销记录)")
            elif label_count > 0 and last_label is None:
                print(f"   (标注 {label_count} 次，全部已撤销)")
            else:
                parts = []
                if label_count:
                    parts.append(f"标注 {label_count} 次")
                if undo_count:
                    parts.append(f"撤销 {undo_count} 次")
                print(f"   ({', '.join(parts)}，无可用详情)")
        elif latest_kind == "label":
            op = latest.get("operation", "未知操作")
            eid_short = latest.get("event_id_short", "???")
            acted_at = latest.get("acted_at", "")
            print(f"   类型:   标注")
            print(f"   操作:   {op}")
            print(f"   事件:   {eid_short}")
            if latest.get("old_status") or latest.get("new_status"):
                old_s = latest.get("old_status") or "无"
                new_s = latest.get("new_status") or "无"
                print(f"   状态:   {old_s}  →  {new_s}")
            if latest.get("old_notes_preview") is not None or latest.get("new_notes_preview") is not None:
                old_n = latest.get("old_notes_preview") or "(空)"
                new_n = latest.get("new_notes_preview") or "(空)"
                print(f"   备注:   {old_n}  →  {new_n}")
            if latest.get("config_version"):
                print(f"   规则版本: {latest['config_version']}")
            if acted_at:
                print(f"   操作时间: {acted_at}")
            total = label_count + undo_count
            if total > 1:
                parts = []
                if label_count:
                    parts.append(f"标注 {label_count} 次")
                if undo_count:
                    parts.append(f"撤销 {undo_count} 次")
                print(f"   (共 {total} 次操作: {', '.join(parts)})")
        elif latest_kind == "undo":
            undo_type_desc = latest.get("undo_type_desc", "撤销")
            acted_at = latest.get("acted_at", "")
            print(f"   类型:   {undo_type_desc}")
            if latest.get("undo_type") == "undo_label":
                eid_short = latest.get("event_id_short", "???")
                op_desc = latest.get("operation_description", "未知操作")
                print(f"   原操作: {op_desc}")
                print(f"   事件:   {eid_short}")
                rest_s = latest.get("restored_status")
                if rest_s:
                    print(f"   恢复状态: {rest_s}")
                if latest.get("old_notes_preview") is not None:
                    old_n = latest.get("old_notes_preview") or "(空)"
                    print(f"   恢复备注: {old_n}")
                if latest.get("config_version"):
                    print(f"   规则版本: {latest['config_version']}")
            elif latest.get("undo_type") == "undo_import":
                fname = latest.get("filename", "未知文件")
                stype = latest.get("source_type", "未知类型")
                print(f"   文件:   [{stype}] {fname}")
                rem_ev = latest.get("removed_event_count", 0)
                rem_err = latest.get("removed_error_count", 0)
                print(f"   清理:   删除 {rem_ev} 条事件, {rem_err} 个解析错误")
                orig_ev = latest.get("imported_event_count", 0)
                orig_err = latest.get("imported_error_count", 0)
                if rem_ev != orig_ev or rem_err != orig_err:
                    print(f"   (导入时: {orig_ev} 事件, {orig_err} 错误)")
                imp_ts = latest.get("imported_at", "")
                if imp_ts:
                    print(f"   原导入时间: {imp_ts}")
            if acted_at:
                print(f"   撤销时间: {acted_at}")
            total = label_count + undo_count
            if total > 1:
                parts = []
                if label_count:
                    parts.append(f"标注 {label_count} 次")
                if undo_count:
                    parts.append(f"撤销 {undo_count} 次")
                print(f"   (共 {total} 次操作: {', '.join(parts)})")

        print()
        print("📤 最近导出:")
        last_export = snapshot.get("last_export")
        export_count = snapshot.get("export_count", 0)
        if not last_export:
            if export_count == 0:
                print("   (暂无导出记录，使用 'export' 命令导出报告)")
            else:
                print(f"   (历史导出 {export_count} 次，最近记录已不可用)")
        else:
            fname = last_export.get("filename", "")
            size = last_export.get("size", 0)
            ts = last_export.get("exported_at", "")
            print(f"   文件:     {fname}")
            print(f"   大小:     {size} 字节")
            if ts:
                print(f"   导出时间: {ts}")
            if export_count > 1:
                print(f"   (共 {export_count} 次历史导出)")

        print()
        print("⚙️  当前规则配置:")
        rule_ver = snapshot.get("rule_version", "未知")
        dedup_win = snapshot.get("dedup_window_seconds", 0)
        gap_thr = snapshot.get("gap_threshold_seconds", 0)
        sim_thr = snapshot.get("dedup_similarity_threshold", 0)
        phase_cnt = snapshot.get("phase_count", 0)
        print(f"   规则版本:       {rule_ver}")
        print(f"   去重时间窗口:   {dedup_win}s")
        print(f"   缺口时间阈值:   {gap_thr}s")
        print(f"   去重相似度:     {sim_thr}")
        print(f"   已配置阶段数:   {phase_cnt}")

        if warnings:
            print()
            print("⚠️  数据完整性警告:")
            for w in warnings:
                print(w)

        print()

    def _print_snapshot_history(self, batch_id: str, limit: int) -> None:
        snapshots = self.store.list_historical_snapshots(batch_id, limit=limit)
        trigger_labels = {
            "create": "创建批次",
            "import": "导入数据",
            "config": "配置变更",
            "label": "标注事件",
            "undo_label": "撤销标注",
            "undo_import": "撤销导入",
            "export": "导出报告",
            "manual": "手动刷新",
            "auto_refresh": "自动刷新",
            "repair": "修复快照",
        }

        print("=" * 78)
        print(f"📜 历史快照记录 (最近 {len(snapshots)} 条):")
        print("=" * 78)
        print(f"{'快照ID':<32} {'触发原因':<12} {'事件数':<8} {'导入数':<8} {'版本':<10} {'保存时间'}")
        print("-" * 78)

        if not snapshots:
            print("   (暂无历史快照记录)")
        else:
            for s in snapshots:
                trigger = trigger_labels.get(s.get("trigger", "unknown"), s.get("trigger", "unknown"))
                saved_at = s.get("saved_at", "").replace("T", " ")[:19]
                print(f"{s.get('snapshot_id',''):<32} {trigger:<12} {s.get('event_count',0):<8} {s.get('imported_file_count',0):<8} {s.get('rule_version',''):<10} {saved_at}")

        print()
        print("💡 提示: 使用 --diff <快照ID> 与指定快照对比")

    def _check_and_print_consistency(self, batch_id: str, fix: bool = False) -> None:
        print("🔍 正在检查快照与真实数据的一致性...")
        print()
        result = self.store.check_snapshot_consistency(batch_id)

        if result.get("consistent", False):
            print("✅ 快照与真实数据一致")
            real = result.get("real_data", {})
            print(f"   事件数: {real.get('event_count', 0)}")
            print(f"   导入文件数: {real.get('imported_file_count', 0)}")
            print(f"   规则版本: {real.get('rule_version', 'unknown')}")
        else:
            print("❌ 发现快照与真实数据不一致")
            print()
            print(f"{'字段':<25} {'快照值':<15} {'真实值':<15} {'差异'}")
            print("-" * 70)
            for inc in result.get("inconsistencies", []):
                field = inc.get("field", "")
                snap = inc.get("snapshot", 0)
                real = inc.get("real", 0)
                diff = inc.get("diff", 0)
                diff_str = f"+{diff}" if diff > 0 else str(diff)
                print(f"{field:<25} {str(snap):<15} {str(real):<15} {diff_str}")

            if fix:
                print()
                print("🔧 正在自动修复...")
                fix_result = self.store.fix_snapshot_inconsistencies(batch_id)
                if fix_result.get("fixed", False):
                    print("✅ 快照已修复")
                else:
                    print(f"❌ 修复失败: {fix_result.get('message', '未知错误')}")
            else:
                print()
                print("💡 提示: 使用 --fix 参数自动修复快照不一致")

        print()

    def _fix_and_print_snapshot(self, batch_id: str) -> None:
        print("🔧 正在检查并修复快照...")
        print()
        check_result = self.store.check_snapshot_consistency(batch_id)

        if check_result.get("consistent", False):
            print("✅ 快照一致，无需修复")
        else:
            fix_result = self.store.fix_snapshot_inconsistencies(batch_id)
            if fix_result.get("fixed", False):
                print(f"✅ 已修复 {len(fix_result.get('inconsistencies_fixed', []))} 处不一致")
                for inc in fix_result.get("inconsistencies_fixed", []):
                    print(f"   - {inc['field']}: {inc['snapshot']} → {inc['real']}")
            else:
                print(f"❌ 修复失败: {fix_result.get('message', '未知错误')}")
        print()

    def _print_change_log(self, batch_id: str, limit: int, log_type: str = None) -> None:
        log = self.store.get_change_log(batch_id, limit=limit, change_type=log_type)

        type_labels = {
            "import_change": "📥 导入变更",
            "config_change": "⚙️  配置变更",
            "export_change": "📤 导出变更",
            "label_change": "🏷️  标注变更",
            "snapshot_repair": "🔧 快照修复",
            "other_change": "🔄 其他变更",
            "manual_refresh": "👆 手动刷新",
        }
        severity_icons = {
            "info": "ℹ️ ",
            "warning": "⚠️ ",
            "error": "❌",
        }

        print("=" * 78)
        title = "变更日志"
        if log_type:
            title += f" (类型: {log_type})"
        print(f"📜 {title} (最近 {len(log)} 条):")
        print("=" * 78)

        if not log:
            print("   (暂无变更记录)")
        else:
            for entry in log:
                ctype = entry.get("change_type", "unknown")
                severity = entry.get("severity", "info")
                icon = severity_icons.get(severity, "  ")
                type_label = type_labels.get(ctype, ctype)
                created_at = entry.get("created_at", "").replace("T", " ")[:19]
                entry_id = entry.get("id", "")

                print(f"{icon} [{created_at}] {type_label} (ID: {entry_id})")
                detail = entry.get("detail", {})
                summary = detail.get("diff_summary", [])
                if summary:
                    for s in summary[:5]:
                        print(f"   • {s}")
                    if len(summary) > 5:
                        print(f"   ... 还有 {len(summary) - 5} 条变更")
                else:
                    for k, v in detail.items():
                        if v:
                            print(f"   • {k}: {v}")
                print()

        print("💡 提示: 使用 --log-type <类型> 过滤变更类型")

    def _print_export_diff(self, batch_id: str) -> None:
        result = self.store.get_export_comparison(batch_id)
        exports = result.get("exports", [])
        comparison = result.get("comparison")

        print("=" * 62)
        print("📤 导出历史与对比:")
        print("=" * 62)
        print()

        if not exports:
            print("   (暂无导出记录)")
        else:
            print(f"共 {len(exports)} 次导出:")
            print()
            for i, exp in enumerate(exports[:10], 1):
                modified_at = exp.get("modified_at", "").replace("T", " ")[:19]
                size = exp.get("size", 0)
                print(f"   {i:>2}. {exp.get('filename',''):<40} {size:>8} 字节  {modified_at}")

            if comparison:
                print()
                print("📊 最近两次导出对比:")
                latest = comparison.get("latest", {})
                previous = comparison.get("previous", {})
                size_diff = comparison.get("size_diff", 0)
                size_diff_pct = comparison.get("size_diff_percent", 0)
                time_diff = comparison.get("time_diff_seconds")

                arrow = "↑" if size_diff > 0 else ("↓" if size_diff < 0 else "=")
                print(f"   文件:     {previous.get('filename','')}  →  {latest.get('filename','')}")
                print(f"   大小:     {previous.get('size',0)} → {latest.get('size',0)} 字节  {arrow} {abs(size_diff)} ({size_diff_pct:+}%)")
                if time_diff is not None:
                    minutes, seconds = divmod(time_diff, 60)
                    time_str = f"{minutes}分{seconds}秒" if minutes > 0 else f"{seconds}秒"
                    print(f"   间隔:     {time_str}")

        print()

    def _print_change_summary(self, batch_id: str, compare_with: str, refresh: bool = False) -> None:
        if refresh:
            print("🔄 正在刷新快照...")
            self.store.refresh_overview_snapshot(batch_id, trigger="manual")
            print()

        print("📊 正在生成变更摘要...")
        print()
        result = self.store.get_change_summary(batch_id, compare_with=compare_with)

        if result.get("error"):
            print(f"❌ {result.get('error')}")
            return

        note = result.get("note", "")
        if note:
            print(f"ℹ️  {note}")
            print()

        diff = result.get("diff")
        if not diff:
            print("📋 没有可对比的变更（当前为初始状态）")
            return

        print("=" * 62)
        print("📊 变更摘要:")
        print("=" * 62)
        print()

        summary = diff.get("summary", [])
        if summary:
            for s in summary:
                print(f"   • {s}")
            print()

        if diff.get("event_count_change") is not None:
            change = diff["event_count_change"]
            arrow = "+" if change > 0 else ""
            print(f"   事件数变化:    {arrow}{change}")
            if diff.get("added_events") > 0:
                print(f"     新增: {diff['added_events']}")
            if diff.get("removed_events") > 0:
                print(f"     移除: {diff['removed_events']}")

        status_changes = diff.get("status_changes", {})
        if status_changes:
            print()
            print("   状态分布变化:")
            for status, change in sorted(status_changes.items()):
                arrow = "+" if change > 0 else ""
                print(f"     {status:<8} {arrow}{change}")

        source_changes = diff.get("source_changes", {})
        if source_changes:
            print()
            src_label = {"log": "日志", "alert": "告警", "note": "备注"}
            print("   来源分布变化:")
            for source, change in sorted(source_changes.items()):
                arrow = "+" if change > 0 else ""
                label = src_label.get(source, source)
                print(f"     {label:<8} {arrow}{change}")

        config_changes = diff.get("config_changes", {})
        if config_changes:
            print()
            print("   配置变更:")
            for field, change in config_changes.items():
                old = change.get("old")
                new = change.get("new")
                print(f"     {field}: {old} → {new}")

        import_changes = diff.get("import_changes", [])
        if import_changes:
            print()
            print("   导入变更:")
            for ic in import_changes:
                if ic.get("type") == "added":
                    print(f"     ✅ 新增: {ic.get('filename')} ({ic.get('source_type')}, {ic.get('event_count',0)}事件)")
                elif ic.get("type") == "removed":
                    print(f"     ❌ 移除: {ic.get('filename')} ({ic.get('source_type')})")

        export_changes = diff.get("export_changes", [])
        if export_changes:
            print()
            print("   导出变更:")
            for ec in export_changes:
                print(f"     📤 新增导出: {ec.get('filename')} ({ec.get('size',0)}字节)")

        label_changes = diff.get("label_changes", [])
        if label_changes:
            print()
            print("   标注变更:")
            for lc in label_changes:
                if lc.get("type") == "label":
                    print(f"     🏷️  {lc.get('operation')}: {lc.get('event_id_short')} → {lc.get('new_status')}")
                elif lc.get("type") == "undo":
                    print(f"     ↩️  {lc.get('undo_type_desc')}")

        print()
        print("💡 提示: 使用 --diff first 与最初状态对比，或 --diff <快照ID> 与指定快照对比")

    def cmd_import(self, args) -> None:
        batch_id = self._require_batch()
        config = self.store.load_config(batch_id)
        existing_events = self.store.load_events(batch_id)
        existing_ids = {e.id for e in existing_events}
        pre_round = self.store._get_next_round_number(batch_id)

        total_events_imported = 0
        total_errors = 0
        files_processed = []
        files_skipped = []
        files_warnings = []
        all_errors = list(self.store.load_parse_errors(batch_id))
        import_ids_created = []
        per_file_import_data = []

        for file_path in args.files:
            path = Path(file_path)
            if not path.exists():
                print(f"❌ 文件不存在: {file_path}", file=sys.stderr)
                continue

            abs_path = str(path.resolve())

            try:
                file_hash = compute_file_hash(str(path))
            except Exception as e:
                print(f"❌ 无法计算文件哈希 {path.name}: {e}", file=sys.stderr)
                continue

            dup_check = self.store.check_duplicate_import(batch_id, abs_path, file_hash)
            if dup_check.get("is_duplicate"):
                if args.force:
                    existing = dup_check.get("existing_entry", {})
                    if dup_check.get("hash_changed"):
                        print(f"⚠️  强制重新导入: {path.name}")
                        print(f"   文件内容已变更 (哈希不匹配)")
                        print(f"   旧哈希: {existing.get('file_hash', '')[:16]}...")
                        print(f"   新哈希: {file_hash[:16]}...")
                        files_warnings.append({
                            "file": path.name,
                            "warning": "文件内容已变更，强制重新导入",
                        })
                        self.store.log_change(batch_id, "import_change", {
                            "action": "force_reimport",
                            "filename": path.name,
                            "hash_changed": True,
                            "old_hash": existing.get("file_hash", "")[:16],
                            "new_hash": file_hash[:16],
                            "old_event_count": existing.get("event_count", 0),
                        }, severity="warning")
                    else:
                        print(f"⚠️  强制重新导入: {path.name}")
                        print(f"   文件内容未变更，与上次导入相同")
                        files_warnings.append({
                            "file": path.name,
                            "warning": "文件内容未变更，强制重新导入",
                        })
                        self.store.log_change(batch_id, "import_change", {
                            "action": "force_reimport",
                            "filename": path.name,
                            "hash_changed": False,
                            "old_event_count": existing.get("event_count", 0),
                        }, severity="info")
                else:
                    existing = dup_check.get("existing_entry", {})
                    recommendation = dup_check.get("recommendation", "skip")
                    if recommendation == "force_reimport":
                        print(f"⚠️  冲突: 文件已导入但内容已变更: {path.name}")
                        print(f"   原导入时间: {existing.get('imported_at', 'N/A')}")
                        print(f"   原导入事件: {existing.get('event_count', 0)} 条")
                        print(f"   💡 使用 --force 强制重新导入")
                    elif recommendation == "restore_or_force":
                        print(f"⚠️  冲突: 文件存在已撤销的导入记录: {path.name}")
                        print(f"   原导入时间: {existing.get('imported_at', 'N/A')}")
                        print(f"   原导入事件: {existing.get('event_count', 0)} 条")
                        print(f"   💡 使用 restore-import 恢复该导入，或 --force 强制重新导入")
                    else:
                        print(f"⏭️  文件已导入过，跳过: {path.name}")
                        print(f"   导入时间: {existing.get('imported_at', 'N/A')}")
                        print(f"   事件数: {existing.get('event_count', 0)} 条")
                        print(f"   💡 使用 --force 强制重新导入")
                    files_skipped.append({
                        "file": path.name,
                        "reason": "duplicate",
                        "recommendation": recommendation,
                        "existing": existing,
                    })
                    self.store.log_change(batch_id, "import_change", {
                        "action": "skipped_duplicate",
                        "filename": path.name,
                        "hash_changed": dup_check.get("hash_changed", False),
                        "recommendation": recommendation,
                    }, severity="info")
                    continue

            if args.type:
                parser = get_parser_by_type(args.type, config)
            else:
                parser = get_parser_by_extension(str(path), config)

            print(f"📥 正在解析: {path.name} ...")
            raw_events, parse_errors = parser.parse(str(path))
            file_error_refs = [(err.source_file, err.line_number) for err in parse_errors]

            all_errors = [e for e in all_errors if e.source_file != path.name]
            all_errors.extend(parse_errors)

            if parse_errors:
                print(f"   ⚠️  发现 {len(parse_errors)} 个解析错误:")
                for err in parse_errors[:5]:
                    print(f"      - 行 {err.line_number}: {err.error_message}")
                if len(parse_errors) > 5:
                    print(f"      ... 还有 {len(parse_errors) - 5} 个错误")
                total_errors += len(parse_errors)

            new_events = raw_events_to_events(raw_events, config)
            unique_new = [e for e in new_events if e.id not in existing_ids]
            duplicates = len(new_events) - len(unique_new)

            if duplicates > 0:
                print(f"   ℹ️  发现 {duplicates} 个与现有事件 ID 重复，已自动跳过")

            file_event_ids = []
            if unique_new:
                existing_events.extend(unique_new)
                for e in unique_new:
                    existing_ids.add(e.id)
                    file_event_ids.append(e.id)
                total_events_imported += len(unique_new)
                print(f"   ✅ 导入 {len(unique_new)} 条新事件")
            else:
                print(f"   ℹ️  没有新事件需要导入")

            per_file_import_data.append((abs_path, file_hash, file_event_ids, file_error_refs, len(unique_new), len(parse_errors), path.name))
            files_processed.append(path.name)

        self.store.save_parse_errors(batch_id, all_errors)

        import_rounds_for_this_call = []
        file_import_attachments = []
        for (abs_path, file_hash, file_event_ids, file_error_refs, ev_cnt, err_cnt, fname) in per_file_import_data:
            this_round = self.store._get_next_round_number(batch_id)
            import_rounds_for_this_call.append(this_round)
            entry = self.store.mark_file_imported(
                batch_id, abs_path, file_hash, ev_cnt, err_cnt,
                event_ids=file_event_ids,
                parse_error_refs=file_error_refs,
                round_number=this_round,
            )
            imp_id = entry.get("import_id", "")
            import_ids_created.append(imp_id)
            file_import_attachments.append((imp_id, this_round, file_event_ids, file_error_refs))

        self.store.save_events(batch_id, existing_events)

        for (imp_id, this_round, file_event_ids, file_error_refs) in file_import_attachments:
            self.store._attach_import_id_to_events(batch_id, imp_id, file_event_ids, this_round)
            self.store._attach_import_id_to_errors(batch_id, imp_id, file_error_refs)

        existing_events = self.store.load_events(batch_id)

        if existing_events:
            print(f"🔍 正在去重归并...")
            before_count = len(existing_events)
            deduped, merged_pairs = dedupe_events(existing_events, config)
            after_count = len(deduped)
            merged = before_count - after_count
            if merged > 0:
                print(f"   ✅ 合并了 {merged} 组重复事件")
                if merged_pairs:
                    deduped_by_id = {e.id: e for e in deduped}
                    orig_by_id = {e.id: e for e in existing_events}
                    transfer_count = 0
                    for (removed_id, kept_id) in merged_pairs:
                        removed = orig_by_id.get(removed_id)
                        kept = deduped_by_id.get(kept_id)
                        if removed and kept and hasattr(removed, 'import_ids') and hasattr(kept, 'import_ids'):
                            if removed.import_ids:
                                for rid in removed.import_ids:
                                    if rid not in kept.import_ids:
                                        kept.import_ids.append(rid)
                                for r, rid in zip(removed.import_rounds, removed.import_ids):
                                    if r not in kept.import_rounds:
                                        kept.import_rounds.append(r)
                                transfer_count += 1
                    if import_ids_created and transfer_count > 0:
                        print(f"   🔗 转移了 {transfer_count} 组导入归属信息 (孤儿事件保护)")
            existing_events = deduped

        self.store.save_events(batch_id, existing_events)
        self.store.save_config(batch_id, config)

        print()
        print(f"📦 导入完成:")
        print(f"   处理文件: {len(files_processed)} 个")
        if import_ids_created:
            print(f"   导入轮次: {', '.join(str(r) for r in import_rounds_for_this_call)}")
        if files_skipped:
            print(f"   跳过文件: {len(files_skipped)} 个")
            for s in files_skipped:
                reason = s.get("reason", "unknown")
                if reason == "duplicate":
                    rec = s.get("recommendation", "skip")
                    if rec == "force_reimport":
                        print(f"     ⚠️  {s['file']}: 内容已变更，建议 --force 重新导入")
                    else:
                        print(f"     ℹ️  {s['file']}: 已存在，使用 --force 强制重导")
        print(f"   新增事件: {total_events_imported} 条")
        print(f"   解析错误: {total_errors} 个")
        print(f"   当前批次事件总数: {len(existing_events)} 条")

        if files_warnings:
            print()
            print(f"⚠️  导入警告 ({len(files_warnings)} 条):")
            for w in files_warnings:
                print(f"   • {w['file']}: {w['warning']}")

        if files_skipped and any(s.get("recommendation") == "force_reimport" for s in files_skipped):
            print()
            print(f"💡 提示: 有 {sum(1 for s in files_skipped if s.get('recommendation') == 'force_reimport')} 个文件内容已变更")
            print(f"   可使用: timeline-review import --force {' '.join(s['file'] for s in files_skipped if s.get('recommendation') == 'force_reimport')}")

    def cmd_undo_import(self, args) -> None:
        batch_id = self._require_batch()
        target_round = getattr(args, "round", None)
        target_import_id = getattr(args, "import_id", None)
        target_index = getattr(args, "index", None)

        if target_index is not None:
            resolved = self.store.resolve_import_by_display_index(batch_id, target_index)
            if not resolved:
                print(f"❌ 序号 {target_index} 对应的导入记录不存在", file=sys.stderr)
                return
            target_import_id = resolved.get("import_id")
            print(f"📍 序号 {target_index} → 导入ID {target_import_id[:16]}...")

        undo_desc = ""
        if target_index is not None:
            undo_desc = f"序号 {target_index}"
        elif target_round is not None:
            undo_desc = f"轮次 {target_round}"
        elif target_import_id:
            undo_desc = f"导入ID {target_import_id[:12]}..."
        else:
            undo_desc = "最近一次"

        print(f"↩️  正在撤销导入 ({undo_desc}) ...")
        last = self.store.undo_last_import(
            batch_id, import_id=target_import_id, round_number=target_round
        )
        if not last:
            print("ℹ️  没有可撤销的导入记录")
            active_imports = self.store.get_active_imports(batch_id)
            if active_imports:
                print(f"   当前有 {len(active_imports)} 个激活的导入记录")
                for i, imp in enumerate(active_imports[-5:], 1):
                    print(f"   {i}. 轮次{imp.get('round_number', '?')}: {imp.get('filename')} ({imp.get('event_count', 0)}事件)")
            return

        removed = last.get("removed_event_count_actual", 0)
        bound = len(last.get("event_ids", []))
        orphans = len(last.get("orphaned_events_due_to_dedup", []))
        filename = last.get("filename", "unknown")
        round_num = last.get("round_number", "?")

        print(f"✅ 已撤销导入: {filename} (轮次 {round_num})")
        print(f"   导入时绑定事件数: {bound}")
        print(f"   实际删除事件数:   {removed}")
        if orphans > 0:
            print(f"   因去重合并保留事件: {orphans} (这些事件还被其他导入引用)")
        print(f"   删除解析错误数:   {last.get('removed_error_count_actual', 0)}")

        snap = self.store.load_overview_snapshot(batch_id, auto_refresh=False)
        real_events = len(self.store.load_events(batch_id))
        snap_events = snap.get("event_count", 0)
        if real_events != snap_events:
            print(f"⚠️  注意: 概览快照({snap_events})与真实事件({real_events})不一致，正在修复...")
            self.store.fix_snapshot_inconsistencies(batch_id)
        print(f"   当前批次事件总数: {real_events}")

    def cmd_restore_import(self, args) -> None:
        batch_id = self._require_batch()
        target_round = getattr(args, "round", None)
        target_import_id = getattr(args, "import_id", None)
        target_index = getattr(args, "index", None)

        if target_index is not None:
            resolved = self.store.resolve_import_by_display_index(batch_id, target_index)
            if not resolved:
                print(f"❌ 序号 {target_index} 对应的导入记录不存在", file=sys.stderr)
                return
            target_import_id = resolved.get("import_id")
            print(f"📍 序号 {target_index} → 导入ID {target_import_id[:16]}...")

        restore_desc = ""
        if target_index is not None:
            restore_desc = f"序号 {target_index}"
        elif target_round is not None:
            restore_desc = f"轮次 {target_round}"
        elif target_import_id:
            restore_desc = f"导入ID {target_import_id[:12]}..."
        else:
            undone = self.store.get_undone_imports(batch_id)
            if undone:
                last_undone = undone[-1]
                restore_desc = f"最近撤销的: {last_undone.get('filename')}"
            else:
                restore_desc = "最近一次撤销"

        print(f"↪️  正在恢复导入 ({restore_desc}) ...")
        result = self.store.restore_import(
            batch_id, import_id=target_import_id, round_number=target_round
        )
        if not result:
            print("ℹ️  没有可恢复的撤销记录")
            undone = self.store.get_undone_imports(batch_id)
            if undone:
                print(f"   有 {len(undone)} 条可恢复的撤销记录:")
                for i, imp in enumerate(undone[-5:], 1):
                    print(f"   {i}. 轮次{imp.get('round_number', '?')}: {imp.get('filename')} "
                          f"(撤销于 {imp.get('undone_at', '')[:19]})")
            return

        filename = result.get("filename", "unknown")
        round_num = result.get("round_number", "?")
        restore_count = result.get("restored_count", 1)
        restored_events = result.get("restored_event_count", 0)
        already_present = result.get("already_present_event_count", 0)

        print(f"✅ 已恢复导入: {filename} (轮次 {round_num})")
        print(f"   恢复次数累计:     {restore_count}")
        print(f"   恢复关联事件数:   {restored_events}")
        if already_present > 0:
            print(f"   已存在于库中:     {already_present} (因后续导入或去重已存在)")
        print(f"   恢复解析错误数:   {result.get('restored_error_count', 0)}")

        snap = self.store.load_overview_snapshot(batch_id, auto_refresh=False)
        real_events = len(self.store.load_events(batch_id))
        print(f"   当前批次事件总数: {real_events}")

    def cmd_import_detail(self, args) -> None:
        batch_id = self._require_batch()
        target_round = getattr(args, "round", None)
        target_import_id = getattr(args, "import_id", None)
        target_index = getattr(args, "index", None)

        if target_index is not None:
            resolved = self.store.resolve_import_by_display_index(batch_id, target_index)
            if not resolved:
                print(f"❌ 序号 {target_index} 对应的导入记录不存在", file=sys.stderr)
                return
            target_import_id = resolved.get("import_id")
            print(f"📍 序号 {target_index} → 导入ID {target_import_id[:16]}...")

        if target_import_id:
            detail = self.store.get_import_detail(batch_id, import_id=target_import_id)
            desc = f"导入ID {target_import_id}"
        elif target_round is not None:
            detail = self.store.get_import_detail(batch_id, round_number=target_round)
            desc = f"轮次 {target_round}"
        else:
            active = self.store.get_active_imports(batch_id)
            if active:
                detail = self.store.get_import_detail(batch_id, import_id=active[-1].get("import_id"))
                desc = f"最近一次导入: {active[-1].get('filename')}"
            else:
                print("ℹ️  没有导入记录")
                return

        if not detail:
            print(f"❌ 找不到 {desc} 的导入详情")
            return

        status_label = {"active": "✅ 激活", "undone": "↩️ 已撤销"}.get(
            detail.get("status", "unknown"), detail.get("status", "未知")
        )

        print("=" * 78)
        print(f"📋 导入详情 - {desc}")
        print("=" * 78)
        print(f"   文件名:       {detail.get('filename', '')}")
        print(f"   导入轮次:     {detail.get('round_number', '?')}")
        print(f"   导入ID:       {detail.get('import_id', '')}")
        print(f"   状态:         {status_label}")
        print(f"   导入时间:     {detail.get('imported_at', '')}")
        if detail.get("status") == "undone":
            print(f"   撤销时间:     {detail.get('undone_at', '')}")
            print(f"   实际删除事件: {detail.get('removed_event_count_actual', 0)}")
            print(f"   孤留事件(去重): {len(detail.get('orphaned_events_due_to_dedup', []))}")
            if detail.get("restored_count", 0) > 0:
                print(f"   历史恢复次数: {detail.get('restored_count', 0)}")
                print(f"   最后恢复时间: {detail.get('last_restored_at', '')}")
        print()
        print(f"   导入时声明:   {detail.get('event_count', 0)} 事件, {detail.get('error_count', 0)} 错误")
        print(f"   实际匹配事件: {detail.get('matched_event_count', 0)} 条")
        print(f"   文件哈希:     {detail.get('file_hash', '')[:16]}...")
        print(f"   绝对路径:     {detail.get('abs_path', '')}")
        print()

        matched = detail.get("matched_events_sample", [])
        if matched:
            print(f"📝 关联事件样本 (最多10条):")
            print(f"   {'状态':<10} {'严重级别':<10} {'时间':<20} 消息")
            print("-" * 78)
            for ev in matched:
                active_marker = "✅" if ev.get("active_in_event") else "⚠️"
                ts = ev.get("timestamp", "")[:19].replace("T", " ")
                msg = ev.get("message", "")[:50]
                print(f"   {active_marker} {ev.get('status',''):<8} {ev.get('severity',''):<8} {ts} {msg}")
                print(f"      ID: {ev.get('id', '')}")
            print()

        consistency = self.store.check_snapshot_consistency(batch_id)
        if consistency.get("consistent"):
            print("✅ 快照与真实数据一致")
        else:
            print(f"⚠️  发现 {len(consistency.get('inconsistencies', []))} 处不一致")
            for inc in consistency.get("inconsistencies", [])[:3]:
                print(f"   - {inc.get('field')}: 快照={inc.get('snapshot')} 真实={inc.get('real')}")
        print()

    def cmd_import_history(self, args) -> None:
        batch_id = self._require_batch()

        if getattr(args, "detail", None) is not None:
            idx = args.detail
            resolved = self.store.resolve_import_by_display_index(batch_id, idx)
            if not resolved:
                print(f"❌ 序号 {idx} 对应的导入记录不存在", file=sys.stderr)
                return
            import_id = resolved.get("import_id")
            print(f"📍 序号 {idx} → 导入ID {import_id[:16]}...")
            detail = self.store.get_import_detail(batch_id, import_id=import_id)
            if not detail:
                print(f"❌ 找不到导入详情", file=sys.stderr)
                return
            self._print_import_detail_block(detail, f"序号 {idx}")
            return

        if getattr(args, "undo", None) is not None:
            idx = args.undo
            resolved = self.store.resolve_import_by_display_index(batch_id, idx)
            if not resolved:
                print(f"❌ 序号 {idx} 对应的导入记录不存在", file=sys.stderr)
                return
            if resolved.get("status") != "active":
                print(f"❌ 序号 {idx} 的导入状态为「已撤销」，无法再次撤销", file=sys.stderr)
                return
            import_id = resolved.get("import_id")
            print(f"📍 序号 {idx} → 导入ID {import_id[:16]}...")
            print(f"↩️  正在撤销导入 (序号 {idx}) ...")
            last = self.store.undo_last_import(batch_id, import_id=import_id)
            if not last:
                print("ℹ️  撤销失败，该导入可能已不存在或已撤销")
                return
            removed = last.get("removed_event_count_actual", 0)
            bound = len(last.get("event_ids", []))
            orphans = len(last.get("orphaned_events_due_to_dedup", []))
            filename = last.get("filename", "unknown")
            round_num = last.get("round_number", "?")
            print(f"✅ 已撤销导入: {filename} (轮次 {round_num})")
            print(f"   导入时绑定事件数: {bound}")
            print(f"   实际删除事件数:   {removed}")
            if orphans > 0:
                print(f"   因去重合并保留事件: {orphans}")
            print(f"   删除解析错误数:   {last.get('removed_error_count_actual', 0)}")
            real_events = len(self.store.load_events(batch_id))
            print(f"   当前批次事件总数: {real_events}")
            self.store.log_change(batch_id, "import_history_undo", {
                "display_index": idx,
                "import_id": import_id,
                "filename": filename,
            }, severity="info")
            return

        if getattr(args, "restore", None) is not None:
            idx = args.restore
            resolved = self.store.resolve_import_by_display_index(batch_id, idx)
            if not resolved:
                print(f"❌ 序号 {idx} 对应的导入记录不存在", file=sys.stderr)
                return
            if resolved.get("status") != "undone":
                print(f"❌ 序号 {idx} 的导入状态为「激活」，无需恢复", file=sys.stderr)
                return
            import_id = resolved.get("import_id")
            print(f"📍 序号 {idx} → 导入ID {import_id[:16]}...")
            print(f"↪️  正在恢复导入 (序号 {idx}) ...")
            result = self.store.restore_import(batch_id, import_id=import_id)
            if not result:
                print("ℹ️  恢复失败，该撤销记录可能已不存在")
                return
            filename = result.get("filename", "unknown")
            round_num = result.get("round_number", "?")
            restore_count = result.get("restored_count", 1)
            restored_events = result.get("restored_event_count", 0)
            already_present = result.get("already_present_event_count", 0)
            print(f"✅ 已恢复导入: {filename} (轮次 {round_num})")
            print(f"   恢复次数累计:     {restore_count}")
            print(f"   恢复关联事件数:   {restored_events}")
            if already_present > 0:
                print(f"   已存在于库中:     {already_present} (因后续导入或去重已存在)")
            print(f"   恢复解析错误数:   {result.get('restored_error_count', 0)}")
            real_events = len(self.store.load_events(batch_id))
            print(f"   当前批次事件总数: {real_events}")
            self.store.log_change(batch_id, "import_history_restore", {
                "display_index": idx,
                "import_id": import_id,
                "filename": filename,
            }, severity="info")
            return

        show_all = getattr(args, "all", False)
        all_entries = self.store.get_all_imports_with_index(batch_id)
        if not show_all:
            display_entries = all_entries
        else:
            display_entries = all_entries

        print("=" * 98)
        title = "导入历史记录 (全部)" if show_all else "导入历史记录"
        print(f"📋 {title} (共 {len(display_entries)} 条):")
        print("=" * 98)
        print(f"{'序号':<6} {'状态':<8} {'轮次':<6} {'类型':<12} {'文件名':<30} {'事件数':<8} {'导入时间'}")
        print("-" * 98)

        if not display_entries:
            print("   (暂无导入记录)")
        else:
            for entry in display_entries:
                if not show_all and entry.get("status") == "undone":
                    continue
                idx = entry.get("display_index", 0)
                status_icon = "✅" if entry.get("status") == "active" else "↩️"
                status_text = f"{status_icon} {entry.get('status', '?')}"
                stype = self.store._infer_source_type(entry.get("filename", ""))
                ec = entry.get("event_count", 0)
                fname = entry.get("filename", "")
                ts = entry.get("imported_at", "")[:19].replace("T", " ")
                rn = entry.get("round_number", "?")
                print(f"{idx:<6} {status_text:<8} {rn:<6} {stype:<12} {fname:<30} {ec:<8} {ts}")
                if entry.get("status") == "undone":
                    undone_ts = entry.get("undone_at", "")[:19].replace("T", " ")
                    removed = entry.get("removed_event_count_actual", 0)
                    print(f"       撤销于: {undone_ts}  实际删除事件: {removed}")

        print()
        print("💡 提示:")
        print("   使用 --detail <序号> 查看指定导入的详情")
        print("   使用 --undo <序号>   撤销指定导入")
        print("   使用 --restore <序号> 恢复指定已撤销的导入")
        print("   使用 --all            显示含已撤销的全部记录")
        print("   undo-import / restore-import / import-detail 也支持 --index <序号>")

    def _print_import_detail_block(self, detail: Dict, desc: str) -> None:
        status_label = {"active": "✅ 激活", "undone": "↩️ 已撤销"}.get(
            detail.get("status", "unknown"), detail.get("status", "未知")
        )

        print("=" * 78)
        print(f"📋 导入详情 - {desc}")
        print("=" * 78)
        print(f"   文件名:       {detail.get('filename', '')}")
        print(f"   导入轮次:     {detail.get('round_number', '?')}")
        print(f"   导入ID:       {detail.get('import_id', '')}")
        print(f"   状态:         {status_label}")
        print(f"   导入时间:     {detail.get('imported_at', '')}")
        if detail.get("status") == "undone":
            print(f"   撤销时间:     {detail.get('undone_at', '')}")
            print(f"   实际删除事件: {detail.get('removed_event_count_actual', 0)}")
            print(f"   孤留事件(去重): {len(detail.get('orphaned_events_due_to_dedup', []))}")
            if detail.get("restored_count", 0) > 0:
                print(f"   历史恢复次数: {detail.get('restored_count', 0)}")
                print(f"   最后恢复时间: {detail.get('last_restored_at', '')}")
        print()
        print(f"   导入时声明:   {detail.get('event_count', 0)} 事件, {detail.get('error_count', 0)} 错误")
        print(f"   实际匹配事件: {detail.get('matched_event_count', 0)} 条")
        print(f"   文件哈希:     {detail.get('file_hash', '')[:16]}...")
        print(f"   绝对路径:     {detail.get('abs_path', '')}")
        print()

        matched = detail.get("matched_events_sample", [])
        if matched:
            print(f"📝 关联事件样本 (最多10条):")
            print(f"   {'状态':<10} {'严重级别':<10} {'时间':<20} 消息")
            print("-" * 78)
            for ev in matched:
                active_marker = "✅" if ev.get("active_in_event") else "⚠️"
                ts = ev.get("timestamp", "")[:19].replace("T", " ")
                msg = ev.get("message", "")[:50]
                print(f"   {active_marker} {ev.get('status',''):<8} {ev.get('severity',''):<8} {ts} {msg}")
                print(f"      ID: {ev.get('id', '')}")
            print()

    def cmd_timeline(self, args) -> None:
        batch_id = self._require_batch()
        events = self.store.load_events(batch_id)
        config = self.store.load_config(batch_id)
        timeline = Timeline(events, config)

        if args.status:
            statuses = []
            for s in args.status:
                if s == "unconfirmed":
                    statuses.append(EventStatus.UNCONFIRMED)
                elif s == "confirmed":
                    statuses.append(EventStatus.CONFIRMED)
                elif s == "root" or s == "root_cause":
                    statuses.append(EventStatus.ROOT_CAUSE)
                elif s == "noise":
                    statuses.append(EventStatus.NOISE)
            timeline = timeline.filter_by_status(statuses)

        if args.severity:
            severities = [Severity(s.upper()) for s in args.severity if s.upper() in [v.value for v in Severity]]
            timeline = timeline.filter_by_severity(severities)

        if args.source:
            sources = []
            for s in args.source:
                if s == "log":
                    sources.append(EventSource.LOG)
                elif s == "alert":
                    sources.append(EventSource.ALERT)
                elif s == "note":
                    sources.append(EventSource.NOTE)
            timeline = timeline.filter_by_source(sources)

        if args.search:
            timeline = timeline.search(args.search)

        sorted_events = timeline.sort(reverse=args.reverse)

        if not sorted_events:
            print("📭 没有找到符合条件的事件")
            return

        start = args.offset or 0
        end = start + (args.limit or len(sorted_events))
        paged_events = sorted_events[start:end]

        print(f"📋 时间线 (共 {len(sorted_events)} 条事件，显示 {start + 1}-{min(end, len(sorted_events))}):")
        print()
        for i, event in enumerate(paged_events, start=start + 1):
            self._print_event_short(event, i)
            print()

        if args.gaps:
            gaps = timeline.get_gaps()
            if gaps:
                print(f"\n⏱️  时间缺口 (>{config.gap_threshold_seconds}s):")
                for start_g, end_g, diff in gaps:
                    print(f"   {start_g.strftime('%H:%M:%S')} -> {end_g.strftime('%H:%M:%S')} ({int(diff.total_seconds())}s)")

    def cmd_label(self, args) -> None:
        batch_id = self._require_batch()
        events = self.store.load_events(batch_id)

        status_map = {
            "unconfirmed": EventStatus.UNCONFIRMED,
            "confirmed": EventStatus.CONFIRMED,
            "root": EventStatus.ROOT_CAUSE,
            "root_cause": EventStatus.ROOT_CAUSE,
            "noise": EventStatus.NOISE,
            "待确认": EventStatus.UNCONFIRMED,
            "已确认": EventStatus.CONFIRMED,
            "根因": EventStatus.ROOT_CAUSE,
            "噪声": EventStatus.NOISE,
        }

        if args.status not in status_map:
            print(f"❌ 无效的状态: {args.status}", file=sys.stderr)
            print(f"   可用值: {list(status_map.keys())}", file=sys.stderr)
            sys.exit(1)

        target_status = status_map[args.status]
        event_ids = args.event_ids

        updated = 0
        for eid in event_ids:
            if args.notes:
                event = self.store.set_event_status_and_notes(batch_id, eid, target_status, args.notes)
            else:
                event = self.store.set_event_status(batch_id, eid, target_status)
            if event:
                updated += 1
                icon = STATUS_ICONS.get(target_status, " ")
                print(f"✅ {icon} {eid[:8]}... -> {target_status.value}")
                if args.notes:
                    print(f"   备注: {args.notes}")
            else:
                print(f"❌ 未找到事件: {eid}")

        print(f"\n📝 已更新 {updated}/{len(event_ids)} 个事件的状态")

    def cmd_note(self, args) -> None:
        batch_id = self._require_batch()
        event = self.store.set_event_notes(batch_id, args.event_id, args.notes)
        if event:
            print(f"✅ 已更新事件备注: {args.event_id[:8]}...")
            if args.notes:
                print(f"   备注: {args.notes}")
        else:
            print(f"❌ 未找到事件: {args.event_id}", file=sys.stderr)
            sys.exit(1)

    def cmd_config(self, args) -> None:
        batch_id = self._require_batch()
        config = self.store.load_config(batch_id)

        if args.show:
            import json
            print(json.dumps(config.to_dict(), ensure_ascii=False, indent=2))
            return

        if getattr(args, "check_conflict", False):
            print("🔍 正在检查配置变更冲突风险...")
            proposed = copy.deepcopy(config)
            if args.dedup_window is not None:
                proposed.dedup_window_seconds = args.dedup_window
            if args.gap_threshold is not None:
                proposed.gap_threshold_seconds = args.gap_threshold
            if args.bump_version:
                parts = proposed.rule_version.split(".")
                parts[-1] = str(int(parts[-1]) + 1)
                proposed.rule_version = ".".join(parts)
            conflict = self.store.get_config_conflict_reasons(batch_id, proposed)
            if conflict.get("has_conflict"):
                print(f"⚠️  检测到 {len(conflict.get('conflicts', []))} 处配置变更冲突风险:")
                for c in conflict.get("conflicts", []):
                    print(f"   ⚠️  {c.get('label', '')}:")
                    print(f"      当前值: {c.get('current')}  →  新值: {c.get('new')}")
                    print(f"      原因: {c.get('reason', '')}")
                print()
                print("💡 建议:")
                for rec in conflict.get("recommendations", []):
                    print(f"   • {rec}")
            else:
                print("✅ 未检测到冲突风险")
            if not any([args.dedup_window is not None, args.gap_threshold is not None,
                       args.add_severity, args.add_time_format, args.bump_version]):
                return

        changed = False
        if args.dedup_window is not None:
            config.dedup_window_seconds = args.dedup_window
            changed = True
            print(f"✅ 去重窗口设置为 {args.dedup_window}s")

        if args.gap_threshold is not None:
            config.gap_threshold_seconds = args.gap_threshold
            changed = True
            print(f"✅ 缺口阈值设置为 {args.gap_threshold}s")

        if args.add_severity:
            parts = args.add_severity.split("=", 1)
            if len(parts) == 2:
                try:
                    sev = Severity(parts[1].upper())
                    config.add_severity_mapping(parts[0], sev)
                    changed = True
                    print(f"✅ 添加严重级别映射: {parts[0]} -> {sev.value}")
                except ValueError:
                    print(f"❌ 无效的严重级别: {parts[1]}", file=sys.stderr)

        if args.add_time_format:
            config.add_timestamp_format(args.add_time_format)
            changed = True
            print(f"✅ 添加时间格式: {args.add_time_format}")

        if args.bump_version:
            parts = config.rule_version.split(".")
            parts[-1] = str(int(parts[-1]) + 1)
            config.rule_version = ".".join(parts)
            changed = True
            print(f"✅ 规则版本升级为: {config.rule_version}")

        if changed:
            self.store.save_config(batch_id, config)
            events = self.store.load_events(batch_id)
            if events:
                print("\n🔄 规则已变更，正在重新去重归并...")
                deduped, _ = dedupe_events(events, config)
                self.store.save_events(batch_id, deduped)
                print(f"   ✅ 完成，当前事件数: {len(deduped)}")
                after_count = len(deduped)
                before_count = len(events)
                if before_count != after_count:
                    print(f"   ℹ️  配置变更导致事件合并: {before_count} → {after_count} (减少 {before_count - after_count})")
                    self.store.log_change(batch_id, "config_change_dedup_effect", {
                        "before": before_count,
                        "after": after_count,
                        "diff": after_count - before_count,
                    }, severity="warning")
        else:
            print("ℹ️  没有修改任何配置，使用 --show 查看当前配置")

    def cmd_phase(self, args) -> None:
        batch_id = self._require_batch()
        config = self.store.load_config(batch_id)

        if args.list:
            if not config.phases:
                print("📭 没有配置任何阶段")
            else:
                print(f"🎬 阶段列表 ({len(config.phases)} 个):")
                for i, p in enumerate(config.phases, 1):
                    start = p.get("start_time", "-")
                    end = p.get("end_time", "-")
                    print(f"  {i}. {p['name']}: {start} ~ {end}")
                    if p.get("description"):
                        print(f"     {p['description']}")
            return

        if args.add:
            name = args.add
            start_time = None
            end_time = None
            if args.start:
                from .importers import parse_timestamp
                start_time = parse_timestamp(args.start, config.timestamp_formats)
            if args.end:
                from .importers import parse_timestamp
                end_time = parse_timestamp(args.end, config.timestamp_formats)
            config.add_phase(name, start_time, end_time, args.description or "")
            self.store.save_config(batch_id, config)
            print(f"✅ 已添加阶段: {name}")
            return

        if args.clear:
            config.phases = []
            self.store.save_config(batch_id, config)
            print("✅ 已清空所有阶段配置")
            return

    def cmd_export(self, args) -> None:
        batch_id = self._require_batch()
        events = self.store.load_events(batch_id)
        config = self.store.load_config(batch_id)
        meta = self.store.get_batch_meta(batch_id)
        timeline = Timeline(events, config)

        if not events:
            print("⚠️  批次中没有事件，报告将为空")

        history_data = self._collect_export_history_data(batch_id)

        fmt = args.format or "markdown"
        content = export_report(timeline, config, fmt, meta, history_data)

        if args.output:
            output_path = args.output
        else:
            ext = ".csv" if fmt.lower() == "csv" else ".md"
            filename = f"report_{meta['id']}{ext}"
            output_path = filename

        if args.save_internal:
            internal_path = self.store.save_export(batch_id, fmt, content, os.path.basename(output_path))
            print(f"💾 已保存到内部存储: {internal_path}")

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)

        print(f"✅ 已导出报告: {output_path}")
        print(f"   格式: {fmt.upper()}  大小: {len(content)} 字符")
        if history_data and history_data.get("rounds"):
            print(f"   已包含 {len(history_data['rounds'])} 轮操作历史摘要")

        if getattr(args, "verify", False):
            print()
            print("🔍 正在核对导出内容与实际数据...")
            with open(output_path, "r", encoding="utf-8") as f:
                exported_content = f.read()
            verify = self.store.verify_export_consistency(batch_id, exported_content, fmt)
            for check in verify.get("checks", []):
                print(f"   ℹ️  {check}")
            if verify.get("consistent"):
                print("✅ 导出内容与实际数据一致")
            else:
                print("❌ 导出内容与实际数据不一致")
                for mm in verify.get("mismatches", []):
                    if "field" in mm:
                        reason = mm.get("reason", "")
                        print(f"   ⚠️  {mm['field']}: 导出={mm['export']} 实际={mm['actual']} (差 {mm['diff']:+d})")
                        if reason:
                            print(f"       原因: {reason}")
                    elif "type" in mm:
                        print(f"   ⚠️  {mm['type']}: {mm.get('details', [])[:2]}")

        consistency = self.store.check_snapshot_consistency(batch_id)
        if not consistency.get("consistent"):
            print()
            print(f"⚠️  注意: 导出时发现 {len(consistency.get('inconsistencies', []))} 处快照不一致")
            if getattr(args, "auto_fix", False):
                print("   正在自动修复...")
                fix = self.store.fix_snapshot_inconsistencies(batch_id)
                if fix.get("fixed"):
                    print(f"   ✅ 已修复")
                else:
                    print(f"   ❌ 修复失败: {fix.get('message', '')}")

    def _collect_export_history_data(self, batch_id: str) -> Dict:
        try:
            rounds = self.store.list_rounds(batch_id, limit=100)
            exports = self.store.get_exports(batch_id)
            consistency = self.store.check_snapshot_consistency(batch_id)

            recent_changes = []
            for r in rounds[:20]:
                summary = r.get("summary", [])
                for s in summary:
                    recent_changes.append(s)

            return {
                "rounds": rounds,
                "recent_changes": recent_changes,
                "exports": exports[:10],
                "consistency": consistency,
            }
        except Exception as e:
            self.store.log_change(batch_id, "export_history_collection_failed", {
                "error": str(e),
            }, severity="warning")
            return {}

    def cmd_show(self, args) -> None:
        batch_id = self._require_batch()
        events = self.store.load_events(batch_id)

        for eid in args.event_ids:
            event = None
            for e in events:
                if e.id == eid or e.id.startswith(eid):
                    event = e
                    break
            if not event:
                print(f"❌ 未找到事件: {eid}", file=sys.stderr)
                continue

            sev_icon = SEVERITY_ICONS.get(event.severity, " ")
            status_icon = STATUS_ICONS.get(event.status, " ")
            src_label = {"log": "应用日志", "alert": "告警", "note": "人工备注"}.get(event.source.value, event.source.value)

            print()
            print(f"{'='*60}")
            print(f"  {sev_icon} [{event.severity.value}] {status_icon} [{event.status.value}]")
            print(f"  {event.message}")
            print(f"{'='*60}")
            print(f"  ID:         {event.id}")
            print(f"  时间:       {event.timestamp.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
            print(f"  来源:       {src_label}")
            print(f"  来源文件:   {event.source_file}:{event.line_number}")
            print(f"  创建时间:   {event.created_at.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"  更新时间:   {event.updated_at.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"  去重键:     {event.dedup_key}")
            if event.notes:
                print(f"  备注:       {event.notes}")
            if event.raw_events and len(event.raw_events) > 1:
                print(f"  合并来源:   {len(event.raw_events)} 条原始事件")
                for re in event.raw_events:
                    print(f"    - {re.source_file}:{re.line_number}")
            if event.extra:
                print(f"  扩展字段:")
                for k, v in event.extra.items():
                    print(f"    {k}: {v}")
            print()

    def cmd_errors(self, args) -> None:
        batch_id = self._require_batch()
        errors = self.store.load_parse_errors(batch_id)

        if not errors:
            print("✅ 没有解析错误")
            return

        print(f"⚠️  共 {len(errors)} 个解析错误:")
        print()
        for i, err in enumerate(errors, 1):
            print(f"  {i}. 📄 {err.source_file}:{err.line_number}")
            print(f"     类型: {err.error_type}")
            print(f"     信息: {err.error_message}")
            if err.raw_content and args.verbose:
                print(f"     内容: {err.raw_content[:100]}")
            print()

    def cmd_label_history(self, args) -> None:
        batch_id = self._require_batch()
        history = self.store.get_label_history(batch_id)

        if not history:
            print("📭 没有标注历史记录")
            return

        limit = args.limit or len(history)
        display = history[-limit:][::-1]

        print(f"📜 标注历史记录 (共 {len(history)} 条，最近 {len(display)} 条):")
        print()
        for i, h in enumerate(display, 1):
            op_desc = {
                "set_status": "修改状态",
                "set_notes": "修改备注",
                "set_both": "修改状态+备注",
            }.get(h.operation, h.operation)
            print(f"  [{h.id}] {h.created_at.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"      操作: {op_desc}  事件ID: {h.event_id[:16]}...")
            if h.old_status or h.new_status:
                old_s = h.old_status.value if h.old_status else "无"
                new_s = h.new_status.value if h.new_status else "无"
                print(f"      状态: {old_s} -> {new_s}")
            if h.old_notes is not None or h.new_notes is not None:
                old_n = (h.old_notes[:30] + "...") if h.old_notes and len(h.old_notes) > 30 else (h.old_notes or "空")
                new_n = (h.new_notes[:30] + "...") if h.new_notes and len(h.new_notes) > 30 else (h.new_notes or "空")
                print(f"      备注: {old_n} -> {new_n}")
            print(f"      规则版本: {h.config_version}")
            print()

    def cmd_undo_label(self, args) -> None:
        batch_id = self._require_batch()
        history = self.store.get_label_history(batch_id)

        if not history:
            print("ℹ️  没有可撤销的标注记录（与导入撤销是独立的功能）")
            return

        last = self.store.undo_last_label(batch_id)
        if not last:
            print("ℹ️  没有可撤销的标注记录")
            return

        op_desc = {
            "set_status": "修改状态",
            "set_notes": "修改备注",
            "set_both": "修改状态+备注",
        }.get(last.operation, last.operation)

        print(f"↩️  已撤销标注操作:")
        print(f"   操作类型: {op_desc}")
        print(f"   事件ID:   {last.event_id}")
        print(f"   操作时间: {last.created_at.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"   规则版本: {last.config_version}")

        if last.operation in ("set_status", "set_both"):
            old_s = last.old_status.value if last.old_status else "无"
            new_s = last.new_status.value if last.new_status else "无"
            print(f"   状态已恢复: {new_s} -> {old_s}")

        if last.operation in ("set_notes", "set_both"):
            old_n = (last.old_notes[:50] + "...") if last.old_notes and len(last.old_notes) > 50 else (last.old_notes or "空")
            new_n = (last.new_notes[:50] + "...") if last.new_notes and len(last.new_notes) > 50 else (last.new_notes or "空")
            print(f"   备注已恢复: {new_n} -> {old_n}")

        remaining = len(self.store.get_label_history(batch_id))
        print(f"   剩余可撤销次数: {remaining}")

    def cmd_audit_center(self, args) -> None:
        batch_id = self._require_batch()
        config = self.store.load_config(batch_id)
        audit_rules = config.audit_rules

        if args.show_rules:
            self._print_audit_rules(audit_rules)
            return

        if args.enable_check or args.disable_check or args.set_tolerance:
            self._update_audit_rules(batch_id, config, args)
            return

        if args.verify_export:
            self._verify_export_file(batch_id, args.verify_export, args.format)
            return

        if args.export_audit:
            self._export_with_audit(batch_id, args)
            return

        if args.undo is not None:
            self._audit_undo_import(batch_id, args.undo, args.force)
            return

        if args.restore is not None:
            self._audit_restore_import(batch_id, args.restore, args.force)
            return

        if args.detail is not None:
            self._audit_show_detail(batch_id, args.detail)
            return

        if args.list or True:
            self._audit_show_list(batch_id, args.limit)

    def _print_audit_rules(self, audit_rules) -> None:
        print("=" * 78)
        print("📋 当前核对规则配置")
        print("=" * 78)
        print(f"   规则总开关:        {'✅ 启用' if audit_rules.enabled else '❌ 禁用'}")
        print()
        print("   核对项配置:")
        print(f"   • 空导出检查:      {'✅ 启用' if audit_rules.check_empty_export else '❌ 禁用'} (容忍度: {audit_rules.empty_export_tolerance})")
        print(f"   • 数量不一致检查:  {'✅ 启用' if audit_rules.check_event_count_mismatch else '❌ 禁用'} (容忍度: {audit_rules.count_mismatch_tolerance})")
        print(f"   • 重复恢复检查:    {'✅ 启用' if audit_rules.check_duplicate_restore else '❌ 禁用'}")
        print(f"   • 导入冲突检查:    {'✅ 启用' if audit_rules.check_import_conflict else '❌ 禁用'}")
        print()
        print("   其他配置:")
        print(f"   • 允许强制重导:    {'✅ 是' if audit_rules.allow_force_reimport else '❌ 否'}")
        print(f"   • 自动修复快照:    {'✅ 是' if audit_rules.auto_fix_snapshot else '❌ 否'}")
        print(f"   • 记录变更日志:    {'✅ 是' if audit_rules.log_to_change_log else '❌ 否'}")
        print(f"   • 日志级别:        {audit_rules.log_level}")
        print()
        print("   导出事件数匹配模式:")
        for pattern in audit_rules.export_count_patterns:
            print(f"   • {pattern}")
        print()

    def _update_audit_rules(self, batch_id: str, config, args) -> None:
        updated = False

        if args.enable_check:
            checks = [args.enable_check] if args.enable_check != "all" else ["empty_export", "event_count_mismatch", "duplicate_restore", "import_conflict"]
            for check in checks:
                if check == "empty_export":
                    config.audit_rules.check_empty_export = True
                elif check == "event_count_mismatch":
                    config.audit_rules.check_event_count_mismatch = True
                elif check == "duplicate_restore":
                    config.audit_rules.check_duplicate_restore = True
                elif check == "import_conflict":
                    config.audit_rules.check_import_conflict = True
                print(f"✅ 已启用核对规则: {check}")
                updated = True

        if args.disable_check:
            checks = [args.disable_check] if args.disable_check != "all" else ["empty_export", "event_count_mismatch", "duplicate_restore", "import_conflict"]
            for check in checks:
                if check == "empty_export":
                    config.audit_rules.check_empty_export = False
                elif check == "event_count_mismatch":
                    config.audit_rules.check_event_count_mismatch = False
                elif check == "duplicate_restore":
                    config.audit_rules.check_duplicate_restore = False
                elif check == "import_conflict":
                    config.audit_rules.check_import_conflict = False
                print(f"✅ 已禁用核对规则: {check}")
                updated = True

        if args.set_tolerance:
            parts = args.set_tolerance.split("=", 1)
            if len(parts) == 2:
                check_name = parts[0].strip()
                try:
                    tolerance = int(parts[1].strip())
                    if check_name == "empty_export":
                        config.audit_rules.empty_export_tolerance = tolerance
                    elif check_name == "event_count_mismatch":
                        config.audit_rules.count_mismatch_tolerance = tolerance
                    else:
                        print(f"❌ 未知的检查项: {check_name}", file=sys.stderr)
                        return
                    print(f"✅ 已设置 {check_name} 容忍度为: {tolerance}")
                    updated = True
                except ValueError:
                    print(f"❌ 容忍度必须是整数: {parts[1]}", file=sys.stderr)
                    return
            else:
                print(f"❌ 格式错误，请使用 CHECK=TOLERANCE 格式", file=sys.stderr)
                return

        if updated:
            self.store.save_config(batch_id, config)
            print()
            print("💡 核对规则已更新，新规则将在下次核对时生效")

    def _verify_export_file(self, batch_id: str, file_path: str, fmt: str) -> None:
        path = Path(file_path)
        if not path.exists():
            print(f"❌ 文件不存在: {file_path}", file=sys.stderr)
            return

        print(f"🔍 正在核对导出文件: {path.name}")
        print()

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        result = self.store.verify_export_consistency(batch_id, content, fmt)

        for check in result.get("checks", []):
            print(f"   ℹ️  {check}")
        print()

        if result.get("consistent"):
            print("✅ 导出内容与库内数据一致，核对通过")
        else:
            print("❌ 核对失败，发现不一致:")
            print()
            for mm in result.get("mismatches", []):
                severity = mm.get("severity", "warning")
                sev_icon = "❌" if severity == "error" else "⚠️"
                if "field" in mm:
                    check_type = mm.get("check_type", "unknown")
                    reason = mm.get("reason", "")
                    print(f"   {sev_icon} [{check_type}] {reason}")
                    if "export" in mm and "actual" in mm:
                        print(f"       导出值: {mm['export']}  实际值: {mm['actual']}  差异: {mm.get('diff', 0):+d}")
                elif "type" in mm:
                    print(f"   {sev_icon} [{mm.get('type', 'unknown')}] {mm.get('reason', '')}")
            print()
            print(f"📝 未通过的核对项: {', '.join(result.get('failed_checks', []))}")
            self.store.log_change(batch_id, "audit_verify_export_failed", {
                "file": file_path,
                "failed_checks": result.get("failed_checks", []),
                "mismatches": result.get("mismatches", []),
            }, severity="error")
            sys.exit(1)

    def _export_with_audit(self, batch_id: str, args) -> None:
        events = self.store.load_events(batch_id)
        config = self.store.load_config(batch_id)
        meta = self.store.get_batch_meta(batch_id)
        timeline = Timeline(events, config)

        if not events:
            print("⚠️  批次中没有事件，报告将为空")

        history_data = self._collect_export_history_data(batch_id)

        fmt = args.format
        content = export_report(timeline, config, fmt, meta, history_data)

        if args.output:
            output_path = args.output
        else:
            ext = ".csv" if fmt.lower() == "csv" else ".md"
            output_path = f"audit_report_{meta['id']}{ext}"

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)

        print(f"✅ 已导出报告: {output_path}")
        print(f"   格式: {fmt.upper()}  大小: {len(content)} 字符")
        print()
        print("🔍 正在执行导出后核对...")
        print()

        verify_result = self.store.verify_export_consistency(batch_id, content, fmt)

        for check in verify_result.get("checks", []):
            print(f"   ℹ️  {check}")
        print()

        if verify_result.get("consistent"):
            print("✅ 导出核对通过，数据一致")
        else:
            print("❌ 导出核对失败!")
            print()
            for mm in verify_result.get("mismatches", []):
                if "check_type" in mm:
                    print(f"   ❌ [{mm['check_type']}] {mm.get('reason', '')}")
                elif "type" in mm:
                    print(f"   ⚠️  [{mm.get('type', 'unknown')}] {mm.get('reason', '')}")
            print()
            self.store.log_change(batch_id, "audit_export_with_audit_failed", {
                "output_file": output_path,
                "failed_checks": verify_result.get("failed_checks", []),
            }, severity="error")
            sys.exit(1)

    def _audit_show_list(self, batch_id: str, limit: int) -> None:
        operations = self.store.get_audit_operations_list(batch_id, limit=limit)

        print("=" * 110)
        print(f"📋 导入导出核对中心 - 最近操作列表 (共 {len(operations)} 条，最近 {limit} 条)")
        print("=" * 110)
        print(f"{'序号':<6} {'操作类型':<14} {'状态':<10} {'文件名':<24} {'事件数':<8} {'匹配数':<8} {'最后处理时间'}")
        print("-" * 110)

        if not operations:
            print("   (暂无操作记录，使用 import 命令导入文件)")
        else:
            for op in operations:
                idx = op.get("display_index", 0)
                op_type = op.get("operation_type", "未知")
                status = op.get("status", "unknown")
                status_icon = "✅" if status == "active" else "↩️"
                fname = op.get("filename", "")[:22]
                ev_count = op.get("event_count", 0)
                matched = op.get("matched_event_count", 0)
                last_ts = op.get("last_processed_at", "")[:19].replace("T", " ")

                print(f"{idx:<6} {op_type:<14} {status_icon} {status:<8} {fname:<24} {ev_count:<8} {matched:<8} {last_ts}")

        print()
        print("💡 操作提示:")
        print("   audit-center --detail <序号>   查看指定操作的详细信息")
        print("   audit-center --undo <序号>     撤销指定导入")
        print("   audit-center --restore <序号>  恢复指定已撤销的导入")
        print("   audit-center --export-audit    导出报告并自动核对")
        print("   audit-center --verify-export <文件>  核对已有导出文件")

    def _audit_show_detail(self, batch_id: str, display_index: int) -> None:
        detail = self.store.get_audit_operation_detail(batch_id, display_index)
        if not detail:
            print(f"❌ 序号 {display_index} 对应的操作记录不存在", file=sys.stderr)
            return

        status_label = {"active": "✅ 激活", "undone": "↩️ 已撤销"}.get(
            detail.get("status", "unknown"), detail.get("status", "未知")
        )

        print("=" * 90)
        print(f"📋 操作详情 - 序号 {display_index}")
        print("=" * 90)
        print(f"   记录标识(import_id): {detail.get('import_id', '')}")
        print(f"   操作类型:           {detail.get('operation_type', '未知')}")
        print(f"   状态:               {status_label}")
        print(f"   导入轮次:           {detail.get('round_number', '?')}")
        print()
        print("📊 事件统计:")
        event_stats = detail.get("event_stats", {})
        print(f"   关联事件总数:       {event_stats.get('total', 0)}")
        by_status = event_stats.get("by_status", {})
        if by_status:
            status_str = "  ".join([f"{k}:{v}" for k, v in by_status.items()])
            print(f"   按状态分布:         {status_str}")
        by_severity = event_stats.get("by_severity", {})
        if by_severity:
            sev_str = "  ".join([f"{k}:{v}" for k, v in by_severity.items()])
            print(f"   按严重级别:         {sev_str}")
        print()
        print("📁 来源文件:")
        print(f"   文件名:             {detail.get('filename', '')}")
        print(f"   绝对路径:           {detail.get('source_file', '')}")
        print(f"   文件哈希:           {detail.get('file_hash', '')[:16]}...")
        print(f"   导入时声明:         {detail.get('event_count', 0)} 事件, {detail.get('error_count', 0)} 错误")
        print(f"   实际匹配事件:       {detail.get('matched_event_count', 0)} 条")
        print()
        print("⏰ 时间线:")
        if detail.get("imported_at"):
            print(f"   导入时间:           {detail.get('imported_at', '')[:19].replace('T', ' ')}")
        if detail.get("status") == "undone" and detail.get("undone_at"):
            print(f"   撤销时间:           {detail.get('undone_at', '')[:19].replace('T', ' ')}")
        if detail.get("restored_count", 0) > 0:
            print(f"   恢复次数:           {detail.get('restored_count', 0)}")
            if detail.get("last_restored_at"):
                print(f"   最后恢复时间:       {detail.get('last_restored_at', '')[:19].replace('T', ' ')}")
        print()
        print("📝 最近一次处理结果:")
        last_result = detail.get("last_processed_result")
        if last_result:
            result_icon = "✅ 成功" if last_result.get("result") == "success" else "❌ 失败"
            print(f"   动作:               {last_result.get('action', '未知')}")
            print(f"   结果:               {result_icon}")
            print(f"   时间:               {last_result.get('timestamp', '')[:19].replace('T', ' ')}")
            action_details = last_result.get("details", {})
            if action_details:
                for k, v in list(action_details.items())[:5]:
                    if v:
                        print(f"   {k:<20} {v}")
        else:
            print("   (暂无处理结果记录)")
        print()

        related_logs = detail.get("recent_related_logs", [])
        if related_logs:
            print("📜 最近相关日志:")
            for log in related_logs[:5]:
                sev_icon = {"info": "ℹ️", "warning": "⚠️", "error": "❌"}.get(log.get("severity", "info"), "  ")
                ts = log.get("created_at", "")[:19].replace("T", " ")
                print(f"   {sev_icon} [{ts}] {log.get('change_type', '')}")

        print()

    def _audit_undo_import(self, batch_id: str, display_index: int, force: bool) -> None:
        resolved = self.store.resolve_import_by_display_index(batch_id, display_index)
        if not resolved:
            print(f"❌ 序号 {display_index} 对应的导入记录不存在", file=sys.stderr)
            return

        import_id = resolved.get("import_id")
        status = resolved.get("status")
        filename = resolved.get("filename", "")

        print(f"📍 序号 {display_index} → 导入ID {import_id[:16]}...")
        print(f"   文件: {filename}")
        print(f"   当前状态: {status}")
        print()

        if status != "active":
            print(f"❌ 该导入状态为「{status}」，无法撤销", file=sys.stderr)
            self.store.log_change(batch_id, "audit_undo_invalid_status", {
                "display_index": display_index,
                "import_id": import_id,
                "filename": filename,
                "current_status": status,
                "expected_status": "active",
            }, severity="error")
            sys.exit(1)

        config = self.store.load_config(batch_id)
        if config.audit_rules.check_import_conflict and not force:
            events = self.store.load_events(batch_id)
            bound_ids = set(resolved.get("event_ids", []))
            has_dependencies = False
            for e in events:
                if e.id in bound_ids and len(e.import_ids) > 1:
                    has_dependencies = True
                    break
            if has_dependencies:
                print("⚠️  冲突检测: 该导入的部分事件被其他导入引用（去重合并）")
                print("   撤销后这些事件将保留（孤儿保护机制）")
                self.store.log_change(batch_id, "audit_undo_conflict_warning", {
                    "display_index": display_index,
                    "import_id": import_id,
                    "filename": filename,
                }, severity="warning")

        print(f"↩️  正在撤销导入 (序号 {display_index}) ...")
        result = self.store.undo_last_import(batch_id, import_id=import_id)
        if not result:
            print("❌ 撤销失败", file=sys.stderr)
            return

        removed = result.get("removed_event_count_actual", 0)
        bound = len(result.get("event_ids", []))
        orphans = len(result.get("orphaned_events_due_to_dedup", []))
        round_num = result.get("round_number", "?")

        print(f"✅ 已撤销导入: {filename} (轮次 {round_num})")
        print(f"   导入时绑定事件数: {bound}")
        print(f"   实际删除事件数:   {removed}")
        if orphans > 0:
            print(f"   因去重合并保留事件: {orphans} (这些事件还被其他导入引用)")
        print(f"   删除解析错误数:   {result.get('removed_error_count_actual', 0)}")
        real_events = len(self.store.load_events(batch_id))
        print(f"   当前批次事件总数: {real_events}")

        self.store.log_change(batch_id, "audit_undo_import_success", {
            "display_index": display_index,
            "import_id": import_id,
            "filename": filename,
            "removed_events": removed,
        }, severity="info")

    def _audit_restore_import(self, batch_id: str, display_index: int, force: bool) -> None:
        resolved = self.store.resolve_import_by_display_index(batch_id, display_index)
        if not resolved:
            print(f"❌ 序号 {display_index} 对应的导入记录不存在", file=sys.stderr)
            return

        import_id = resolved.get("import_id")
        status = resolved.get("status")
        filename = resolved.get("filename", "")

        print(f"📍 序号 {display_index} → 导入ID {import_id[:16]}...")
        print(f"   文件: {filename}")
        print(f"   当前状态: {status}")
        print()

        if status != "undone":
            print(f"❌ 该导入状态为「{status}」，无需恢复", file=sys.stderr)
            self.store.log_change(batch_id, "audit_restore_invalid_status", {
                "display_index": display_index,
                "import_id": import_id,
                "filename": filename,
                "current_status": status,
                "expected_status": "undone",
            }, severity="error")
            sys.exit(1)

        if not force:
            conflict = self.store.check_duplicate_restore(batch_id, import_id)
            if conflict.get("has_conflict"):
                conflict_type = conflict.get("conflict_type", "unknown")
                print(f"❌ 冲突检测失败: {conflict.get('message', '')}")
                print(f"   冲突类型: {conflict_type}")
                details = conflict.get("details", {})
                if details:
                    for k, v in details.items():
                        if v:
                            print(f"   {k}: {v}")
                print()
                print("💡 使用 --force 参数忽略冲突强制执行")
                self.store.log_change(batch_id, "audit_restore_conflict_blocked", {
                    "display_index": display_index,
                    "import_id": import_id,
                    "filename": filename,
                    "conflict_type": conflict_type,
                }, severity="warning")
                sys.exit(1)

        print(f"↪️  正在恢复导入 (序号 {display_index}) ...")
        result = self.store.restore_import(batch_id, import_id=import_id)
        if not result:
            print("❌ 恢复失败", file=sys.stderr)
            return

        round_num = result.get("round_number", "?")
        restore_count = result.get("restored_count", 1)
        restored_events = result.get("restored_event_count", 0)
        already_present = result.get("already_present_event_count", 0)

        print(f"✅ 已恢复导入: {filename} (轮次 {round_num})")
        print(f"   恢复次数累计:     {restore_count}")
        print(f"   恢复关联事件数:   {restored_events}")
        if already_present > 0:
            print(f"   已存在于库中:     {already_present} (因后续导入或去重已存在)")
        print(f"   恢复解析错误数:   {result.get('restored_error_count', 0)}")
        real_events = len(self.store.load_events(batch_id))
        print(f"   当前批次事件总数: {real_events}")

        self.store.log_change(batch_id, "audit_restore_import_success", {
            "display_index": display_index,
            "import_id": import_id,
            "filename": filename,
            "restored_events": restored_events,
        }, severity="info")

    def cmd_history(self, args) -> None:
        batch_id = self._require_batch()

        if args.recover:
            self._recover_to_snapshot(batch_id, args.recover, skip_confirmation=args.yes)
            return

        if args.repair:
            self._repair_snapshot(batch_id, args.repair)
            return

        if args.check_lag:
            self._check_and_print_database_lag(batch_id)
            return

        if args.check_config:
            self._check_and_print_config_conflict(batch_id)
            return

        if args.rounds:
            self._print_rounds(batch_id, args.limit)
            return

        if args.round is not None:
            self._print_round_detail(batch_id, args.round)
            return

        if args.round_diff is not None:
            self._print_round_diff(batch_id, args.round_diff)
            return

        self._print_rounds(batch_id, args.limit)

    def _print_rounds(self, batch_id: str, limit: int) -> None:
        rounds = self.store.list_rounds(batch_id, limit=limit)
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
            "auto_refresh": "自动刷新",
            "repair": "修复快照",
            "restore": "恢复快照",
        }

        print("=" * 98)
        print(f"📜 导入轮次时间线 (最近 {len(rounds)} 轮):")
        print("=" * 98)
        print(f"{'轮次':<6} {'触发原因':<12} {'事件数(前→后)':<16} {'导入数(前→后)':<16} {'版本':<10} {'时间'}")
        print("-" * 98)

        if not rounds:
            print("   (暂无轮次记录)")
        else:
            for r in rounds:
                trigger = trigger_labels.get(r.get("trigger", "unknown"), r.get("trigger", "unknown"))
                created_at = r.get("created_at", "").replace("T", " ")[:19]
                ev_before = r.get("before_event_count", 0)
                ev_after = r.get("after_event_count", 0)
                ev_change = ev_after - ev_before
                ev_change_str = f"+{ev_change}" if ev_change > 0 else str(ev_change)
                imp_before = r.get("before_import_count", 0)
                imp_after = r.get("after_import_count", 0)
                imp_change = imp_after - imp_before
                imp_change_str = f"+{imp_change}" if imp_change > 0 else str(imp_change)
                print(f"{r.get('round_number',0):<6} {trigger:<12} {ev_before}→{ev_after}({ev_change_str:<6}) {imp_before}→{imp_after}({imp_change_str:<6}) {r.get('rule_version',''):<10} {created_at}")
                summary = r.get("summary", [])
                if summary:
                    for s in summary[:2]:
                        print(f"       • {s}")
                    if len(summary) > 2:
                        print(f"       ... 还有 {len(summary) - 2} 项变更")

        print()
        print("💡 提示:")
        print("   使用 --round <轮次号> 查看轮次详情")
        print("   使用 --round-diff <轮次号> 查看轮次变更差异")
        print("   使用 --recover <快照ID> 恢复到指定快照状态")

    def _print_round_detail(self, batch_id: str, round_number: int) -> None:
        round_info = self.store.get_round(batch_id, round_number)
        if not round_info:
            print(f"❌ 找不到轮次: {round_number}")
            return

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
        trigger = trigger_labels.get(round_info.get("trigger", "unknown"), round_info.get("trigger", "unknown"))

        print("=" * 78)
        print(f"📋 轮次详情 - 第 {round_number} 轮")
        print("=" * 78)
        print(f"   触发原因: {trigger}")
        print(f"   操作时间: {round_info.get('created_at', '').replace('T', ' ')}")
        print(f"   前快照ID: {round_info.get('before_snapshot_id', 'N/A')}")
        print(f"   后快照ID: {round_info.get('after_snapshot_id', 'N/A')}")
        print()

        detail = round_info.get("detail", {})
        if detail:
            print("📝 操作详情:")
            for k, v in detail.items():
                if v:
                    print(f"   {k}: {v}")
            print()

        before = round_info.get("before_snapshot")
        after = round_info.get("after_snapshot")
        if before and after:
            print("📊 状态对比:")
            print(f"   {'字段':<25} {'操作前':<15} {'操作后':<15} {'变化'}")
            print("-" * 70)
            fields = [
                ("event_count", "事件总数"),
                ("imported_file_count", "导入文件数"),
                ("parse_error_count", "解析错误数"),
                ("label_action_count", "标注操作数"),
                ("export_count", "导出次数"),
                ("rule_version", "规则版本"),
            ]
            for field, label in fields:
                old_val = before.get(field, 0)
                new_val = after.get(field, 0)
                if field == "rule_version":
                    diff = f"{old_val} → {new_val}" if old_val != new_val else "无变化"
                else:
                    diff_int = new_val - old_val
                    diff = f"+{diff_int}" if diff_int > 0 else str(diff_int) if diff_int != 0 else "0"
                print(f"   {label:<25} {str(old_val):<15} {str(new_val):<15} {diff}")
            print()

        diff = round_info.get("diff")
        if diff:
            summary = diff.get("summary", [])
            if summary:
                print("📋 变更摘要:")
                for s in summary:
                    print(f"   • {s}")
                print()

        print("💡 提示: 使用 --round-diff <轮次号> 查看详细差异")

    def _print_round_diff(self, batch_id: str, round_number: int) -> None:
        diff_info = self.store.get_round_diff(batch_id, round_number)
        if not diff_info:
            print(f"❌ 找不到轮次: {round_number}")
            return

        print("=" * 78)
        print(f"📊 轮次变更差异 - 第 {round_number} 轮")
        print("=" * 78)
        print(f"   触发原因: {diff_info.get('trigger', 'unknown')}")
        print(f"   操作时间: {diff_info.get('created_at', '').replace('T', ' ')}")
        print(f"   快照对比: {diff_info.get('before_snapshot_id', 'N/A')}  →  {diff_info.get('after_snapshot_id', 'N/A')}")
        print()

        diff = diff_info.get("diff", {})

        summary = diff.get("summary", [])
        if summary:
            print("📋 变更摘要:")
            for s in summary:
                print(f"   • {s}")
            print()

        if diff.get("event_count_change") is not None:
            change = diff["event_count_change"]
            arrow = "+" if change > 0 else ""
            print(f"   事件数变化:    {arrow}{change}")
            if diff.get("added_events") > 0:
                print(f"     新增: {diff['added_events']}")
            if diff.get("removed_events") > 0:
                print(f"     移除: {diff['removed_events']}")
            print()

        import_changes = diff.get("import_changes", [])
        if import_changes:
            print("   导入变更:")
            for ic in import_changes:
                if ic.get("type") == "added":
                    print(f"     ✅ 新增: {ic.get('filename')} ({ic.get('source_type')}, {ic.get('event_count',0)}事件)")
                elif ic.get("type") == "removed":
                    print(f"     ❌ 移除: {ic.get('filename')} ({ic.get('source_type')})")
            print()

        config_changes = diff.get("config_changes", {})
        if config_changes:
            print("   配置变更:")
            for field, change in config_changes.items():
                old = change.get("old")
                new = change.get("new")
                print(f"     {field}: {old} → {new}")
            print()

        status_changes = diff.get("status_changes", {})
        if status_changes:
            print("   状态分布变化:")
            for status, change in sorted(status_changes.items()):
                arrow = "+" if change > 0 else ""
                print(f"     {status:<8} {arrow}{change}")
            print()

        label_changes = diff.get("label_changes", [])
        if label_changes:
            print("   标注变更:")
            for lc in label_changes:
                if lc.get("type") == "label":
                    print(f"     🏷️  {lc.get('operation')}: {lc.get('event_id_short')} → {lc.get('new_status')}")
                elif lc.get("type") == "undo":
                    print(f"     ↩️  {lc.get('undo_type_desc')}")
            print()

        export_changes = diff.get("export_changes", [])
        if export_changes:
            print("   导出变更:")
            for ec in export_changes:
                print(f"     📤 新增导出: {ec.get('filename')} ({ec.get('size',0)}字节)")
            print()

    def _recover_to_snapshot(self, batch_id: str, snapshot_id: str, skip_confirmation: bool = False) -> None:
        print(f"⚠️  即将恢复到快照: {snapshot_id}")
        print("   此操作将覆盖当前状态，并创建一个新的恢复轮次")
        print()

        if not skip_confirmation:
            import sys
            print("   确认恢复？输入 'yes' 继续，其他输入取消: ", end="")
            confirmation = sys.stdin.readline().strip()
            if confirmation.lower() != "yes":
                print("   ❌ 已取消恢复")
                return

        result = self.store.restore_to_snapshot(batch_id, snapshot_id)
        if result.get("success"):
            print(f"✅ {result.get('message')}")
            print(f"   恢复轮次: {result.get('round_number', 'N/A')}")
        else:
            print(f"❌ {result.get('message')}")
            if result.get("error"):
                print(f"   错误详情: {result.get('error')}")

    def _repair_snapshot(self, batch_id: str, snapshot_id: str) -> None:
        print(f"🔧 正在修复快照: {snapshot_id}")
        print()
        result = self.store.repair_snapshot_file(batch_id, snapshot_id)

        print(f"   修复状态: {'✅ 成功' if result.get('repaired') else '❌ 失败'}")
        print(f"   消息: {result.get('message', '')}")

        actions = result.get("actions", [])
        if actions:
            print()
            print("   执行的操作:")
            for a in actions:
                print(f"   • {a}")

    def _check_and_print_database_lag(self, batch_id: str) -> None:
        print("🔍 正在检查数据库状态是否落后...")
        print()
        result = self.store.check_database_state_lag(batch_id)

        if result.get("is_lagged"):
            print("❌ 发现数据库状态落后")
            details = result.get("details", [])
            for d in details:
                print(f"   • {d}")
            print(f"   快照事件数: {result.get('snapshot_event_count', 0)}")
            print(f"   实际事件数: {result.get('actual_event_count', 0)}")
            print()
            print("💡 建议: 使用 --fix 或 refresh_overview_snapshot 刷新快照")
        else:
            print("✅ 快照与数据库状态一致")
            print(f"   快照事件数: {result.get('snapshot_event_count', 0)}")
            print(f"   实际事件数: {result.get('actual_event_count', 0)}")
        print()

    def _check_and_print_config_conflict(self, batch_id: str) -> None:
        print("🔍 正在检查配置冲突...")
        print()
        current_config = self.store.load_config(batch_id)
        result = self.store.check_config_conflict(batch_id, current_config)

        if result.get("has_conflict"):
            print("❌ 发现配置冲突")
            conflicts = result.get("conflicts", [])
            print(f"{'字段':<20} {'当前值':<20} {'新值':<20}")
            print("-" * 60)
            for c in conflicts:
                print(f"   {c.get('label',''):<18} {str(c.get('current','')):<20} {str(c.get('new','')):<20}")
            print()
            print("💡 建议: 保存配置后刷新快照，或使用 --bump-version 升级规则版本")
        else:
            print("✅ 配置一致，无冲突")
        print()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="timeline-review",
        description="本地事件时间线复盘 CLI 工具 - 合并日志、告警和备注进行追溯分析",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例流程:
  # 1. 创建复盘批次
  timeline-review create --name "线上故障复盘" --desc "支付服务 5xx 问题"

  # 2. 导入三类数据文件
  timeline-review import app.log alerts.csv notes.json

  # 3. 查看时间线
  timeline-review timeline --limit 20

  # 4. 标注事件（状态和备注可以同时设置）
  timeline-review label --status root <event_id> --notes "第三方支付接口变更"
  timeline-review label --status noise <event_id1> <event_id2>

  # 5. 查看和撤销标注（与导入撤销是独立功能）
  timeline-review label-history
  timeline-review undo-label

  # 6. 导出报告
  timeline-review export --format markdown --output report.md
        """,
    )
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    p_create = subparsers.add_parser("create", help="创建新的复盘批次")
    p_create.add_argument("--name", "-n", required=True, help="批次名称")
    p_create.add_argument("--description", "--desc", "-d", help="批次描述")

    p_list = subparsers.add_parser("list", help="列出所有批次")

    p_switch = subparsers.add_parser("switch", help="切换到指定批次")
    p_switch.add_argument("batch_id", help="批次 ID")

    p_status = subparsers.add_parser("status", help="显示当前批次状态")

    p_overview = subparsers.add_parser("overview", help="批次概览（一目了然显示进度）")
    p_overview.add_argument("--diff", "--compare-with", dest="compare_with", const="previous", nargs="?", default=None, help="显示与历史快照的变更摘要，可指定 previous/first/快照ID")
    p_overview.add_argument("--history", action="store_true", help="列出历史快照")
    p_overview.add_argument("--history-limit", type=int, default=20, help="历史快照显示数量")
    p_overview.add_argument("--check-consistency", action="store_true", help="检查快照与真实数据的一致性")
    p_overview.add_argument("--fix", action="store_true", help="自动修复快照不一致问题")
    p_overview.add_argument("--change-log", action="store_true", help="显示变更日志")
    p_overview.add_argument("--log-limit", type=int, default=20, help="变更日志显示数量")
    p_overview.add_argument("--log-type", choices=["import_change", "config_change", "export_change", "label_change", "snapshot_repair", "other_change", "manual_refresh", "round_created", "round_saved"], help="按类型过滤变更日志")
    p_overview.add_argument("--export-diff", action="store_true", help="显示最近导出对比")
    p_overview.add_argument("--refresh", "-r", action="store_true", help="强制刷新快照")
    p_overview.add_argument("--rounds", action="store_true", help="列出导入轮次时间线")
    p_overview.add_argument("--round", type=int, help="查看指定轮次详情")
    p_overview.add_argument("--round-diff", type=int, help="查看指定轮次的变更差异")
    p_overview.add_argument("--round-limit", type=int, default=20, help="轮次显示数量")

    p_history = subparsers.add_parser("history", help="历史记录与轮次管理")
    p_history.add_argument("--rounds", action="store_true", help="列出所有导入轮次")
    p_history.add_argument("--round", type=int, help="查看指定轮次详情")
    p_history.add_argument("--round-diff", type=int, help="查看指定轮次的变更差异")
    p_history.add_argument("--limit", type=int, default=20, help="显示数量")
    p_history.add_argument("--recover", type=str, metavar="SNAPSHOT_ID", help="恢复到指定快照")
    p_history.add_argument("--repair", type=str, metavar="SNAPSHOT_ID", help="修复损坏的快照文件")
    p_history.add_argument("--check-lag", action="store_true", help="检查数据库状态是否落后")
    p_history.add_argument("--check-config", action="store_true", help="检查配置冲突")
    p_history.add_argument("--yes", "-y", action="store_true", help="自动确认恢复操作，无需交互式确认")

    p_import = subparsers.add_parser("import", help="导入日志/告警/备注文件")
    p_import.add_argument("files", nargs="+", help="要导入的文件路径")
    p_import.add_argument("--type", "-t", choices=["log", "csv", "json"], help="强制指定文件类型（不按扩展名判断）")
    p_import.add_argument("--force", "-f", action="store_true", help="强制重新导入已导入过的文件")

    p_undo = subparsers.add_parser("undo-import", help="撤销文件导入（默认最近一次）")
    p_undo.add_argument("--round", type=int, help="按轮次号撤销指定导入")
    p_undo.add_argument("--import-id", type=str, help="按导入ID撤销指定导入")
    p_undo.add_argument("--index", type=int, help="按 import-history 列表中的显示序号撤销")

    p_restore = subparsers.add_parser("restore-import", help="恢复被撤销的导入（默认最近撤销）")
    p_restore.add_argument("--round", type=int, help="按轮次号恢复指定导入")
    p_restore.add_argument("--import-id", type=str, help="按导入ID恢复指定导入")
    p_restore.add_argument("--index", type=int, help="按 import-history 列表中的显示序号恢复")

    p_import_detail = subparsers.add_parser("import-detail", help="查看某轮导入的详情、变化摘要和冲突原因")
    p_import_detail.add_argument("--round", type=int, help="按轮次号查看")
    p_import_detail.add_argument("--import-id", type=str, help="按导入ID查看")
    p_import_detail.add_argument("--index", type=int, help="按 import-history 列表中的显示序号查看")

    p_import_history = subparsers.add_parser("import-history", help="查看导入历史列表（含已撤销记录），可用序号操作")
    p_import_history.add_argument("--detail", type=int, metavar="INDEX", help="查看指定序号的导入详情")
    p_import_history.add_argument("--undo", type=int, metavar="INDEX", help="撤销指定序号的导入")
    p_import_history.add_argument("--restore", type=int, metavar="INDEX", help="恢复指定序号的已撤销导入")
    p_import_history.add_argument("--all", action="store_true", help="显示全部记录（含已撤销）")

    p_timeline = subparsers.add_parser("timeline", help="查看排序后的时间线")
    p_timeline.add_argument("--limit", "-n", type=int, help="显示事件数量限制")
    p_timeline.add_argument("--offset", "-o", type=int, help="跳过事件数量")
    p_timeline.add_argument("--reverse", "-r", action="store_true", help="倒序排列（最新在前）")
    p_timeline.add_argument("--status", action="append", choices=["unconfirmed", "confirmed", "root", "noise", "root_cause"], help="按状态过滤")
    p_timeline.add_argument("--severity", action="append", help="按严重级别过滤（可多次指定）")
    p_timeline.add_argument("--source", action="append", choices=["log", "alert", "note"], help="按来源过滤（可多次指定）")
    p_timeline.add_argument("--search", "-s", help="按关键词搜索消息和备注")
    p_timeline.add_argument("--gaps", "-g", action="store_true", help="同时显示时间缺口")

    p_label = subparsers.add_parser("label", help="标注事件状态（根因/噪声/确认/待确认）")
    p_label.add_argument("event_ids", nargs="+", help="事件 ID（支持前缀匹配）")
    p_label.add_argument("--status", "-s", required=True, help="状态: root(根因)/noise(噪声)/confirmed(已确认)/unconfirmed(待确认)")
    p_label.add_argument("--notes", "-n", help="同时添加备注")

    p_note = subparsers.add_parser("note", help="为事件添加/更新备注")
    p_note.add_argument("event_id", help="事件 ID")
    p_note.add_argument("notes", help="备注内容")

    p_config = subparsers.add_parser("config", help="管理规则配置")
    p_config.add_argument("--show", action="store_true", help="显示当前配置")
    p_config.add_argument("--dedup-window", type=int, help="去重时间窗口（秒）")
    p_config.add_argument("--gap-threshold", type=int, help="时间缺口阈值（秒）")
    p_config.add_argument("--add-severity", help="添加严重级别映射，格式: raw=SEVERITY")
    p_config.add_argument("--add-time-format", help="添加时间戳格式（strftime）")
    p_config.add_argument("--bump-version", action="store_true", help="规则版本号 +1")
    p_config.add_argument("--check-conflict", action="store_true", help="变更前检查配置冲突风险并给出建议")

    p_phase = subparsers.add_parser("phase", help="管理事件阶段")
    p_phase.add_argument("--list", "-l", action="store_true", help="列出所有阶段")
    p_phase.add_argument("--add", "-a", help="添加阶段名称")
    p_phase.add_argument("--start", help="阶段开始时间")
    p_phase.add_argument("--end", help="阶段结束时间")
    p_phase.add_argument("--description", "--desc", "-d", help="阶段描述")
    p_phase.add_argument("--clear", action="store_true", help="清空所有阶段")

    p_export = subparsers.add_parser("export", help="导出复盘报告")
    p_export.add_argument("--format", "-f", choices=["markdown", "csv"], default="markdown", help="导出格式")
    p_export.add_argument("--output", "-o", help="输出文件路径")
    p_export.add_argument("--save-internal", action="store_true", help="同时保存到批次内部存储")
    p_export.add_argument("--verify", action="store_true", help="导出后核对内容与实际数据一致性")
    p_export.add_argument("--auto-fix", action="store_true", help="发现快照不一致时自动修复")

    p_show = subparsers.add_parser("show", help="显示事件详细信息")
    p_show.add_argument("event_ids", nargs="+", help="事件 ID（支持前缀匹配）")

    p_errors = subparsers.add_parser("errors", help="显示解析错误详情")
    p_errors.add_argument("--verbose", "-v", action="store_true", help="显示原始内容")

    p_label_history = subparsers.add_parser("label-history", help="查看标注历史记录")
    p_label_history.add_argument("--limit", "-n", type=int, help="显示最近的N条记录")

    p_undo_label = subparsers.add_parser("undo-label", help="撤销最后一次标注操作（状态/备注，与导入撤销独立）")

    p_audit = subparsers.add_parser("audit-center", help="导入导出核对中心 - 完整链路管理")
    p_audit.add_argument("--list", "-l", action="store_true", help="显示最近操作列表")
    p_audit.add_argument("--detail", type=int, metavar="INDEX", help="按显示序号查看操作详情")
    p_audit.add_argument("--undo", type=int, metavar="INDEX", help="按显示序号撤销导入")
    p_audit.add_argument("--restore", type=int, metavar="INDEX", help="按显示序号恢复已撤销的导入")
    p_audit.add_argument("--verify-export", type=str, metavar="FILE", help="核对指定导出文件与库内数据的一致性")
    p_audit.add_argument("--export-audit", action="store_true", help="导出报告后自动核对")
    p_audit.add_argument("--format", choices=["markdown", "csv"], default="markdown", help="导出格式")
    p_audit.add_argument("--output", "-o", help="导出文件路径")
    p_audit.add_argument("--limit", type=int, default=20, help="操作列表显示数量")
    p_audit.add_argument("--show-rules", action="store_true", help="显示当前核对规则配置")
    p_audit.add_argument("--enable-check", type=str, choices=["empty_export", "event_count_mismatch", "duplicate_restore", "import_conflict", "all"], help="启用指定核对规则")
    p_audit.add_argument("--disable-check", type=str, choices=["empty_export", "event_count_mismatch", "duplicate_restore", "import_conflict", "all"], help="禁用指定核对规则")
    p_audit.add_argument("--set-tolerance", type=str, metavar="CHECK=TOLERANCE", help="设置指定检查的容忍度，如 event_count_mismatch=5")
    p_audit.add_argument("--force", "-f", action="store_true", help="忽略冲突强制执行操作")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    app = CLIApp()
    cmd = args.command.replace("-", "_")
    handler = getattr(app, f"cmd_{cmd}", None)
    if handler:
        try:
            handler(args)
        except KeyboardInterrupt:
            print("\n⏹️  已取消")
            sys.exit(130)
        except Exception as e:
            print(f"❌ 执行失败: {e}", file=sys.stderr)
            import traceback
            if "--debug" in sys.argv:
                traceback.print_exc()
            sys.exit(1)
    else:
        print(f"❌ 未知命令: {args.command}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
