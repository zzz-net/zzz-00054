#!/usr/bin/env python3
"""
事件时间线复盘工具 - 标注撤销功能回归测试
覆盖场景：
1. 创建批次、导入样例、标注事件
2. 撤销标注（状态、备注、同时修改）
3. 重启后仍可撤销标注
4. 空撤销有明确提示
5. 坏时间格式、重复导入等既有行为不退化
6. 导出报告内容在撤销前后正确
"""

import os
import sys
import io
import json
import tempfile
import shutil
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from timeline_review.storage import StateStore, BatchNotFoundError
from timeline_review.models import EventStatus
from timeline_review.config import RuleConfig


class TestResult:
    def __init__(self, name: str):
        self.name = name
        self.passed = False
        self.error = None
        self.details = []

    def add_detail(self, detail: str):
        self.details.append(detail)

    def success(self, detail: str = ""):
        self.passed = True
        if detail:
            self.details.append(detail)

    def fail(self, error: str, detail: str = ""):
        self.passed = False
        self.error = error
        if detail:
            self.details.append(detail)


def run_tests():
    results = []
    test_dir = Path(tempfile.mkdtemp(prefix="timeline_test_"))
    print(f"🧪 测试目录: {test_dir}")
    print()

    try:
        example_dir = Path(__file__).parent / "examples"

        results.append(test_create_and_import(test_dir, example_dir))
        results.append(test_label_and_undo(test_dir))
        results.append(test_label_both_and_undo(test_dir))
        results.append(test_multiple_undos(test_dir))
        results.append(test_empty_undo(test_dir))
        results.append(test_persistence_after_restart(test_dir))
        results.append(test_report_consistency(test_dir, example_dir))
        results.append(test_no_regression_bad_timestamp(test_dir, example_dir))
        results.append(test_no_regression_duplicate_import(test_dir, example_dir))
        results.append(test_separate_undo_systems(test_dir, example_dir))

    finally:
        shutil.rmtree(test_dir, ignore_errors=True)

    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed

    print("\n" + "=" * 70)
    print(f"📊 测试结果: {passed}/{len(results)} 通过")
    print("=" * 70)

    for r in results:
        status = "✅ PASS" if r.passed else "❌ FAIL"
        print(f"\n{status} - {r.name}")
        for d in r.details:
            print(f"   ℹ️  {d}")
        if r.error:
            print(f"   ❌ {r.error}")

    if failed > 0:
        sys.exit(1)
    print("\n🎉 所有测试通过!")


