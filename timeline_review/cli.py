import argparse
import sys
import os
import io
from pathlib import Path
from datetime import datetime
from typing import List, Optional

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

        try:
            snapshot = self.store.load_overview_snapshot(batch_id)
        except Exception as e:
            print(f"⚠️  概览数据加载失败，正在重建: {e}")
            snapshot = self.store.refresh_overview_snapshot(batch_id)

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
        print("🏷️  最近标注动作:")
        last_label = snapshot.get("last_label_action")
        label_count = snapshot.get("label_action_count", 0)
        if not last_label:
            if label_count == 0:
                print("   (暂无标注记录)")
            else:
                print(f"   (历史标注 {label_count} 次，最近记录已被撤销)")
        else:
            op = last_label.get("operation", "未知操作")
            eid_short = last_label.get("event_id_short", "???")
            acted_at = last_label.get("acted_at", "")
            print(f"   操作:   {op}")
            print(f"   事件:   {eid_short}")
            if last_label.get("old_status") or last_label.get("new_status"):
                old_s = last_label.get("old_status") or "无"
                new_s = last_label.get("new_status") or "无"
                print(f"   状态:   {old_s}  →  {new_s}")
            if last_label.get("old_notes_preview") is not None or last_label.get("new_notes_preview") is not None:
                old_n = last_label.get("old_notes_preview") or "(空)"
                new_n = last_label.get("new_notes_preview") or "(空)"
                print(f"   备注:   {old_n}  →  {new_n}")
            if last_label.get("config_version"):
                print(f"   规则版本: {last_label['config_version']}")
            if acted_at:
                print(f"   操作时间: {acted_at}")
            if label_count > 1:
                print(f"   (共 {label_count} 次标注操作记录)")

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

    def cmd_import(self, args) -> None:
        batch_id = self._require_batch()
        config = self.store.load_config(batch_id)
        existing_events = self.store.load_events(batch_id)
        existing_ids = {e.id for e in existing_events}

        total_events_imported = 0
        total_errors = 0
        files_processed = []
        all_errors = list(self.store.load_parse_errors(batch_id))

        for file_path in args.files:
            path = Path(file_path)
            if not path.exists():
                print(f"❌ 文件不存在: {file_path}", file=sys.stderr)
                continue

            abs_path = str(path.resolve())
            if not args.force and self.store.is_file_imported(batch_id, abs_path):
                print(f"⏭️  文件已导入过，跳过: {path.name} (使用 --force 强制重新导入)")
                continue

            if args.type:
                parser = get_parser_by_type(args.type, config)
            else:
                parser = get_parser_by_extension(str(path), config)

            print(f"📥 正在解析: {path.name} ...")
            raw_events, parse_errors = parser.parse(str(path))
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

            if unique_new:
                existing_events.extend(unique_new)
                for e in unique_new:
                    existing_ids.add(e.id)
                total_events_imported += len(unique_new)
                print(f"   ✅ 导入 {len(unique_new)} 条新事件")
            else:
                print(f"   ℹ️  没有新事件需要导入")

            file_hash = compute_file_hash(str(path))
            self.store.mark_file_imported(batch_id, abs_path, file_hash, len(unique_new), len(parse_errors))
            files_processed.append(path.name)

        if existing_events:
            print(f"🔍 正在去重归并...")
            before_count = len(existing_events)
            deduped, merged_pairs = dedupe_events(existing_events, config)
            after_count = len(deduped)
            merged = before_count - after_count
            if merged > 0:
                print(f"   ✅ 合并了 {merged} 组重复事件")
            existing_events = deduped

        self.store.save_events(batch_id, existing_events)
        self.store.save_parse_errors(batch_id, all_errors)
        self.store.save_config(batch_id, config)

        print()
        print(f"📦 导入完成:")
        print(f"   处理文件: {len(files_processed)} 个")
        print(f"   新增事件: {total_events_imported} 条")
        print(f"   解析错误: {total_errors} 个")
        print(f"   当前批次事件总数: {len(existing_events)} 条")

    def cmd_undo_import(self, args) -> None:
        batch_id = self._require_batch()
        last = self.store.undo_last_import(batch_id)
        if not last:
            print("ℹ️  没有可撤销的导入记录")
            return
        print(f"↩️  已撤销导入记录: {last['filename']}")
        print(f"   (注意: 已导入的事件需要手动清理，或重新创建批次)")

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

        fmt = args.format or "markdown"
        content = export_report(timeline, config, fmt, meta)

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

    p_import = subparsers.add_parser("import", help="导入日志/告警/备注文件")
    p_import.add_argument("files", nargs="+", help="要导入的文件路径")
    p_import.add_argument("--type", "-t", choices=["log", "csv", "json"], help="强制指定文件类型（不按扩展名判断）")
    p_import.add_argument("--force", "-f", action="store_true", help="强制重新导入已导入过的文件")

    p_undo = subparsers.add_parser("undo-import", help="撤销最近一次文件导入")

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

    p_show = subparsers.add_parser("show", help="显示事件详细信息")
    p_show.add_argument("event_ids", nargs="+", help="事件 ID（支持前缀匹配）")

    p_errors = subparsers.add_parser("errors", help="显示解析错误详情")
    p_errors.add_argument("--verbose", "-v", action="store_true", help="显示原始内容")

    p_label_history = subparsers.add_parser("label-history", help="查看标注历史记录")
    p_label_history.add_argument("--limit", "-n", type=int, help="显示最近的N条记录")

    p_undo_label = subparsers.add_parser("undo-label", help="撤销最后一次标注操作（状态/备注，与导入撤销独立）")

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
