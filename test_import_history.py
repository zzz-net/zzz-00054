#!/usr/bin/env python3
"""
导入历史操作面板 + 导出核对 回归测试套件
覆盖场景：
1. 连续导入后按序号查看/撤销/恢复
2. 重启后再次操作（显示索引映射恢复）
3. 恢复后再导入的冲突提示
4. 导出文件事件数为0的校验
5. 导出数量不一致的校验
6. 显示序号与实际记录一致性
7. 完整 CLI 链路跑通
"""

import os
import sys
import io
import json
import tempfile
import shutil
import subprocess
import time
from pathlib import Path
from datetime import datetime
from typing import List, Tuple, Dict

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent))

from timeline_review.storage import StateStore
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


def run_tests():
    results = []
    test_dir = Path(tempfile.mkdtemp(prefix="tlr_import_hist_test_"))
    example_dir = Path(__file__).parent / "examples"
    print(f"🧪 测试工作目录: {test_dir}")
    print()

    try:
        results.append(test_sequential_import_view_undo_restore(test_dir, example_dir))
        results.append(test_restart_index_mapping_recovery(test_dir, example_dir))
        results.append(test_restore_then_reimport_conflict(test_dir, example_dir))
        results.append(test_export_empty_events_validation(test_dir, example_dir))
        results.append(test_export_count_mismatch_validation(test_dir, example_dir))
        results.append(test_display_index_consistency(test_dir, example_dir))
        results.append(test_full_chain_with_import_history(test_dir, example_dir))
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
    print("\n🎉 所有导入历史+导出核对测试通过!")


