#!/usr/bin/env python3
"""
撤销场景一致性回归测试
覆盖：标注撤销、导入撤销、重启后再查、重复撤销、缺少历史记录提示
"""

import os
import sys
import io
import json
import tempfile
import shutil
import subprocess
from pathlib import Path
from datetime import datetime
from typing import List, Tuple

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent))

from timeline_review.storage import StateStore
from timeline_review.models import EventStatus, EventSource


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


def run_cli(args: List[str], work_dir: str = None) -> Tuple[int, str, str]:
    env = os.environ.copy()
    repo_root = str(Path(__file__).parent.resolve())
    if "PYTHONPATH" in env:
        env["PYTHONPATH"] = repo_root + os.pathsep + env["PYTHONPATH"]
    else:
        env["PYTHONPATH"] = repo_root
    cli_args = [sys.executable, "-m", "timeline_review"] + args
    result = subprocess.run(
        cli_args,
        cwd=work_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    return result.returncode, result.stdout, result.stderr


def extract_event_ids(timeline_output: str) -> List[str]:
    ids = []
    for line in timeline_output.splitlines():
        line = line.strip()
        if "ID:" in line:
            parts = line.split("ID:", 1)
            if len(parts) > 1:
                id_part = parts[1].strip().split()[0] if parts[1].strip() else ""
                if id_part and len(id_part) >= 8:
                    ids.append(id_part)
    return ids


def run_tests():
    results = []
    test_dir = Path(tempfile.mkdtemp(prefix="tlr_undo_test_"))
    example_dir = Path(__file__).parent / "examples"
    print(f"🧪 测试工作目录: {test_dir}")
    print()

    try:
        results.append(test_undo_label_overview_shows_undo(test_dir, example_dir))
        results.append(test_undo_label_then_new_label_latest(test_dir, example_dir))
        results.append(test_undo_import_events_and_stats_removed(test_dir, example_dir))
        results.append(test_undo_import_parse_errors_removed(test_dir, example_dir))
        results.append(test_undo_persistence_after_restart(test_dir, example_dir))
        results.append(test_repeated_undo_label_all_consumed(test_dir, example_dir))
        results.append(test_repeated_undo_import_all_consumed(test_dir, example_dir))
        results.append(test_undo_no_history_cli_prompt(test_dir, example_dir))
        results.append(test_full_chain_undo_consistency(test_dir, example_dir))
        results.append(test_undo_import_source_stats_match_real_data(test_dir, example_dir))
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)

    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed

    print("\n" + "=" * 70)
    print(f"📊 撤销场景测试结果: {passed}/{len(results)} 通过")
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
    print("\n🎉 所有撤销场景测试通过!")


