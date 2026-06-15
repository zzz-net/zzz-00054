#!/usr/bin/env python3
"""
导入导出核对中心 - 完整回归测试套件
覆盖场景:
1. audit-center 完整链路: 列表→详情→撤销→恢复→导出核对
2. 跨重启后序号映射稳定，按序号继续操作
3. 导入/恢复/导出三段链路的冲突和异常提示
4. 空导出校验失败场景
5. 数量不一致校验失败场景
6. 重复恢复冲突检测
7. 核对规则配置化开关
8. 成功和失败场景的真实命令执行
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
from timeline_review.config import RuleConfig, AuditRuleConfig


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


def contains_any(text: str, keywords: List[str]) -> bool:
    """检查文本是否包含任一关键词，用于处理编码问题"""
    text_lower = text.lower()
    for kw in keywords:
        if kw.lower() in text_lower:
            return True
    return False


def run_tests():
    results = []
    test_dir = Path(tempfile.mkdtemp(prefix="audit_center_test_"))
    example_dir = Path(__file__).parent / "examples"
    print(f"🧪 测试工作目录: {test_dir}")
    print()

    try:
        results.append(test_audit_center_full_chain(test_dir, example_dir))
        results.append(test_restart_index_mapping_stable(test_dir, example_dir))
        results.append(test_import_conflict_detection(test_dir, example_dir))
        results.append(test_restore_conflict_detection(test_dir, example_dir))
        results.append(test_empty_export_validation(test_dir, example_dir))
        results.append(test_count_mismatch_validation(test_dir, example_dir))
        results.append(test_audit_rules_config(test_dir, example_dir))
        results.append(test_success_and_failure_scenarios(test_dir, example_dir))
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
    print("\n🎉 所有导入导出核对中心测试通过!")


def test_audit_center_full_chain(test_dir: Path, example_dir: Path) -> TestResult:
    result = TestResult("audit-center 完整链路: 列表→详情→撤销→恢复→导出核对")
    try:
        work_dir = test_dir / "full_chain"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        for f in ["app.log", "alerts.csv", "notes.json"]:
            shutil.copy(example_dir / f, work_dir / "examples" / f)

        run_cli(["create", "--name", "audit-full-chain"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))
        time.sleep(0.3)
        run_cli(["import", "examples/alerts.csv"], work_dir=str(work_dir))
        time.sleep(0.3)
        run_cli(["import", "examples/notes.json"], work_dir=str(work_dir))

        code, out, err = run_cli(["audit-center", "--list"], work_dir=str(work_dir))
        assert code == 0, f"audit-center --list 失败: {err}"
        assert contains_any(out, ["app.log"]), "应显示 app.log"
        assert contains_any(out, ["alerts.csv"]), "应显示 alerts.csv"
        assert contains_any(out, ["notes.json"]), "应显示 notes.json"
        result.add_detail("audit-center --list 正确显示三个导入操作")

        for i in range(1, 4):
            code, out, err = run_cli(["audit-center", "--detail", str(i)], work_dir=str(work_dir))
            assert code == 0, f"audit-center --detail {i} 失败: {err}"
            assert contains_any(out, ["import_id", "记录标识"]), "应显示记录标识"
            assert contains_any(out, ["事件统计", "event_stats"]), "应显示事件统计"
            assert contains_any(out, ["来源文件", "source_file"]), "应显示来源文件"
            assert contains_any(out, ["最近一次处理结果", "last_processed_result"]), "应显示最近处理结果"
        result.add_detail("audit-center --detail 1-3 均正确显示详情")

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()
        entries_before = store.get_all_imports_with_index(batch_id)
        assert len(entries_before) >= 2, "至少应有2条导入记录"
        
        active_indices = [e["display_index"] for e in entries_before if e.get("status") == "active"]
        assert len(active_indices) >= 1, "至少应有1个激活的导入"
        undo_index = active_indices[0]
        
        events_before_undo = len(store.load_events(batch_id))
        result.add_detail(f"撤销前事件数: {events_before_undo}")

        code, out, err = run_cli(["audit-center", "--undo", str(undo_index)], work_dir=str(work_dir))
        assert code == 0, f"audit-center --undo {undo_index} 失败: {err}"
        assert contains_any(out, ["撤销", "undo"]), "应显示撤销成功"
        result.add_detail(f"audit-center --undo {undo_index} 成功撤销")

        events_after_undo = len(store.load_events(batch_id))
        result.add_detail(f"撤销后事件数: {events_after_undo}")

        code, out, err = run_cli(["audit-center", "--list"], work_dir=str(work_dir))
        assert code == 0, f"撤销后 list 失败: {err}"
        assert contains_any(out, ["undone", "已撤销"]), "列表应显示已撤销状态"
        result.add_detail("撤销后列表正确显示已撤销状态")

        code, out, err = run_cli(["audit-center", "--restore", str(undo_index)], work_dir=str(work_dir))
        assert code == 0, f"audit-center --restore {undo_index} 失败: {err}"
        assert contains_any(out, ["恢复", "restore"]), "应显示恢复成功"
        result.add_detail(f"audit-center --restore {undo_index} 成功恢复")

        events_after_restore = len(store.load_events(batch_id))
        assert events_after_restore > events_after_undo, "恢复后事件数应增加"
        result.add_detail(f"恢复后事件数: {events_after_restore}")

        code, out, err = run_cli(
            ["audit-center", "--export-audit", "--format", "markdown", "--output", "audit_report.md"],
            work_dir=str(work_dir)
        )
        assert code == 0, f"audit-center --export-audit 失败: {err}"
        assert contains_any(out, ["一致", "通过", "consistent", "verified"]), "导出核对应通过"
        result.add_detail("audit-center --export-audit 导出并核对成功")

        code, out, err = run_cli(
            ["audit-center", "--verify-export", "audit_report.md"],
            work_dir=str(work_dir)
        )
        assert code == 0, f"audit-center --verify-export 失败: {err}"
        assert contains_any(out, ["一致", "通过", "consistent", "verified"]), "验证核对应通过"
        result.add_detail("audit-center --verify-export 验证成功")

        result.success("完整链路: 列表→详情→撤销→恢复→导出核对 全部成功")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_restart_index_mapping_stable(test_dir: Path, example_dir: Path) -> TestResult:
    result = TestResult("跨重启后序号映射稳定，按序号继续操作")
    try:
        work_dir = test_dir / "restart_stable"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        for f in ["app.log", "alerts.csv", "notes.json"]:
            shutil.copy(example_dir / f, work_dir / "examples" / f)

        run_cli(["create", "--name", "restart-stability-test"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))
        time.sleep(0.3)
        run_cli(["import", "examples/alerts.csv"], work_dir=str(work_dir))
        time.sleep(0.3)
        run_cli(["import", "examples/notes.json"], work_dir=str(work_dir))

        store1 = StateStore(str(work_dir))
        batch_id = store1.get_active_batch()
        ops1 = store1.get_audit_operations_list(batch_id, limit=10)
        assert len(ops1) == 3, f"应有3条操作记录，实际 {len(ops1)}"
        id1 = ops1[0].get("import_id")
        id2 = ops1[1].get("import_id")
        id3 = ops1[2].get("import_id")
        idx1 = ops1[0].get("display_index")
        idx2 = ops1[1].get("display_index")
        idx3 = ops1[2].get("display_index")
        result.add_detail(f"实例1: 序号{idx1}={id1[:12]}... 序号{idx2}={id2[:12]}... 序号{idx3}={id3[:12]}...")

        store2 = StateStore(str(work_dir))
        rebuild = store2.rebuild_state_after_restart(batch_id)
        result.add_detail(f"重启重建: {rebuild.get('actions', [])}")

        ops2 = store2.get_audit_operations_list(batch_id, limit=10)
        assert len(ops2) == 3, "重启后应有3条操作记录"
        id_map = {}
        for op in ops2:
            id_map[op.get("display_index")] = op.get("import_id")
        assert id_map.get(idx1) == id1, f"重启后序号{idx1}的import_id应一致"
        assert id_map.get(idx2) == id2, f"重启后序号{idx2}的import_id应一致"
        assert id_map.get(idx3) == id3, f"重启后序号{idx3}的import_id应一致"
        result.add_detail("重启后显示序号→import_id映射完全一致")

        code, out, err = run_cli(["audit-center", "--detail", str(idx2)], work_dir=str(work_dir))
        assert code == 0, f"重启后 detail {idx2} 失败: {err}"
        assert contains_any(out, ["alerts.csv"]), f"重启后序号{idx2}应为 alerts.csv"
        result.add_detail(f"重启后按序号{idx2}查看详情正确")

        code, out, err = run_cli(["audit-center", "--undo", str(idx2)], work_dir=str(work_dir))
        assert code == 0, f"重启后 undo {idx2} 失败: {err}"
        assert contains_any(out, ["撤销", "undo"]), f"重启后按序号{idx2}撤销应成功"
        result.add_detail(f"重启后按序号{idx2}撤销成功")

        code, out, err = run_cli(["audit-center", "--restore", str(idx2)], work_dir=str(work_dir))
        assert code == 0, f"重启后 restore {idx2} 失败: {err}"
        assert contains_any(out, ["恢复", "restore"]), f"重启后按序号{idx2}恢复应成功"
        result.add_detail(f"重启后按序号{idx2}恢复成功")

        code, out, err = run_cli(["audit-center", "--export-audit", "-o", "restart_report.md"], work_dir=str(work_dir))
        assert code == 0, f"重启后导出核对失败: {err}"
        assert contains_any(out, ["一致", "通过", "consistent"]), "重启后导出核对应通过"
        result.add_detail("重启后导出核对成功")

        result.success("跨重启后序号映射稳定，所有操作按序号正常执行")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_import_conflict_detection(test_dir: Path, example_dir: Path) -> TestResult:
    result = TestResult("导入冲突检测和提示")
    try:
        work_dir = test_dir / "import_conflict"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        shutil.copy(example_dir / "app.log", work_dir / "examples" / "app.log")

        run_cli(["create", "--name", "import-conflict-test"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))

        code, out, err = run_cli(["import", "examples/app.log"], work_dir=str(work_dir))
        assert code == 0, f"重复导入不应崩溃: {err}"
        assert contains_any(out, ["已导入", "跳过", "冲突", "duplicate", "skip"]), \
            f"重复导入应提示冲突或跳过，实际: {out[:200]}"
        result.add_detail(f"重复导入正确提示: {out[:100]}")

        run_cli(["audit-center", "--undo", "1"], work_dir=str(work_dir))
        code, out, err = run_cli(["import", "examples/app.log"], work_dir=str(work_dir))
        assert code == 0, f"撤销后再导入不应崩溃: {err}"
        assert contains_any(out, ["已撤销", "restore", "restore_or_force"]), \
            f"撤销后再导入应提示，实际: {out[:200]}"
        result.add_detail(f"撤销后再导入正确提示: {out[:100]}")

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()
        log = store.get_change_log(batch_id, limit=20, change_type="import_change")
        skip_logs = [e for e in log if e.get("detail", {}).get("action") == "skipped_duplicate"]
        assert len(skip_logs) >= 1, "应有跳过重复的日志"
        result.add_detail(f"跳过重复日志: {len(skip_logs)} 条")

        code, out, err = run_cli(["audit-center", "--restore", "1"], work_dir=str(work_dir))
        assert code == 0, f"恢复失败: {err}"
        result.add_detail("恢复序号1成功")

        code, out, err = run_cli(["import", "examples/app.log"], work_dir=str(work_dir))
        assert code == 0, f"恢复后再导入不应崩溃: {err}"
        assert contains_any(out, ["已导入", "跳过", "duplicate", "skip"]), \
            f"恢复后再导入应提示，实际: {out[:200]}"
        result.add_detail("恢复后再导入正确提示跳过")

        result.success("导入冲突检测和提示正确")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_restore_conflict_detection(test_dir: Path, example_dir: Path) -> TestResult:
    result = TestResult("重复恢复冲突检测")
    try:
        work_dir = test_dir / "restore_conflict"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        shutil.copy(example_dir / "app.log", work_dir / "examples" / "app.log")

        run_cli(["create", "--name", "restore-conflict-test"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()

        code, out, err = run_cli(["audit-center", "--restore", "1"], work_dir=str(work_dir))
        assert code != 0, f"恢复激活状态的导入应失败，实际code={code}"
        assert contains_any(out, ["无需恢复", "冲突", "active"]), \
            f"恢复激活状态的导入应提示，实际code={code}, out={out[:200]}"
        result.add_detail(f"恢复激活状态的导入正确提示: {out[:100]}")

        conflict = store.check_duplicate_restore(batch_id, store.resolve_import_by_display_index(batch_id, 1).get("import_id"))
        assert conflict.get("has_conflict"), "应检测到冲突"
        assert conflict.get("conflict_type") == "already_active", "冲突类型应为 already_active"
        result.add_detail(f"冲突检测: has_conflict={conflict.get('has_conflict')}, type={conflict.get('conflict_type')}")

        code, out, err = run_cli(["audit-center", "--undo", "1"], work_dir=str(work_dir))
        assert code == 0, f"撤销失败: {err}"
        result.add_detail("撤销序号1成功")

        code, out, err = run_cli(["import", "examples/app.log", "--force"], work_dir=str(work_dir))
        assert code == 0, f"强制重导失败: {err}"
        result.add_detail("强制重新导入成功")

        entries = store.get_all_imports_with_index(batch_id)
        assert len(entries) == 2, f"应有2条导入记录，实际 {len(entries)}"
        result.add_detail(f"现在有 {len(entries)} 条导入记录")

        code, out, err = run_cli(["audit-center", "--restore", "1"], work_dir=str(work_dir))
        assert code != 0, f"恢复已存在相同文件的导入应失败，实际code={code}"
        assert contains_any(out, ["冲突", "duplicate", "conflict"]), \
            f"恢复已存在相同文件的导入应提示，实际code={code}, out={out[:200]}"
        result.add_detail(f"恢复已存在相同文件的导入正确提示: {out[:100]}")

        import_id_1 = store.resolve_import_by_display_index(batch_id, 1).get("import_id")
        conflict2 = store.check_duplicate_restore(batch_id, import_id_1)
        assert conflict2.get("has_conflict"), "应检测到重复文件冲突"
        assert conflict2.get("conflict_type") == "duplicate_file_active", "冲突类型应为 duplicate_file_active"
        result.add_detail(f"冲突检测: has_conflict={conflict2.get('has_conflict')}, type={conflict2.get('conflict_type')}")

        code, out, err = run_cli(["audit-center", "--restore", "1", "--force"], work_dir=str(work_dir))
        assert code == 0, f"强制恢复应成功: {err}"
        assert contains_any(out, ["恢复", "restore"]), "强制恢复应成功"
        result.add_detail("使用 --force 强制恢复成功")

        result.success("重复恢复冲突检测和处理正确")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_empty_export_validation(test_dir: Path, example_dir: Path) -> TestResult:
    result = TestResult("空导出校验失败场景")
    try:
        work_dir = test_dir / "empty_export"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        shutil.copy(example_dir / "app.log", work_dir / "examples" / "app.log")

        run_cli(["create", "--name", "empty-export-test"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()
        real_count = len(store.load_events(batch_id))
        assert real_count > 0, "应有事件"
        result.add_detail(f"实际事件数: {real_count}")

        empty_content = "导出文件内容无事件"
        verify = store.verify_export_consistency(batch_id, empty_content, "markdown")
        assert not verify.get("consistent", True), "空导出应判定不一致"
        assert "empty_export" in verify.get("failed_checks", []), "应有 empty_export 失败项"
        mismatches = [m for m in verify.get("mismatches", []) if m.get("check_type") == "empty_export"]
        assert len(mismatches) > 0, "应包含 empty_export 不一致"
        assert mismatches[0].get("export") == 0, "导出事件数应为0"
        reason = mismatches[0].get("reason", "")
        assert "0" in reason or "空" in reason or "empty" in reason.lower(), f"原因应说明事件数为0，实际: {reason}"
        result.add_detail(f"空导出校验: inconsistent={not verify.get('consistent')}, reason={reason}")

        csv_empty = "id,timestamp,severity,message\n"
        csv_verify = store.verify_export_consistency(batch_id, csv_empty, "csv")
        assert not csv_verify.get("consistent", True), "只有表头的CSV应判定不一致"
        result.add_detail("CSV只有表头时也正确判定不一致")

        empty_file = work_dir / "empty_report.md"
        with open(empty_file, "w", encoding="utf-8") as f:
            f.write(empty_content)

        code, out, err = run_cli(
            ["audit-center", "--verify-export", str(empty_file)],
            work_dir=str(work_dir)
        )
        assert code != 0, f"空导出校验应返回非0退出码，实际code={code}"
        assert contains_any(out, ["核对失败", "不一致", "failed", "inconsistent"]), "应显示核对失败"
        assert contains_any(out, ["empty_export", "事件数为0", "0"]), "应说明空导出原因"
        result.add_detail("CLI 空导出校验正确失败并返回错误码")

        change_log = store.get_change_log(batch_id, limit=10)
        error_logs = [e for e in change_log if e.get("severity") == "error" and "audit" in e.get("change_type", "")]
        assert len(error_logs) >= 1, "应有核对失败的错误日志"
        result.add_detail(f"关键错误日志: {len(error_logs)} 条")

        result.success("空导出校验失败场景正确")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_count_mismatch_validation(test_dir: Path, example_dir: Path) -> TestResult:
    result = TestResult("数量不一致校验失败场景")
    try:
        work_dir = test_dir / "count_mismatch"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        shutil.copy(example_dir / "app.log", work_dir / "examples" / "app.log")

        run_cli(["create", "--name", "count-mismatch-test"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()
        real_count = len([e for e in store.load_events(batch_id) if e.status.value != "噪声"])
        assert real_count > 0, "应有事件"
        result.add_detail(f"实际有效事件数: {real_count}")

        fake_count = real_count + 5
        fake_md = f"# 报告\n\n事件总数: {fake_count}\n"
        verify = store.verify_export_consistency(batch_id, fake_md, "markdown")
        assert not verify.get("consistent", True), "事件数不一致应判定不一致"
        assert "event_count_mismatch" in verify.get("failed_checks", []), "应有 event_count_mismatch 失败项"
        mismatches = [m for m in verify.get("mismatches", []) if m.get("check_type") == "event_count_mismatch"]
        assert len(mismatches) > 0, "应包含数量不一致"
        assert mismatches[0].get("export") == fake_count, f"导出事件数应为{fake_count}"
        assert mismatches[0].get("actual") == real_count, f"实际事件数应为{real_count}"
        diff = mismatches[0].get("diff", 0)
        assert diff == -5, f"差异应为-5，实际 {diff}"
        result.add_detail(f"不一致校验: export={fake_count} actual={real_count} diff={diff}")

        correct_md = f"# 报告\n\n事件总数: {real_count}\n"
        verify_correct = store.verify_export_consistency(batch_id, correct_md, "markdown")
        assert verify_correct.get("consistent", False), "事件数一致应判定一致"
        result.add_detail("正确事件数时判定一致")

        mismatched_file = work_dir / "mismatch_report.md"
        with open(mismatched_file, "w", encoding="utf-8") as f:
            f.write(fake_md)

        code, out, err = run_cli(
            ["audit-center", "--verify-export", str(mismatched_file)],
            work_dir=str(work_dir)
        )
        assert code != 0, f"数量不一致校验应返回非0退出码，实际code={code}"
        assert contains_any(out, ["核对失败", "不一致", "failed", "inconsistent"]), "应显示核对失败"
        assert contains_any(out, ["event_count_mismatch", "数量不一致", "mismatch"]), "应说明数量不一致原因"
        result.add_detail("CLI 数量不一致校验正确失败")

        change_log = store.get_change_log(batch_id, limit=10)
        fail_logs = [e for e in change_log if "audit_count_mismatch_failed" in e.get("change_type", "")]
        assert len(fail_logs) >= 1, "应有数量不匹配的日志"
        result.add_detail(f"数量不匹配日志: {len(fail_logs)} 条")

        result.success("数量不一致校验失败场景正确")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_audit_rules_config(test_dir: Path, example_dir: Path) -> TestResult:
    result = TestResult("核对规则配置化开关")
    try:
        work_dir = test_dir / "rules_config"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        shutil.copy(example_dir / "app.log", work_dir / "examples" / "app.log")

        run_cli(["create", "--name", "rules-config-test"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))

        code, out, err = run_cli(["audit-center", "--show-rules"], work_dir=str(work_dir))
        assert code == 0, f"--show-rules 失败: {err}"
        assert contains_any(out, ["核对规则", "audit", "rules"]), "应显示规则配置"
        assert contains_any(out, ["空导出", "empty_export"]), "应显示空导出检查"
        assert contains_any(out, ["数量不一致", "event_count_mismatch"]), "应显示数量不一致检查"
        result.add_detail("--show-rules 正确显示所有规则")

        code, out, err = run_cli(
            ["audit-center", "--disable-check", "empty_export"],
            work_dir=str(work_dir)
        )
        assert code == 0, f"--disable-check empty_export 失败: {err}"
        result.add_detail("禁用 empty_export 规则成功")

        code, out, err = run_cli(
            ["audit-center", "--set-tolerance", "event_count_mismatch=10"],
            work_dir=str(work_dir)
        )
        assert code == 0, f"--set-tolerance 失败: {err}"
        result.add_detail("设置 event_count_mismatch 容忍度为10成功")

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()
        config = store.load_config(batch_id)
        assert not config.audit_rules.check_empty_export, "empty_export 应已禁用"
        assert config.audit_rules.count_mismatch_tolerance == 10, "容忍度应为10"
        result.add_detail("配置持久化正确")

        empty_content = "导出文件内容无事件"
        verify = store.verify_export_consistency(batch_id, empty_content, "markdown")
        assert verify.get("consistent", False), "禁用空导出检查后应判定一致"
        result.add_detail("禁用空导出检查后，空导出不再判定失败")

        real_count = len([e for e in store.load_events(batch_id) if e.status.value != "噪声"])
        fake_md = f"# 报告\n\n事件总数: {real_count + 8}\n"
        verify2 = store.verify_export_consistency(batch_id, fake_md, "markdown")
        assert verify2.get("consistent", False), "容忍度内的差异应判定一致"
        result.add_detail("容忍度10时，差异8应通过")

        code, out, err = run_cli(
            ["audit-center", "--enable-check", "all"],
            work_dir=str(work_dir)
        )
        assert code == 0, f"--enable-check all 失败: {err}"
        result.add_detail("启用所有规则成功")

        config2 = store.load_config(batch_id)
        assert config2.audit_rules.check_empty_export, "empty_export 应已重新启用"
        result.add_detail("所有规则已重新启用")

        result.success("核对规则配置化开关和容忍度正确")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_success_and_failure_scenarios(test_dir: Path, example_dir: Path) -> TestResult:
    result = TestResult("成功和失败场景真实命令执行")
    try:
        work_dir = test_dir / "scenarios"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        for f in ["app.log", "alerts.csv", "notes.json"]:
            shutil.copy(example_dir / f, work_dir / "examples" / f)

        print("=" * 60)
        print("🎬 场景1: 成功链路 - 导入→列表→详情→撤销→恢复→导出核对")
        print("=" * 60)

        run_cli(["create", "--name", "scenario-test", "--desc", "success-failure-scenarios"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))
        time.sleep(0.3)
        run_cli(["import", "examples/alerts.csv"], work_dir=str(work_dir))

        code, out, err = run_cli(["audit-center", "--list"], work_dir=str(work_dir))
        assert code == 0
        print(out)
        assert contains_any(out, ["app.log", "alerts.csv"])
        result.add_detail("✅ 场景1-列表: 成功")

        code, out, err = run_cli(["audit-center", "--detail", "1"], work_dir=str(work_dir))
        assert code == 0
        print(out)
        assert contains_any(out, ["import_id", "记录标识"]) or "事件统计" in out or "event_stats" in out
        result.add_detail("✅ 场景1-详情: 成功")

        code, out, err = run_cli(["audit-center", "--undo", "1"], work_dir=str(work_dir))
        assert code == 0
        print(out)
        assert contains_any(out, ["撤销", "undo"])
        result.add_detail("✅ 场景1-撤销: 成功")

        code, out, err = run_cli(["audit-center", "--restore", "1"], work_dir=str(work_dir))
        assert code == 0
        print(out)
        assert contains_any(out, ["恢复", "restore"])
        result.add_detail("✅ 场景1-恢复: 成功")

        code, out, err = run_cli(
            ["audit-center", "--export-audit", "-o", "success_report.md"],
            work_dir=str(work_dir)
        )
        assert code == 0
        print(out)
        assert contains_any(out, ["一致", "通过", "consistent", "verified"])
        result.add_detail("✅ 场景1-导出核对: 成功")

        print("\n" + "=" * 60)
        print("🎬 场景2: 失败场景 - 空导出校验")
        print("=" * 60)

        empty_file = work_dir / "empty_fail.md"
        with open(empty_file, "w", encoding="utf-8") as f:
            f.write("无事件的空报告")

        code, out, err = run_cli(
            ["audit-center", "--verify-export", str(empty_file)],
            work_dir=str(work_dir)
        )
        assert code != 0
        print(out)
        print(f"退出码: {code}")
        assert contains_any(out, ["核对失败", "不一致", "failed"])
        result.add_detail(f"✅ 场景2-空导出: 正确失败，退出码={code}")

        print("\n" + "=" * 60)
        print("🎬 场景3: 失败场景 - 数量不一致校验")
        print("=" * 60)

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()
        real_count = len([e for e in store.load_events(batch_id) if e.status.value != "噪声"])
        mismatch_file = work_dir / "mismatch_fail.md"
        with open(mismatch_file, "w", encoding="utf-8") as f:
            f.write(f"事件总数: {real_count + 100}\n")

        code, out, err = run_cli(
            ["audit-center", "--verify-export", str(mismatch_file)],
            work_dir=str(work_dir)
        )
        assert code != 0
        print(out)
        print(f"退出码: {code}")
        assert contains_any(out, ["核对失败", "不一致", "failed"])
        result.add_detail(f"✅ 场景3-数量不一致: 正确失败，退出码={code}")

        print("\n" + "=" * 60)
        print("🎬 场景4: 失败场景 - 重复恢复冲突")
        print("=" * 60)

        code, out, err = run_cli(["audit-center", "--restore", "1"], work_dir=str(work_dir))
        assert code != 0
        print(out)
        print(f"退出码: {code}")
        assert contains_any(out, ["冲突", "无需恢复", "active", "conflict"])
        result.add_detail(f"✅ 场景4-重复恢复: 正确失败，退出码={code}")

        print("\n" + "=" * 60)
        print("🎬 场景5: 跨重启后继续操作")
        print("=" * 60)

        store2 = StateStore(str(work_dir))
        rebuild = store2.rebuild_state_after_restart(batch_id)
        print(f"重启重建: {rebuild.get('actions', [])}")

        code, out, err = run_cli(["import", "examples/notes.json"], work_dir=str(work_dir))
        assert code == 0
        print(out)

        code, out, err = run_cli(["audit-center", "--detail", "3"], work_dir=str(work_dir))
        assert code == 0
        print(out)
        assert contains_any(out, ["notes.json"])
        result.add_detail("✅ 场景5-跨重启后按序号3查看: 成功")

        code, out, err = run_cli(["audit-center", "--undo", "3"], work_dir=str(work_dir))
        assert code == 0
        print(out)
        assert contains_any(out, ["撤销", "undo"])
        result.add_detail("✅ 场景5-跨重启后按序号3撤销: 成功")

        code, out, err = run_cli(["audit-center", "--restore", "3"], work_dir=str(work_dir))
        assert code == 0
        print(out)
        assert contains_any(out, ["恢复", "restore"])
        result.add_detail("✅ 场景5-跨重启后按序号3恢复: 成功")

        print("\n" + "=" * 60)
        print("🎬 场景6: 最终导出核对完整链路")
        print("=" * 60)

        code, out, err = run_cli(
            ["audit-center", "--export-audit", "-o", "final_report.md"],
            work_dir=str(work_dir)
        )
        assert code == 0
        print(out)
        assert contains_any(out, ["一致", "通过", "consistent"])
        result.add_detail("✅ 场景6-最终导出核对: 成功")

        final_check = store2.check_snapshot_consistency(batch_id)
        assert final_check.get("consistent", False), "最终快照应一致"
        result.add_detail("✅ 最终快照一致性检查通过")

        print("\n" + "=" * 60)
        print("🎬 场景7: 核对规则日志检查")
        print("=" * 60)

        change_log = store2.get_change_log(batch_id, limit=200)
        audit_logs = [e for e in change_log if "audit" in e.get("change_type", "")]
        print(f"找到 {len(audit_logs)} 条 audit 相关日志")
        for log in audit_logs[:10]:
            print(f"  [{log.get('severity')}] {log.get('change_type')}: {list(log.get('detail', {}).keys())}")

        success_logs = [e for e in audit_logs if e.get("severity") == "info"]
        error_logs = [e for e in audit_logs if e.get("severity") == "error"]
        warning_logs = [e for e in audit_logs if e.get("severity") == "warning"]
        result.add_detail(f"关键日志: success={len(success_logs)}, warning={len(warning_logs)}, error={len(error_logs)}")
        assert len(error_logs) >= 3, f"至少应有3条错误日志（空导出、数量不一致、重复恢复），实际 {len(error_logs)}"
        assert len(success_logs) >= 3, f"至少应有3条成功日志，实际 {len(success_logs)}"
        result.add_detail("✅ 场景7-关键日志记录完整")

        result.success("所有成功和失败场景的真实命令执行验证通过")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


if __name__ == "__main__":
    run_tests()