def test_sequential_import_view_undo_restore(test_dir: Path, example_dir: Path) -> TestResult:
    result = TestResult("连续导入后按序号查看/撤销/恢复")
    try:
        work_dir = test_dir / "seq_import"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        shutil.copy(example_dir / "app.log", work_dir / "examples" / "app.log")
        shutil.copy(example_dir / "alerts.csv", work_dir / "examples" / "alerts.csv")

        run_cli(["create", "--name", "连续导入测试"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))
        time.sleep(0.3)
        run_cli(["import", "examples/alerts.csv"], work_dir=str(work_dir))

        code, out, err = run_cli(["import-history"], work_dir=str(work_dir))
        assert code == 0, f"import-history 失败: {err}"
        assert "app.log" in out, "应显示 app.log"
        assert "alerts.csv" in out, "应显示 alerts.csv"
        result.add_detail("import-history 列表正常显示两个导入")

        code, out, err = run_cli(["import-history", "--detail", "1"], work_dir=str(work_dir))
        assert code == 0, f"import-history --detail 1 失败: {err}"
        assert "app.log" in out, "序号1应为 app.log"
        assert "导入详情" in out, "应显示导入详情"
        result.add_detail("import-history --detail 1 正确显示 app.log 详情")

        code, out, err = run_cli(["import-history", "--detail", "2"], work_dir=str(work_dir))
        assert code == 0, f"import-history --detail 2 失败: {err}"
        assert "alerts.csv" in out, "序号2应为 alerts.csv"
        result.add_detail("import-history --detail 2 正确显示 alerts.csv 详情")

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()
        events_before = len(store.load_events(batch_id))

        code, out, err = run_cli(["import-history", "--undo", "2"], work_dir=str(work_dir))
        assert code == 0, f"import-history --undo 2 失败: {err}"
        assert "已撤销导入" in out, "应显示撤销成功"
        result.add_detail("import-history --undo 2 成功撤销 alerts.csv 导入")

        events_after_undo = len(store.load_events(batch_id))
        assert events_after_undo < events_before, f"撤销后事件数应减少: {events_before} → {events_after_undo}"
        result.add_detail(f"撤销后事件数: {events_before} → {events_after_undo}")

        code, out, err = run_cli(["import-history", "--all"], work_dir=str(work_dir))
        assert code == 0, f"import-history --all 失败: {err}"
        assert "已撤销" in out or "undone" in out, "应显示已撤销状态"
        result.add_detail("import-history --all 显示已撤销记录")

        code, out, err = run_cli(["import-history", "--restore", "2"], work_dir=str(work_dir))
        assert code == 0, f"import-history --restore 2 失败: {err}"
        assert "已恢复导入" in out, "应显示恢复成功"
        result.add_detail("import-history --restore 2 成功恢复 alerts.csv 导入")

        events_after_restore = len(store.load_events(batch_id))
        assert events_after_restore == events_before, f"恢复后事件数应恢复: {events_before} → {events_after_restore}"
        result.add_detail(f"恢复后事件数: {events_after_undo} → {events_after_restore}")

        code, out, err = run_cli(["import-detail", "--index", "1"], work_dir=str(work_dir))
        assert code == 0, f"import-detail --index 1 失败: {err}"
        assert "app.log" in out, "import-detail --index 1 应显示 app.log"
        result.add_detail("import-detail --index 1 正确显示 app.log 详情")

        code, out, err = run_cli(["undo-import", "--index", "2"], work_dir=str(work_dir))
        assert code == 0, f"undo-import --index 2 失败: {err}"
        assert "已撤销导入" in out, "undo-import --index 应成功"
        result.add_detail("undo-import --index 2 成功撤销")

        code, out, err = run_cli(["restore-import", "--index", "2"], work_dir=str(work_dir))
        assert code == 0, f"restore-import --index 2 失败: {err}"
        assert "已恢复导入" in out, "restore-import --index 应成功"
        result.add_detail("restore-import --index 2 成功恢复")

        result.success("连续导入→按序号查看/撤销/恢复 全流程正确")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_restart_index_mapping_recovery(test_dir: Path, example_dir: Path) -> TestResult:
    result = TestResult("重启后再次操作（显示索引映射恢复）")
    try:
        work_dir = test_dir / "restart_idx"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        shutil.copy(example_dir / "app.log", work_dir / "examples" / "app.log")
        shutil.copy(example_dir / "alerts.csv", work_dir / "examples" / "alerts.csv")

        run_cli(["create", "--name", "重启索引测试"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))
        time.sleep(0.3)
        run_cli(["import", "examples/alerts.csv"], work_dir=str(work_dir))

        store1 = StateStore(str(work_dir))
        batch_id = store1.get_active_batch()
        entries1 = store1.get_all_imports_with_index(batch_id)
        assert len(entries1) == 2, f"应有2条导入记录，实际 {len(entries1)}"
        id1_import_id = entries1[0].get("import_id")
        id2_import_id = entries1[1].get("import_id")
        result.add_detail(f"实例1: 序号1={id1_import_id[:16]}... 序号2={id2_import_id[:16]}...")

        store2 = StateStore(str(work_dir))
        rebuild = store2.rebuild_state_after_restart(batch_id)
        result.add_detail(f"重建结果: {rebuild.get('actions', [])}")

        entries2 = store2.get_all_imports_with_index(batch_id)
        assert len(entries2) == 2, f"重启后应有2条导入记录，实际 {len(entries2)}"
        assert entries2[0].get("import_id") == id1_import_id, "重启后序号1的import_id应一致"
        assert entries2[1].get("import_id") == id2_import_id, "重启后序号2的import_id应一致"
        result.add_detail("重启后显示索引映射与之前一致")

        code, out, err = run_cli(["import-history", "--detail", "1"], work_dir=str(work_dir))
        assert code == 0, f"重启后 import-history --detail 1 失败: {err}"
        assert "app.log" in out, "重启后序号1应为 app.log"
        result.add_detail("重启后 CLI 按序号查看正确")

        code, out, err = run_cli(["import-history", "--undo", "2"], work_dir=str(work_dir))
        assert code == 0, f"重启后 import-history --undo 2 失败: {err}"
        assert "已撤销导入" in out, "重启后按序号撤销应成功"
        result.add_detail("重启后 CLI 按序号撤销成功")

        code, out, err = run_cli(["import-history", "--restore", "2"], work_dir=str(work_dir))
        assert code == 0, f"重启后 import-history --restore 2 失败: {err}"
        assert "已恢复导入" in out, "重启后按序号恢复应成功"
        result.add_detail("重启后 CLI 按序号恢复成功")

        result.success("重启后显示索引映射恢复正确，操作正常")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_restore_then_reimport_conflict(test_dir: Path, example_dir: Path) -> TestResult:
    result = TestResult("恢复后再导入的冲突提示")
    try:
        work_dir = test_dir / "restore_conflict"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        shutil.copy(example_dir / "app.log", work_dir / "examples" / "app.log")

        run_cli(["create", "--name", "恢复后冲突测试"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()
        entries = store.get_all_imports_with_index(batch_id)
        assert len(entries) == 1
        import_id_1 = entries[0].get("import_id")

        code, out, err = run_cli(["import-history", "--undo", "1"], work_dir=str(work_dir))
        assert code == 0, f"撤销失败: {err}"
        assert "已撤销导入" in out
        result.add_detail("序号1撤销成功")

        code, out, err = run_cli(["import", "examples/app.log"], work_dir=str(work_dir))
        assert code == 0, f"撤销后再次导入不应崩溃: {err}"
        assert "已撤销的导入记录" in out or "restore-import" in out or "restore_or_force" in out or "已导入过" in out, \
            f"应提示冲突或跳过，实际输出: {out[:200]}"
        result.add_detail(f"撤销后再次导入正确提示: {out[:100]}")

        code, out, err = run_cli(["import-history", "--restore", "1"], work_dir=str(work_dir))
        assert code == 0, f"恢复失败: {err}"
        assert "已恢复导入" in out
        result.add_detail("恢复序号1成功")

        code, out, err = run_cli(["import", "examples/app.log"], work_dir=str(work_dir))
        assert code == 0, f"恢复后再次导入不应崩溃: {err}"
        assert "已导入过" in out or "跳过" in out or "冲突" in out, \
            f"恢复后再次导入应提示冲突或跳过，实际: {out[:200]}"
        result.add_detail("恢复后再次导入正确提示跳过/冲突")

        store2 = StateStore(str(work_dir))
        log = store2.get_change_log(batch_id, limit=20, change_type="import_change")
        conflict_logs = [e for e in log if "conflict" in json.dumps(e.get("detail", {}), ensure_ascii=False)]
        result.add_detail(f"冲突相关日志: {len(conflict_logs)} 条")

        dup_logs = [e for e in log if e.get("detail", {}).get("action") == "skipped_duplicate"]
        assert len(dup_logs) >= 1, "应有跳过重复的日志"
        result.add_detail(f"跳过重复日志: {len(dup_logs)} 条")

        result.success("恢复后再导入冲突提示和日志正确")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_export_empty_events_validation(test_dir: Path, example_dir: Path) -> TestResult:
    result = TestResult("导出文件事件数为0的校验")
    try:
        work_dir = test_dir / "export_empty"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        shutil.copy(example_dir / "app.log", work_dir / "examples" / "app.log")

        run_cli(["create", "--name", "导出空校验测试"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()

        verify = store.verify_export_consistency(batch_id, "导出文件内容无事件", "markdown")
        assert not verify.get("consistent", True), "空导出内容应判定不一致"
        empty_mismatches = [m for m in verify.get("mismatches", []) if m.get("field") == "event_count"]
        assert len(empty_mismatches) > 0, "应包含 event_count 不一致"
        assert empty_mismatches[0].get("export") == 0, "导出事件数应为0"
        reason = empty_mismatches[0].get("reason", "")
        assert "0" in reason or "空" in reason or "事件数为0" in reason, f"原因应说明事件数为0，实际: {reason}"
        result.add_detail(f"空导出校验: inconsistent={not verify.get('consistent')}, reason={reason}")

        csv_verify = store.verify_export_consistency(batch_id, "id,timestamp\n", "csv")
        assert not csv_verify.get("consistent", True), "只有表头的CSV应判定不一致"
        result.add_detail("CSV只有表头时也正确判定不一致")

        result.success("导出文件事件数为0的校验正确")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_export_count_mismatch_validation(test_dir: Path, example_dir: Path) -> TestResult:
    result = TestResult("导出数量不一致的校验")
    try:
        work_dir = test_dir / "export_mismatch"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        shutil.copy(example_dir / "app.log", work_dir / "examples" / "app.log")

        run_cli(["create", "--name", "导出不一致校验测试"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()
        real_count = len(store.load_events(batch_id))
        assert real_count > 0, "应有事件"
        result.add_detail(f"实际事件数: {real_count}")

        fake_md = f"# 报告\n\n事件总数: {real_count + 5}\n"
        verify = store.verify_export_consistency(batch_id, fake_md, "markdown")
        assert not verify.get("consistent", True), "事件数不一致应判定不一致"
        mismatches = [m for m in verify.get("mismatches", []) if m.get("field") == "event_count"]
        assert len(mismatches) > 0, "应包含 event_count 不一致"
        assert mismatches[0].get("export") == real_count + 5, "导出事件数应为篡改值"
        assert mismatches[0].get("actual") == real_count, "实际事件数应为正确值"
        diff = mismatches[0].get("diff", 0)
        assert diff == -5, f"差异应为-5，实际 {diff}"
        reason = mismatches[0].get("reason", "")
        assert "不一致" in reason, f"原因应包含不一致，实际: {reason}"
        result.add_detail(f"不一致校验: export={mismatches[0]['export']} actual={mismatches[0]['actual']} diff={diff} reason={reason}")

        correct_md = f"# 报告\n\n事件总数: {real_count}\n"
        verify_correct = store.verify_export_consistency(batch_id, correct_md, "markdown")
        assert verify_correct.get("consistent", False), "事件数一致应判定一致"
        result.add_detail("正确事件数时判定一致")

        code, out, err = run_cli(
            ["export", "--format", "markdown", "--output", "test_report.md", "--verify"],
            work_dir=str(work_dir)
        )
        assert code == 0, f"导出+核对失败: {err}"
        assert "一致" in out, "正常导出核对应一致"
        result.add_detail("CLI 导出 --verify 正常场景一致")

        result.success("导出数量不一致的校验正确")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_display_index_consistency(test_dir: Path, example_dir: Path) -> TestResult:
    result = TestResult("显示序号与实际记录一致性")
    try:
        work_dir = test_dir / "idx_consistency"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        shutil.copy(example_dir / "app.log", work_dir / "examples" / "app.log")
        shutil.copy(example_dir / "alerts.csv", work_dir / "examples" / "alerts.csv")
        shutil.copy(example_dir / "notes.json", work_dir / "examples" / "notes.json")

        run_cli(["create", "--name", "序号一致性测试"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))
        time.sleep(0.3)
        run_cli(["import", "examples/alerts.csv"], work_dir=str(work_dir))
        time.sleep(0.3)
        run_cli(["import", "examples/notes.json"], work_dir=str(work_dir))

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()
        entries = store.get_all_imports_with_index(batch_id)
        assert len(entries) == 3, f"应有3条导入记录，实际 {len(entries)}"

        display_indices = [e.get("display_index") for e in entries]
        assert display_indices == [1, 2, 3], f"显示序号应为[1,2,3]，实际 {display_indices}"
        result.add_detail(f"初始序号: {display_indices}")

        import_ids = [e.get("import_id") for e in entries]
        for i, entry in enumerate(entries):
            resolved = store.resolve_import_by_display_index(batch_id, i + 1)
            assert resolved is not None, f"序号 {i+1} 应能解析"
            assert resolved.get("import_id") == import_ids[i], \
                f"序号 {i+1} 的 import_id 不匹配"
        result.add_detail("所有序号都能正确解析到对应记录")

        code, out, err = run_cli(["import-history"], work_dir=str(work_dir))
        assert code == 0, f"import-history 失败: {err}"
        assert "app.log" in out, "应显示 app.log"
        assert "alerts.csv" in out, "应显示 alerts.csv"
        assert "notes.json" in out, "应显示 notes.json"
        result.add_detail("CLI 列表正确显示三个导入文件")

        code, out, err = run_cli(["import-history", "--undo", "2"], work_dir=str(work_dir))
        assert code == 0, f"撤销序号2失败: {err}"

        entries_after = store.get_all_imports_with_index(batch_id)
        assert len(entries_after) == 3, "撤销后总记录数不变"
        display_after = [e.get("display_index") for e in entries_after]
        assert display_after == [1, 2, 3], f"撤销后序号应仍为[1,2,3]，实际 {display_after}"
        assert entries_after[1].get("status") == "undone", "序号2应为已撤销"
        result.add_detail("撤销后序号不变，状态正确")

        code, out, err = run_cli(["import-history", "--all"], work_dir=str(work_dir))
        assert code == 0, f"import-history --all 失败: {err}"

        resolved_2 = store.resolve_import_by_display_index(batch_id, 2)
        assert resolved_2 is not None, "撤销后序号2仍应可解析"
        assert resolved_2.get("status") == "undone", "序号2应为已撤销"
        result.add_detail("撤销后序号2仍能正确解析")

        code, out, err = run_cli(["import-history", "--detail", "3"], work_dir=str(work_dir))
        assert code == 0, f"撤销后查看序号3失败: {err}"
        assert "notes.json" in out, "序号3应为 notes.json"
        result.add_detail("撤销后序号3的详情仍正确")

        result.success("显示序号与实际记录始终一致")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_full_chain_with_import_history(test_dir: Path, example_dir: Path) -> TestResult:
    result = TestResult("完整 CLI 链路（含 import-history 操作）")
    try:
        work_dir = test_dir / "full_chain_hist"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        for f in ["app.log", "alerts.csv", "notes.json"]:
            shutil.copy(example_dir / f, work_dir / "examples" / f)

        run_cli(["create", "--name", "完整链路测试", "--description", "含import-history"], work_dir=str(work_dir))

        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))
        time.sleep(0.3)
        run_cli(["import", "examples/alerts.csv"], work_dir=str(work_dir))
        time.sleep(0.3)
        run_cli(["import", "examples/notes.json"], work_dir=str(work_dir))

        code, out, err = run_cli(["import-history"], work_dir=str(work_dir))
        assert code == 0, f"import-history 失败: {err}"
        result.add_detail("import-history 列表成功")

        code, out, err = run_cli(["import-history", "--detail", "1"], work_dir=str(work_dir))
        assert code == 0, f"detail 1 失败: {err}"
        result.add_detail("--detail 1 成功")

        code, out, err = run_cli(["import-history", "--undo", "3"], work_dir=str(work_dir))
        assert code == 0, f"undo 3 失败: {err}"
        assert "已撤销导入" in out
        result.add_detail("--undo 3 成功")

        code, out, err = run_cli(["import-history", "--all"], work_dir=str(work_dir))
        assert code == 0, f"--all 失败: {err}"
        result.add_detail("--all 显示含已撤销记录")

        code, out, err = run_cli(["import-history", "--restore", "3"], work_dir=str(work_dir))
        assert code == 0, f"restore 3 失败: {err}"
        assert "已恢复导入" in out
        result.add_detail("--restore 3 成功")

        code, out, err = run_cli(["export", "--format", "markdown", "--output", "chain_report.md", "--verify"],
                                  work_dir=str(work_dir))
        assert code == 0, f"导出失败: {err}"
        assert "一致" in out or "✅" in out, "导出核对应一致"
        result.add_detail("导出+核对成功")

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()

        store2 = StateStore(str(work_dir))
        rebuild = store2.rebuild_state_after_restart(batch_id)
        result.add_detail(f"重启重建: {rebuild.get('actions', [])}")

        code, out, err = run_cli(["import-history", "--detail", "2"], work_dir=str(work_dir))
        assert code == 0, f"重启后 detail 2 失败: {err}"
        assert "alerts.csv" in out
        result.add_detail("重启后按序号查看正常")

        code, out, err = run_cli(["undo-import", "--index", "3"], work_dir=str(work_dir))
        assert code == 0, f"重启后 undo-import --index 3 失败: {err}"
        result.add_detail("重启后 undo-import --index 成功")

        code, out, err = run_cli(["restore-import", "--index", "3"], work_dir=str(work_dir))
        assert code == 0, f"重启后 restore-import --index 3 失败: {err}"
        result.add_detail("重启后 restore-import --index 成功")

        final_check = store2.check_snapshot_consistency(batch_id)
        assert final_check.get("consistent", False), "最终快照应一致"
        result.add_detail("最终快照一致性检查通过")

        result.success("完整链路所有步骤成功，含 import-history 操作")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


if __name__ == "__main__":
    run_tests()