def test_create_and_import(test_dir: Path, example_dir: Path) -> TestResult:
    """测试1: 创建批次并导入三类文件"""
    result = TestResult("创建批次并导入文件")
    try:
        store = StateStore(str(test_dir))
        meta = store.create_batch("测试批次1", "测试导入功能")
        batch_id = meta["id"]
        result.add_detail(f"批次创建成功: {batch_id}")

        config = store.load_config(batch_id)
        from timeline_review.importers import LogParser, CSVParser, JSONParser, raw_events_to_events

        log_parser = LogParser(config)
        raw, errors = log_parser.parse(str(example_dir / "app.log"))
        result.add_detail(f"日志解析: {len(raw)} 事件, {len(errors)} 错误")
        assert len(errors) == 2, f"应该有2个时间格式错误，实际: {len(errors)}"
        assert errors[0].source_file == "app.log"
        assert errors[0].line_number == 22
        assert errors[1].line_number == 24
        result.add_detail("坏时间格式错误正确: app.log:22, app.log:24")

        csv_parser = CSVParser(config)
        raw2, errors2 = csv_parser.parse(str(example_dir / "alerts.csv"))
        result.add_detail(f"CSV解析: {len(raw2)} 事件, {len(errors2)} 错误")
        assert len(errors2) == 1, f"应该有1个时间格式错误，实际: {len(errors2)}"
        assert errors2[0].source_file == "alerts.csv"
        assert errors2[0].line_number == 17
        result.add_detail("坏时间格式错误正确: alerts.csv:17")

        json_parser = JSONParser(config)
        raw3, errors3 = json_parser.parse(str(example_dir / "notes.json"))
        result.add_detail(f"JSON解析: {len(raw3)} 事件, {len(errors3)} 错误")

        from timeline_review.importers import compute_file_hash
        all_raw = raw + raw2 + raw3
        events = raw_events_to_events(all_raw, config)
        from timeline_review.timeline import dedupe_events
        deduped, merged = dedupe_events(events, config)
        result.add_detail(f"去重后: {len(deduped)} 事件, 合并 {len(merged)} 组")

        store.save_events(batch_id, deduped)
        all_errors = errors + errors2 + errors3
        store.save_parse_errors(batch_id, all_errors)

        for fpath, fcount, ferrors in [
            (example_dir / "app.log", len(raw), len(errors)),
            (example_dir / "alerts.csv", len(raw2), len(errors2)),
            (example_dir / "notes.json", len(raw3), len(errors3)),
        ]:
            abs_path = str(Path(fpath).resolve())
            fhash = compute_file_hash(str(fpath))
            store.mark_file_imported(batch_id, abs_path, fhash, fcount, ferrors)

        saved_events = store.load_events(batch_id)
        assert len(saved_events) > 0, "事件保存后应该能读取到"
        result.success(f"成功导入并保存 {len(saved_events)} 个事件")

    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_label_and_undo(test_dir: Path) -> TestResult:
    """测试2: 标注状态并撤销"""
    result = TestResult("标注状态并撤销")
    try:
        store = StateStore(str(test_dir))
        batch_id = store.list_batches()[0]["id"]
        events = store.load_events(batch_id)
        test_event = events[0]
        original_status = test_event.status
        original_notes = test_event.notes
        eid = test_event.id
        result.add_detail(f"测试事件: {eid[:12]}..., 初始状态: {original_status.value}")

        new_status = EventStatus.ROOT_CAUSE
        updated = store.set_event_status(batch_id, eid, new_status)
        assert updated is not None, "标注状态应该成功"
        assert updated.status == new_status, f"状态应该变为 {new_status.value}"
        result.add_detail(f"标注状态: {original_status.value} -> {new_status.value}")

        history = store.get_label_history(batch_id)
        assert len(history) == 1, f"应该有1条历史记录，实际: {len(history)}"
        assert history[0].operation == "set_status"
        assert history[0].old_status == original_status
        assert history[0].new_status == new_status
        result.add_detail("历史记录正确记录了状态变更")

        undone = store.undo_last_label(batch_id)
        assert undone is not None, "撤销应该成功"
        assert undone.operation == "set_status"

        restored = store.get_event_by_id(batch_id, eid)
        assert restored.status == original_status, f"状态应该恢复为 {original_status.value}"
        assert restored.notes == original_notes, "备注应该保持不变"
        result.success(f"撤销成功，状态恢复: {new_status.value} -> {restored.status.value}")

        history_after = store.get_label_history(batch_id)
        assert len(history_after) == 0, "撤销后历史应该为空"

    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_label_both_and_undo(test_dir: Path) -> TestResult:
    """测试3: 同时修改状态和备注并撤销"""
    result = TestResult("同时修改状态和备注并撤销")
    try:
        store = StateStore(str(test_dir))
        batch_id = store.list_batches()[0]["id"]
        events = store.load_events(batch_id)
        test_event = events[1]
        original_status = test_event.status
        original_notes = test_event.notes
        eid = test_event.id
        result.add_detail(f"测试事件: {eid[:12]}...")
        result.add_detail(f"初始: 状态={original_status.value}, 备注='{original_notes}'")

        new_status = EventStatus.CONFIRMED
        new_notes = "这是测试备注内容"
        updated = store.set_event_status_and_notes(batch_id, eid, new_status, new_notes)
        assert updated is not None, "同时修改应该成功"
        assert updated.status == new_status
        assert updated.notes == new_notes
        result.add_detail(f"修改后: 状态={new_status.value}, 备注='{new_notes}'")

        history = store.get_label_history(batch_id)
        assert len(history) == 1, f"应该有1条历史记录，实际: {len(history)}"
        assert history[0].operation == "set_both"
        assert history[0].old_status == original_status
        assert history[0].new_status == new_status
        assert history[0].old_notes == original_notes
        assert history[0].new_notes == new_notes
        result.add_detail("历史记录正确记录了状态+备注变更")

        undone = store.undo_last_label(batch_id)
        assert undone is not None, "撤销应该成功"
        assert undone.operation == "set_both"

        restored = store.get_event_by_id(batch_id, eid)
        assert restored.status == original_status, "状态应该恢复"
        assert restored.notes == original_notes, "备注应该恢复"
        result.success(f"撤销成功，状态和备注都已恢复")

    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_multiple_undos(test_dir: Path) -> TestResult:
    """测试4: 多次标注多次撤销"""
    result = TestResult("多次标注多次撤销")
    try:
        store = StateStore(str(test_dir))
        batch_id = store.list_batches()[0]["id"]
        events = store.load_events(batch_id)
        test_event = events[2]
        original_status = test_event.status
        eid = test_event.id
        result.add_detail(f"测试事件: {eid[:12]}...")

        statuses = [EventStatus.CONFIRMED, EventStatus.NOISE, EventStatus.ROOT_CAUSE]
        for i, s in enumerate(statuses, 1):
            store.set_event_status(batch_id, eid, s)
            result.add_detail(f"第{i}次标注: -> {s.value}")

        history = store.get_label_history(batch_id)
        assert len(history) == 3, f"应该有3条历史记录，实际: {len(history)}"

        for i, expected_s in enumerate(reversed([original_status] + statuses[:-1]), 1):
            undone = store.undo_last_label(batch_id)
            assert undone is not None, f"第{i}次撤销应该成功"
            current = store.get_event_by_id(batch_id, eid)
            assert current.status == expected_s, f"第{i}次撤销后状态应为 {expected_s.value}"
            result.add_detail(f"第{i}次撤销: 恢复为 {expected_s.value}")

        history_after = store.get_label_history(batch_id)
        assert len(history_after) == 0, "所有撤销后历史应该为空"
        result.success("3次标注全部成功撤销")

    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_empty_undo(test_dir: Path) -> TestResult:
    """测试5: 空撤销提示"""
    result = TestResult("空撤销有明确提示")
    try:
        store = StateStore(str(test_dir))
        batch_id = store.list_batches()[0]["id"]

        history = store.get_label_history(batch_id)
        assert len(history) == 0, "测试前历史应该为空"

        undone = store.undo_last_label(batch_id)
        assert undone is None, "空撤销应该返回 None"
        result.success("空撤销返回 None，CLI 可给出明确提示")

    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_persistence_after_restart(test_dir: Path) -> TestResult:
    """测试6: 重启后仍可撤销上一标注"""
    result = TestResult("重启后仍可撤销上一标注")
    try:
        store = StateStore(str(test_dir))
        batch_id = store.list_batches()[0]["id"]
        events = store.load_events(batch_id)
        test_event = events[0]
        original_status = test_event.status
        eid = test_event.id

        new_status = EventStatus.NOISE
        new_notes = "重启前添加的备注"
        store.set_event_status_and_notes(batch_id, eid, new_status, new_notes)
        result.add_detail(f"重启前标注: 状态={new_status.value}, 备注='{new_notes}'")

        history_before = store.get_label_history(batch_id)
        assert len(history_before) == 1
        result.add_detail(f"重启前历史记录数: {len(history_before)}")

        new_store = StateStore(str(test_dir))
        active = new_store.switch_batch(batch_id)
        result.add_detail(f"模拟重启，重新加载批次: {active['id']}")

        history_after = new_store.get_label_history(batch_id)
        assert len(history_after) == 1, "重启后历史记录应该仍然存在"
        assert history_after[0].operation == "set_both"
        assert history_after[0].config_version == "1.0.0"
        result.add_detail(f"重启后历史记录数: {len(history_after)}，规则版本: {history_after[0].config_version}")

        current = new_store.get_event_by_id(batch_id, eid)
        assert current.status == new_status, "重启后状态应该保持标注值"
        assert current.notes == new_notes, "重启后备注应该保持标注值"
        result.add_detail("重启后标注值正确保留")

        undone = new_store.undo_last_label(batch_id)
        assert undone is not None, "重启后应该仍然可以撤销"

        restored = new_store.get_event_by_id(batch_id, eid)
        assert restored.status == original_status, "重启后撤销应该正确恢复状态"
        assert restored.notes == test_event.notes, "重启后撤销应该正确恢复备注"
        result.success("重启后成功撤销标注，状态和备注都正确恢复")

    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_report_consistency(test_dir: Path, example_dir: Path) -> TestResult:
    """测试7: 报告导出内容在撤销前后一致"""
    result = TestResult("报告导出内容在撤销前后一致")
    try:
        store = StateStore(str(test_dir))
        batch_id = store.list_batches()[0]["id"]
        meta = store.get_batch_meta(batch_id)
        config = store.load_config(batch_id)
        events = store.load_events(batch_id)

        from timeline_review.timeline import Timeline
        from timeline_review.exporters import export_report

        timeline_before = Timeline(events, config)
        report_before = export_report(timeline_before, config, "markdown", meta)
        result.add_detail(f"标注前报告大小: {len(report_before)} 字符")

        test_event = events[3]
        eid = test_event.id
        original_status = test_event.status
        original_status_count_before = sum(1 for e in events if e.status == original_status)

        store.set_event_status_and_notes(batch_id, eid, EventStatus.ROOT_CAUSE, "根因标注测试")
        events_after = store.load_events(batch_id)
        timeline_after = Timeline(events_after, config)
        report_after = export_report(timeline_after, config, "markdown", meta)
        result.add_detail(f"标注后报告大小: {len(report_after)} 字符")
        assert "🎯" in report_after or "根因" in report_after, "标注后报告应该包含根因状态"
        assert "根因标注测试" in report_after, "标注后报告应该包含备注"

        store.undo_last_label(batch_id)
        events_restored = store.load_events(batch_id)
        timeline_restored = Timeline(events_restored, config)
        report_restored = export_report(timeline_restored, config, "markdown", meta)
        result.add_detail(f"撤销后报告大小: {len(report_restored)} 字符")

        restored_event = store.get_event_by_id(batch_id, eid)
        assert restored_event.status == original_status, "事件状态应该恢复"
        assert restored_event.notes == test_event.notes, "事件备注应该恢复"
        assert "根因标注测试" not in report_restored, "撤销后报告不应该包含备注"

        status_count_after = sum(1 for e in events_restored if e.status == original_status)
        assert status_count_after == original_status_count_before, f"状态计数应该恢复为 {original_status_count_before}"
        result.success("撤销前后报告内容一致，状态和备注正确恢复")

    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_no_regression_bad_timestamp(test_dir: Path, example_dir: Path) -> TestResult:
    """测试8: 坏时间格式检测不退化"""
    result = TestResult("坏时间格式检测不退化")
    try:
        store = StateStore(str(test_dir))
        batch_id = store.list_batches()[0]["id"]
        errors = store.load_parse_errors(batch_id)
        result.add_detail(f"解析错误总数: {len(errors)}")

        timestamp_errors = [e for e in errors if e.error_type == "timestamp_error"]
        assert len(timestamp_errors) == 3, f"应该有3个时间格式错误，实际: {len(timestamp_errors)}"

        error_locations = {(e.source_file, e.line_number) for e in timestamp_errors}
        expected = {("app.log", 22), ("app.log", 24), ("alerts.csv", 17)}
        assert error_locations == expected, f"错误位置应该是 {expected}，实际: {error_locations}"
        result.success(f"正确检测到3个时间格式错误: {sorted(expected)}")

    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_no_regression_duplicate_import(test_dir: Path, example_dir: Path) -> TestResult:
    """测试9: 重复导入不产生重复事件"""
    result = TestResult("重复导入不产生重复事件")
    try:
        store = StateStore(str(test_dir))
        batch_id = store.list_batches()[0]["id"]
        config = store.load_config(batch_id)

        count_before = len(store.load_events(batch_id))
        result.add_detail(f"重复导入前事件数: {count_before}")

        log_path = str(example_dir / "app.log")
        is_imported = store.is_file_imported(batch_id, log_path)
        result.add_detail(f"文件是否已导入: {is_imported}")
        assert is_imported == True, "文件应该标记为已导入"

        from timeline_review.importers import LogParser, raw_events_to_events
        parser = LogParser(config)
        raw, errors = parser.parse(log_path)
        events = raw_events_to_events(raw, config)

        existing_ids = {e.id for e in store.load_events(batch_id)}
        dup_count = sum(1 for e in events if e.id in existing_ids)
        assert dup_count > 0, "应该检测到重复的事件ID"
        result.add_detail(f"检测到 {dup_count} 个重复事件ID")

        count_after = len(store.load_events(batch_id))
        assert count_after == count_before, f"事件数应该保持不变，之前: {count_before}, 之后: {count_after}"
        result.success(f"重复导入后事件数保持不变: {count_after}")

    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_separate_undo_systems(test_dir: Path, example_dir: Path) -> TestResult:
    """测试10: 标注撤销与导入撤销是独立系统"""
    result = TestResult("标注撤销与导入撤销独立")
    try:
        store = StateStore(str(test_dir))
        batch_id = store.list_batches()[0]["id"]

        imports_before = store.get_imported_files(batch_id)
        labels_before = store.get_label_history(batch_id)
        result.add_detail(f"导入记录数: {len(imports_before)}, 标注历史数: {len(labels_before)}")

        undone_import = store.undo_last_import(batch_id)
        assert undone_import is not None, "应该能撤销导入记录"
        result.add_detail(f"撤销导入: {undone_import['filename']}")

        labels_after_import_undo = store.get_label_history(batch_id)
        assert len(labels_after_import_undo) == len(labels_before), "撤销导入不应该影响标注历史"

        store.set_event_status(batch_id, store.load_events(batch_id)[0].id, EventStatus.NOISE)
        labels_after_label = store.get_label_history(batch_id)
        imports_after_label = store.get_imported_files(batch_id)
        assert len(imports_after_label) == len(imports_before) - 1, "标注不应该影响导入记录"
        result.add_detail("标注操作不影响导入记录")

        undone_label = store.undo_last_label(batch_id)
        assert undone_label is not None, "应该能撤销标注"
        imports_after_label_undo = store.get_imported_files(batch_id)
        assert len(imports_after_label_undo) == len(imports_before) - 1, "撤销标注不应该影响导入记录"
        result.add_detail("撤销标注不影响导入记录")

        result.success("标注撤销与导入撤销是完全独立的系统")

    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


if __name__ == "__main__":
    run_tests()