def test_undo_label_overview_shows_undo(test_dir: Path, example_dir: Path) -> TestResult:
    """测试1: 撤销标注后，概览显示撤销动作而非直接清空"""
    result = TestResult("撤销标注后概览显示撤销动作")
    try:
        work_dir = test_dir / "undo_label_show"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        shutil.copy(example_dir / "app.log", work_dir / "examples" / "app.log")

        run_cli(["create", "--name", "撤销标注显示测试"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))

        code, out, err = run_cli(["timeline", "--limit", "1"], work_dir=str(work_dir))
        event_ids = extract_event_ids(out)
        eid = event_ids[0]

        run_cli(["label", "--status", "root", eid, "--notes", "测试根因"], work_dir=str(work_dir))
        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()
        snap1 = store.load_overview_snapshot(batch_id, auto_refresh=False)
        assert snap1.get("latest_action_kind") == "label"
        assert snap1.get("label_action_count") == 1
        assert snap1.get("undo_action_count") == 0
        result.add_detail("标注后: latest_action_kind=label")

        code, out, err = run_cli(["undo-label"], work_dir=str(work_dir))
        assert code == 0, f"undo-label 失败: {err}"

        snap2 = store.load_overview_snapshot(batch_id, auto_refresh=False)
        assert snap2.get("latest_action_kind") == "undo", f"撤销后应为 undo: {snap2.get('latest_action_kind')}"
        assert snap2.get("last_label_action") is None, f"label_action 应清空: {snap2.get('last_label_action')}"
        assert snap2.get("label_action_count") == 0
        assert snap2.get("undo_action_count") == 1

        last_undo = snap2.get("last_undo_action")
        assert last_undo is not None
        assert last_undo.get("undo_type") == "undo_label"
        assert last_undo.get("event_id_short")
        assert last_undo.get("new_status") == EventStatus.ROOT_CAUSE.value
        assert last_undo.get("restored_status") == EventStatus.UNCONFIRMED.value
        result.add_detail(f"撤销后: latest_action_kind=undo, 恢复状态={last_undo.get('restored_status')}")

        code, out, err = run_cli(["overview"], work_dir=str(work_dir))
        assert "类型:   撤销标注" in out, f"CLI 应显示撤销标注类型: {out[:500]}"
        assert "恢复状态" in out or "根因" in out
        result.add_detail("CLI 概览正确显示撤销标注详情")

        result.success("撤销标注后概览正确显示撤销动作")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_undo_label_then_new_label_latest(test_dir: Path, example_dir: Path) -> TestResult:
    """测试2: 撤销标注后再做新标注，概览应显示新标注（更新的那个）"""
    result = TestResult("撤销后新标注，概览显示更新的标注")
    try:
        work_dir = test_dir / "undo_then_label"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        shutil.copy(example_dir / "app.log", work_dir / "examples" / "app.log")

        run_cli(["create", "--name", "撤销后新标注测试"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))
        code, out, err = run_cli(["timeline", "--limit", "2"], work_dir=str(work_dir))
        eids = extract_event_ids(out)

        run_cli(["label", "--status", "root", eids[0]], work_dir=str(work_dir))
        run_cli(["undo-label"], work_dir=str(work_dir))
        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()
        snap1 = store.load_overview_snapshot(batch_id, auto_refresh=False)
        assert snap1.get("latest_action_kind") == "undo"
        assert snap1.get("undo_action_count") == 1
        result.add_detail("撤销后: latest=undo")

        run_cli(["label", "--status", "confirmed", eids[1]], work_dir=str(work_dir))
        snap2 = store.load_overview_snapshot(batch_id, auto_refresh=False)
        assert snap2.get("latest_action_kind") == "label", f"新标注后应为 label: {snap2.get('latest_action_kind')}"
        assert snap2.get("label_action_count") == 1
        assert snap2.get("undo_action_count") == 1
        latest = snap2.get("latest_action")
        assert latest.get("new_status") == EventStatus.CONFIRMED.value
        result.add_detail("新标注后: latest=label, 已确认")

        result.success("撤销后新标注正确覆盖最近动作")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_undo_import_events_and_stats_removed(test_dir: Path, example_dir: Path) -> TestResult:
    """测试3: 撤销导入后，事件被删除、来源统计正确更新（核心修复）"""
    result = TestResult("撤销导入后事件和来源统计被删除")
    try:
        work_dir = test_dir / "undo_import_events"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        shutil.copy(example_dir / "app.log", work_dir / "examples" / "app.log")
        shutil.copy(example_dir / "alerts.csv", work_dir / "examples" / "alerts.csv")
        shutil.copy(example_dir / "notes.json", work_dir / "examples" / "notes.json")

        run_cli(["create", "--name", "撤销导入事件删除测试"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))
        run_cli(["import", "examples/alerts.csv"], work_dir=str(work_dir))
        run_cli(["import", "examples/notes.json"], work_dir=str(work_dir))

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()

        snap_before = store.load_overview_snapshot(batch_id, auto_refresh=False)
        events_before = len(store.load_events(batch_id))
        assert snap_before.get("imported_file_count") == 3
        by_src_before = snap_before.get("events_by_source", {})
        assert by_src_before.get("log", 0) > 0
        assert by_src_before.get("alert", 0) > 0
        assert by_src_before.get("note", 0) > 0
        result.add_detail(f"撤销前: {events_before} 事件, log={by_src_before.get('log')}, alert={by_src_before.get('alert')}, note={by_src_before.get('note')}")

        note_src_before = by_src_before.get("note", 0)
        code, out, err = run_cli(["undo-import"], work_dir=str(work_dir))
        assert code == 0, f"undo-import 失败: {err}"

        snap_after = store.load_overview_snapshot(batch_id, auto_refresh=False)
        real_events_after = store.load_events(batch_id)
        event_count_after = len(real_events_after)
        note_count_after = sum(1 for e in real_events_after if e.source == EventSource.NOTE)

        assert event_count_after == events_before - note_src_before, \
            f"事件数未正确减少: 前={events_before}, 后={event_count_after}, 应减少={note_src_before}"
        assert note_count_after == 0, f"备注来源事件应全部被删除，实际剩 {note_count_after}"

        by_src_after = snap_after.get("events_by_source", {})
        assert "note" not in by_src_after or by_src_after.get("note", 0) == 0, \
            f"概览来源统计不应再有 note: {by_src_after}"
        assert snap_after.get("event_count") == event_count_after, \
            f"概览事件数 {snap_after.get('event_count')} != 真实 {event_count_after}"
        assert snap_after.get("imported_file_count") == 2

        last_undo = snap_after.get("last_undo_action")
        assert last_undo is not None
        assert last_undo.get("undo_type") == "undo_import"
        assert last_undo.get("filename") == "notes.json"
        assert last_undo.get("removed_event_count") == note_src_before
        result.add_detail(f"撤销后: {event_count_after} 事件, 撤销 removed={last_undo.get('removed_event_count')}")

        result.success("撤销导入后事件和来源统计正确删除")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_undo_import_parse_errors_removed(test_dir: Path, example_dir: Path) -> TestResult:
    """测试4: 撤销导入后，对应文件的解析错误也被删除"""
    result = TestResult("撤销导入后解析错误也被删除")
    try:
        work_dir = test_dir / "undo_import_errors"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        shutil.copy(example_dir / "app.log", work_dir / "examples" / "app.log")
        shutil.copy(example_dir / "alerts.csv", work_dir / "examples" / "alerts.csv")

        run_cli(["create", "--name", "撤销导入错误删除测试"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()
        errors_before = store.load_parse_errors(batch_id)
        log_errors_before = sum(1 for e in errors_before if e.source_file == "app.log")
        result.add_detail(f"撤销前解析错误: {len(errors_before)} 个 (app.log={log_errors_before})")

        run_cli(["import", "examples/alerts.csv"], work_dir=str(work_dir))
        snap_before = store.load_overview_snapshot(batch_id, auto_refresh=False)
        total_errors_before = snap_before.get("parse_error_count")
        assert total_errors_before > log_errors_before, "应包含 alerts.csv 的错误"
        result.add_detail(f"导入 alerts.csv 后解析错误: {total_errors_before}")

        run_cli(["undo-import"], work_dir=str(work_dir))
        snap_after = store.load_overview_snapshot(batch_id, auto_refresh=False)
        real_errors_after = store.load_parse_errors(batch_id)
        csv_errors = sum(1 for e in real_errors_after if e.source_file == "alerts.csv")
        assert csv_errors == 0, f"alerts.csv 的解析错误应全部删除，实际剩 {csv_errors}"

        expected_error_count = log_errors_before
        assert len(real_errors_after) == expected_error_count, \
            f"真实解析错误数 {len(real_errors_after)} != 预期 {expected_error_count}"
        assert snap_after.get("parse_error_count") == expected_error_count, \
            f"概览解析错误数 {snap_after.get('parse_error_count')} != 真实 {expected_error_count}"

        pe_by_file = snap_after.get("parse_errors_by_file", {})
        assert "alerts.csv" not in pe_by_file, f"pe_by_file 不应再有 alerts.csv: {pe_by_file}"
        result.add_detail(f"撤销后解析错误: {len(real_errors_after)}, 文件分布: {pe_by_file}")

        result.success("撤销导入后解析错误正确删除")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_undo_persistence_after_restart(test_dir: Path, example_dir: Path) -> TestResult:
    """测试5: 重启后撤销历史仍能查询（undo_history 持久化）"""
    result = TestResult("重启后撤销历史持久化")
    try:
        work_dir = test_dir / "undo_restart"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        shutil.copy(example_dir / "app.log", work_dir / "examples" / "app.log")
        shutil.copy(example_dir / "notes.json", work_dir / "examples" / "notes.json")

        run_cli(["create", "--name", "撤销重启测试"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))
        run_cli(["import", "examples/notes.json"], work_dir=str(work_dir))

        code, out, err = run_cli(["timeline", "--limit", "1"], work_dir=str(work_dir))
        eid = extract_event_ids(out)[0]
        run_cli(["label", "--status", "root", eid], work_dir=str(work_dir))
        run_cli(["undo-label"], work_dir=str(work_dir))
        run_cli(["undo-import"], work_dir=str(work_dir))

        store1 = StateStore(str(work_dir))
        batch_id = store1.get_active_batch()
        undo_hist1 = store1.get_undo_history(batch_id)
        snap1 = store1.load_overview_snapshot(batch_id, auto_refresh=False)
        assert len(undo_hist1) == 2, f"应有2条撤销历史: {len(undo_hist1)}"
        assert undo_hist1[0].get("undo_type") == "undo_label"
        assert undo_hist1[1].get("undo_type") == "undo_import"
        assert snap1.get("undo_action_count") == 2
        assert snap1.get("latest_action_kind") == "undo"
        latest = snap1.get("latest_action")
        assert latest.get("undo_type") == "undo_import"
        result.add_detail(f"重启前: 撤销历史={len(undo_hist1)} 条, 最近撤销类型={latest.get('undo_type')}")

        store2 = StateStore(str(work_dir))
        undo_hist2 = store2.get_undo_history(batch_id)
        snap2 = store2.load_overview_snapshot(batch_id, auto_refresh=False)
        assert len(undo_hist2) == 2
        assert snap2.get("undo_action_count") == 2
        assert snap2.get("latest_action_kind") == "undo"
        assert snap2.get("latest_action", {}).get("undo_type") == "undo_import"
        assert snap2.get("event_count") == snap1.get("event_count")
        assert snap2.get("imported_file_count") == snap1.get("imported_file_count")
        result.add_detail("重启后: 所有撤销字段一致")

        code, out, err = run_cli(["overview"], work_dir=str(work_dir))
        assert "撤销导入" in out
        assert "notes.json" in out
        assert "清理:" in out
        result.add_detail("重启后 CLI 正确显示撤销历史")

        result.success("重启后撤销历史持久化一致")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_repeated_undo_label_all_consumed(test_dir: Path, example_dir: Path) -> TestResult:
    """测试6: 重复撤销标注，全部消费完后 CLI 给出明确提示"""
    result = TestResult("重复撤销标注消费完给出提示")
    try:
        work_dir = test_dir / "repeat_undo_label"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        shutil.copy(example_dir / "app.log", work_dir / "examples" / "app.log")

        run_cli(["create", "--name", "重复撤销标注测试"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))
        code, out, err = run_cli(["timeline", "--limit", "3"], work_dir=str(work_dir))
        eids = extract_event_ids(out)
        assert len(eids) >= 3

        run_cli(["label", "--status", "confirmed", eids[0]], work_dir=str(work_dir))
        run_cli(["label", "--status", "root", eids[1]], work_dir=str(work_dir))
        run_cli(["label", "--status", "noise", eids[2]], work_dir=str(work_dir))

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()
        snap = store.load_overview_snapshot(batch_id, auto_refresh=False)
        assert snap.get("label_action_count") == 3
        result.add_detail("标注 3 次成功")

        for i in range(3):
            code, out, err = run_cli(["undo-label"], work_dir=str(work_dir))
            assert code == 0, f"第 {i+1} 次撤销失败: {err}"

        snap_after = store.load_overview_snapshot(batch_id, auto_refresh=False)
        assert snap_after.get("label_action_count") == 0
        assert snap_after.get("undo_action_count") == 3
        assert snap_after.get("last_label_action") is None
        last_undo = snap_after.get("last_undo_action")
        assert last_undo.get("undo_type") == "undo_label"
        result.add_detail("3 次撤销成功，label 清空，undo 累计 3")

        code, out, err = run_cli(["undo-label"], work_dir=str(work_dir))
        assert code == 0, f"空撤销不应报错: {err}"
        assert "没有可撤销" in out or "标注历史" in out or "可撤销" in out, \
            f"应给出明确提示: {out[:200]}"
        result.add_detail("空撤销时明确提示")

        snap_empty = store.load_overview_snapshot(batch_id, auto_refresh=False)
        assert snap_empty.get("undo_action_count") == 3, "空撤销不应增加 undo 计数"
        result.add_detail("空撤销无副作用")

        result.success("重复撤销标注全部消费后提示正确")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_repeated_undo_import_all_consumed(test_dir: Path, example_dir: Path) -> TestResult:
    """测试7: 重复撤销导入，全部消费完后提示，事件统计也逐步清空"""
    result = TestResult("重复撤销导入消费完给出提示，统计逐步清空")
    try:
        work_dir = test_dir / "repeat_undo_import"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        for f in ["app.log", "alerts.csv", "notes.json"]:
            shutil.copy(example_dir / f, work_dir / "examples" / f)

        run_cli(["create", "--name", "重复撤销导入测试"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))
        run_cli(["import", "examples/alerts.csv"], work_dir=str(work_dir))
        run_cli(["import", "examples/notes.json"], work_dir=str(work_dir))

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()
        snap_before = store.load_overview_snapshot(batch_id, auto_refresh=False)
        total_events_before = snap_before.get("event_count")
        result.add_detail(f"初始: 3 个文件, {total_events_before} 事件")

        expected_left = total_events_before
        for i, expect_file in enumerate(["notes.json", "alerts.csv", "app.log"]):
            code, out, err = run_cli(["undo-import"], work_dir=str(work_dir))
            assert code == 0, f"第 {i+1} 次撤销导入失败: {err}"
            snap = store.load_overview_snapshot(batch_id, auto_refresh=False)
            undo_last = snap.get("last_undo_action")
            assert undo_last.get("filename") == expect_file, \
                f"第 {i+1} 次撤销应删除 {expect_file}, 实际 {undo_last.get('filename')}"
            removed = undo_last.get("removed_event_count", 0)
            expected_left -= removed
            real = len(store.load_events(batch_id))
            assert real == expected_left, f"第 {i+1} 次撤销后事件数 真实={real} != 预期={expected_left}"
            assert snap.get("event_count") == real, f"概览事件数与真实不一致"
            assert snap.get("imported_file_count") == 3 - (i + 1)
            result.add_detail(f"撤销 {i+1}: 删除 {expect_file} 含 {removed} 事件, 剩 {real} 事件")

        snap_done = store.load_overview_snapshot(batch_id, auto_refresh=False)
        assert snap_done.get("event_count") == 0
        assert snap_done.get("imported_file_count") == 0
        assert snap_done.get("undo_action_count") == 3
        result.add_detail("3 次撤销后: 0 事件, 0 文件, 3 次撤销")

        code, out, err = run_cli(["undo-import"], work_dir=str(work_dir))
        assert code == 0, f"空撤销导入不应报错: {err}"
        assert "没有可撤销" in out or "导入记录" in out or "可撤销" in out
        result.add_detail("空撤销导入时给出明确提示")

        result.success("重复撤销导入逐步清空统计，空撤销提示正确")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_undo_no_history_cli_prompt(test_dir: Path, example_dir: Path) -> TestResult:
    """测试8: 缺少历史记录时 CLI 概览给出明确提示"""
    result = TestResult("缺少历史时概览明确提示")
    try:
        work_dir = test_dir / "no_history_prompt"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        shutil.copy(example_dir / "app.log", work_dir / "examples" / "app.log")

        run_cli(["create", "--name", "无历史提示测试"], work_dir=str(work_dir))
        code, out, err = run_cli(["overview"], work_dir=str(work_dir))
        assert code == 0
        assert "暂无标注或撤销记录" in out, f"空批次应显示暂无记录: {out[:400]}"
        result.add_detail("空批次: '暂无标注或撤销记录' ✓")

        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))
        code, out, err = run_cli(["timeline", "--limit", "2"], work_dir=str(work_dir))
        eids = extract_event_ids(out)
        run_cli(["label", "--status", "root", eids[0]], work_dir=str(work_dir))
        run_cli(["label", "--status", "confirmed", eids[1]], work_dir=str(work_dir))
        run_cli(["undo-label"], work_dir=str(work_dir))
        run_cli(["undo-label"], work_dir=str(work_dir))

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()
        snap = store.load_overview_snapshot(batch_id, auto_refresh=False)
        assert snap.get("label_action_count") == 0
        assert snap.get("undo_action_count") == 2

        code, out, err = run_cli(["overview"], work_dir=str(work_dir))
        assert "类型:   撤销标注" in out, f"最近动作应是撤销标注: {out[:500]}"
        assert "标注 0 次" in out or "撤销 2 次" in out, f"应显示统计: {out[:500]}"
        result.add_detail("全部撤销后: 正确显示撤销动作为最近动作 ✓")

        result.success("缺少历史时概览提示正确")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_full_chain_undo_consistency(test_dir: Path, example_dir: Path) -> TestResult:
    """测试9: 完整链路（标注撤销+导入撤销交替）最终状态与落库一致"""
    result = TestResult("完整链路撤销后概览与落库完全一致")
    try:
        work_dir = test_dir / "full_chain_undo"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        for f in ["app.log", "alerts.csv", "notes.json"]:
            shutil.copy(example_dir / f, work_dir / "examples" / f)

        run_cli(["create", "--name", "完整撤销链路", "--description", "交替撤销测试"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))
        run_cli(["import", "examples/alerts.csv"], work_dir=str(work_dir))
        code, out, err = run_cli(["timeline", "--limit", "5"], work_dir=str(work_dir))
        eids = extract_event_ids(out)
        assert len(eids) >= 3
        run_cli(["label", "--status", "root", eids[0]], work_dir=str(work_dir))
        run_cli(["label", "--status", "noise", eids[1], "--notes", "噪声事件"], work_dir=str(work_dir))
        run_cli(["undo-label"], work_dir=str(work_dir))
        run_cli(["import", "examples/notes.json"], work_dir=str(work_dir))
        run_cli(["label", "--status", "confirmed", eids[2], "--notes", "已确认处理"], work_dir=str(work_dir))
        run_cli(["undo-import"], work_dir=str(work_dir))
        run_cli(["export", "--format", "markdown", "--output", "fchain.md", "--save-internal"], work_dir=str(work_dir))
        run_cli(["undo-label"], work_dir=str(work_dir))

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()
        snap = store.load_overview_snapshot(batch_id, auto_refresh=False)

        real_events = store.load_events(batch_id)
        real_errors = store.load_parse_errors(batch_id)
        real_imports = store.get_imported_files(batch_id)
        real_label_hist = store.get_label_history(batch_id)
        real_undo_hist = store.get_undo_history(batch_id)
        real_exports = store.get_exports(batch_id)
        real_config = store.load_config(batch_id)

        by_src_real = {}
        for e in real_events:
            k = e.source.value
            by_src_real[k] = by_src_real.get(k, 0) + 1
        by_status_real = {}
        for e in real_events:
            k = e.status.value
            by_status_real[k] = by_status_real.get(k, 0) + 1
        pe_by_file_real = {}
        for err in real_errors:
            pe_by_file_real[err.source_file] = pe_by_file_real.get(err.source_file, 0) + 1

        assert snap.get("event_count") == len(real_events), \
            f"event_count: 概览={snap.get('event_count')} 真实={len(real_events)}"
        assert snap.get("events_by_source") == by_src_real, \
            f"events_by_source: 概览={snap.get('events_by_source')} 真实={by_src_real}"
        assert snap.get("events_by_status") == by_status_real, \
            f"events_by_status: 概览={snap.get('events_by_status')} 真实={by_status_real}"
        assert snap.get("parse_error_count") == len(real_errors), \
            f"parse_error_count: 概览={snap.get('parse_error_count')} 真实={len(real_errors)}"
        assert snap.get("parse_errors_by_file") == pe_by_file_real, \
            f"parse_errors_by_file: 概览={snap.get('parse_errors_by_file')} 真实={pe_by_file_real}"
        assert snap.get("imported_file_count") == len(real_imports), \
            f"imported_file_count: 概览={snap.get('imported_file_count')} 真实={len(real_imports)}"
        snap_import_names = sorted(f.get("filename") for f in snap.get("imported_files", []))
        real_import_names = sorted(f.get("filename") for f in real_imports)
        assert snap_import_names == real_import_names, f"imported_files 不一致"
        assert snap.get("label_action_count") == len(real_label_hist), \
            f"label_action_count: 概览={snap.get('label_action_count')} 真实={len(real_label_hist)}"
        assert snap.get("undo_action_count") == len(real_undo_hist), \
            f"undo_action_count: 概览={snap.get('undo_action_count')} 真实={len(real_undo_hist)}"
        assert snap.get("export_count") == len(real_exports), \
            f"export_count: 概览={snap.get('export_count')} 真实={len(real_exports)}"
        assert snap.get("rule_version") == real_config.rule_version, \
            f"rule_version: 概览={snap.get('rule_version')} 真实={real_config.rule_version}"
        assert snap.get("dedup_window_seconds") == real_config.dedup_window_seconds
        assert snap.get("gap_threshold_seconds") == real_config.gap_threshold_seconds

        if real_undo_hist:
            last_undo_time = real_undo_hist[-1].get("created_at", "")
            last_label_time = ""
            if real_label_hist:
                last_label_time = real_label_hist[-1].created_at.isoformat()
            if last_undo_time >= last_label_time:
                assert snap.get("latest_action_kind") == "undo"
                lu = snap.get("last_undo_action")
                assert lu.get("filename") == real_undo_hist[-1].get("detail", {}).get("filename") or \
                    lu.get("event_id") == real_undo_hist[-1].get("detail", {}).get("event_id")
        result.add_detail(f"最终校验通过: 事件={len(real_events)}, 导入={len(real_imports)}, 标注={len(real_label_hist)}, 撤销={len(real_undo_hist)}, 导出={len(real_exports)}")

        result.success("完整链路后概览与落库状态完全一致")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_undo_import_source_stats_match_real_data(test_dir: Path, example_dir: Path) -> TestResult:
    """测试10: 撤销导入后，概览中的来源分布与真实 events 数据严格对齐（关键回归）"""
    result = TestResult("撤销导入后来源统计严格对齐真实数据")
    try:
        work_dir = test_dir / "src_stats_align"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        for f in ["app.log", "alerts.csv", "notes.json"]:
            shutil.copy(example_dir / f, work_dir / "examples" / f)

        run_cli(["create", "--name", "来源统计对齐测试"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))
        run_cli(["import", "examples/alerts.csv"], work_dir=str(work_dir))
        run_cli(["import", "examples/notes.json"], work_dir=str(work_dir))

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()

        for step in range(3):
            run_cli(["undo-import"], work_dir=str(work_dir))
            snap = store.load_overview_snapshot(batch_id, auto_refresh=False)
            real_events = store.load_events(batch_id)
            real_by_src: dict = {}
            for e in real_events:
                k = e.source.value
                real_by_src[k] = real_by_src.get(k, 0) + 1
            snap_by_src = snap.get("events_by_source", {})
            assert snap_by_src == real_by_src, \
                f"第 {step+1} 次撤销后来源分布不一致: 概览={snap_by_src} 真实={real_by_src}"
            assert snap.get("event_count") == len(real_events)
            real_imports = store.get_imported_files(batch_id)
            assert snap.get("imported_file_count") == len(real_imports)
            for src_key, cnt in snap_by_src.items():
                real_count = sum(1 for e in real_events if e.source.value == src_key)
                assert cnt == real_count, f"来源 {src_key}: 概览={cnt} 真实={real_count}"
            result.add_detail(f"第 {step+1} 次撤销: 来源分布对齐 ✓ (共 {len(real_events)} 事件, {len(real_imports)} 文件)")

        result.success("撤销导入后来源统计与真实数据严格对齐")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


if __name__ == "__main__":
    run_tests()
