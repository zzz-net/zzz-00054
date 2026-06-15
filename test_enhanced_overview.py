#!/usr/bin/env python3
"""
批次概览增强功能回归测试套件
覆盖场景：
1. 重启后概览持久化恢复
2. 配置变更后摘要刷新与冲突提示
3. 重复导入冲突检测与处理
4. 导出前后对比与变更摘要
5. 快照损坏自动恢复
6. 历史快照记录与对比
7. 快照一致性检查与自动修复
8. 变更日志记录
9. 完整 CLI 命令链路验证
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
    test_dir = Path(tempfile.mkdtemp(prefix="tlr_enhanced_test_"))
    example_dir = Path(__file__).parent / "examples"
    print(f"🧪 测试工作目录: {test_dir}")
    print()

    try:
        results.append(test_persistence_after_restart(test_dir, example_dir))
        results.append(test_config_change_and_refresh(test_dir, example_dir))
        results.append(test_duplicate_import_conflict(test_dir, example_dir))
        results.append(test_export_comparison(test_dir, example_dir))
        results.append(test_snapshot_corruption_recovery(test_dir, example_dir))
        results.append(test_historical_snapshots(test_dir, example_dir))
        results.append(test_change_summary(test_dir, example_dir))
        results.append(test_consistency_check_and_fix(test_dir, example_dir))
        results.append(test_change_log(test_dir, example_dir))
        results.append(test_cli_help_parameters(test_dir))
        results.append(test_full_cli_chain(test_dir, example_dir))
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
    print("\n🎉 所有增强概览测试通过!")


def test_persistence_after_restart(test_dir: Path, example_dir: Path) -> TestResult:
    """测试1: 重启后概览持久化恢复"""
    result = TestResult("重启后概览持久化恢复")
    try:
        work_dir = test_dir / "persist_restart"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        shutil.copy(example_dir / "app.log", work_dir / "examples" / "app.log")
        shutil.copy(example_dir / "alerts.csv", work_dir / "examples" / "alerts.csv")

        run_cli(["create", "--name", "重启持久化测试"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))

        store1 = StateStore(str(work_dir))
        batch_id = store1.get_active_batch()
        snap1 = store1.load_overview_snapshot(batch_id, auto_refresh=False)
        result.add_detail(f"实例1快照: 事件={snap1['event_count']}, 导入={snap1['imported_file_count']}")

        run_cli(["import", "examples/alerts.csv"], work_dir=str(work_dir))
        snap1_after = store1.load_overview_snapshot(batch_id, auto_refresh=False)
        result.add_detail(f"导入后: 事件={snap1_after['event_count']}, 导入={snap1_after['imported_file_count']}")

        store2 = StateStore(str(work_dir))
        snap2 = store2.load_overview_snapshot(batch_id, auto_refresh=False)
        result.add_detail(f"新实例(重启后)快照: 事件={snap2['event_count']}, 导入={snap2['imported_file_count']}")

        assert snap2["event_count"] == snap1_after["event_count"], "事件数不一致"
        assert snap2["imported_file_count"] == snap1_after["imported_file_count"], "导入文件数不一致"
        assert snap2["rule_version"] == snap1_after["rule_version"], "规则版本不一致"

        code, out, err = run_cli(["overview"], work_dir=str(work_dir))
        assert code == 0, f"CLI overview 失败: {err}"
        assert "app.log" in out and "alerts.csv" in out, "CLI 应显示两个导入文件"
        result.add_detail("CLI overview 在新实例中正常工作")

        history = store2.list_historical_snapshots(batch_id, limit=10)
        assert len(history) >= 2, f"应至少有2个历史快照，实际有 {len(history)}"
        result.add_detail(f"历史快照数: {len(history)} (创建、导入、再导入)")

        result.success("重启后所有数据完全一致，历史快照保留")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_config_change_and_refresh(test_dir: Path, example_dir: Path) -> TestResult:
    """测试2: 配置变更后摘要刷新与冲突提示"""
    result = TestResult("配置变更后摘要刷新与冲突提示")
    try:
        work_dir = test_dir / "config_refresh"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        shutil.copy(example_dir / "app.log", work_dir / "examples" / "app.log")

        run_cli(["create", "--name", "配置变更测试"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()
        snap_before = store.load_overview_snapshot(batch_id, auto_refresh=False)
        old_version = snap_before["rule_version"]
        old_dedup = snap_before["dedup_window_seconds"]
        result.add_detail(f"配置前: 版本={old_version}, 去重={old_dedup}s")

        code, out, err = run_cli(
            ["config", "--dedup-window", "600", "--bump-version"],
            work_dir=str(work_dir)
        )
        assert code == 0, f"config 失败: {err}"

        snap_after = store.load_overview_snapshot(batch_id, auto_refresh=False)
        assert snap_after["rule_version"] != old_version, "版本号未升级"
        assert snap_after["dedup_window_seconds"] == 600, "去重窗口未更新"
        result.add_detail(f"配置后: 版本={snap_after['rule_version']}, 去重={snap_after['dedup_window_seconds']}s")

        code, out, err = run_cli(["overview", "--diff", "previous"], work_dir=str(work_dir))
        assert code == 0, f"overview --diff 失败: {err}"
        assert "配置变更" in out or "dedup_window_seconds" in out, "diff 应显示配置变更"
        assert old_version in out, "diff 应显示旧版本"
        assert snap_after["rule_version"] in out, "diff 应显示新版本"
        result.add_detail("变更摘要正确显示配置变更")

        change_log = store.get_change_log(batch_id, limit=10, change_type="config_change")
        assert len(change_log) >= 1, "应有配置变更日志"
        result.add_detail(f"配置变更日志数: {len(change_log)}")

        result.success("配置变更后摘要正确刷新，变更日志记录完整")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_duplicate_import_conflict(test_dir: Path, example_dir: Path) -> TestResult:
    """测试3: 重复导入冲突检测与处理"""
    result = TestResult("重复导入冲突检测与处理")
    try:
        work_dir = test_dir / "dup_import"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        shutil.copy(example_dir / "app.log", work_dir / "examples" / "app.log")

        run_cli(["create", "--name", "重复导入测试"], work_dir=str(work_dir))

        code, out, err = run_cli(["import", "examples/app.log"], work_dir=str(work_dir))
        assert code == 0, f"首次导入失败: {err}"
        result.add_detail("首次导入成功")

        code, out, err = run_cli(["import", "examples/app.log"], work_dir=str(work_dir))
        assert code == 0, f"重复导入不应退出非0: {err}"
        assert "已导入过，跳过" in out or "⏭️" in out, "应提示跳过重复导入"
        assert "文件已导入过，跳过" in out, "应明确提示跳过"
        result.add_detail("重复导入正确提示跳过")

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()
        imported = store.get_imported_files(batch_id)
        assert len(imported) == 1, f"导入索引应只有1条，实际有 {len(imported)}"
        result.add_detail(f"导入索引保持 {len(imported)} 条")

        log_file = work_dir / "examples" / "app.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n2024-01-15 10:30:45 ERROR Appended test error line for duplicate check\n")

        code, out, err = run_cli(["import", "examples/app.log"], work_dir=str(work_dir))
        assert code == 0, f"内容变更后导入不应退出非0: {err}"
        assert "内容已变更" in out or "哈希不匹配" in out, "应检测到内容变更"
        assert "--force" in out, "应提示使用 --force"
        result.add_detail("内容变更后正确提示冲突，建议 --force 重导")

        code, out, err = run_cli(["import", "--force", "examples/app.log"], work_dir=str(work_dir))
        assert code == 0, f"强制重新导入失败: {err}"
        assert "强制重新导入" in out, "应显示强制重新导入"
        result.add_detail("强制重新导入成功")

        dup_check = store.check_duplicate_import(
            batch_id, str(log_file),
            "a1b2c3d4e5f6"
        )
        assert dup_check["is_duplicate"] == True, "应识别为重复"
        assert dup_check["hash_changed"] == True, "应检测到哈希变更"
        assert dup_check["recommendation"] == "force_reimport", "应建议强制重导"
        result.add_detail("API 级重复检测正确")

        change_log = store.get_change_log(batch_id, limit=20, change_type="import_change")
        skipped = [e for e in change_log if e.get("detail", {}).get("action") == "skipped_duplicate"]
        forced = [e for e in change_log if e.get("detail", {}).get("action") == "force_reimport"]
        assert len(skipped) >= 1, "应有跳过重复的日志"
        assert len(forced) >= 1, "应有强制重导的日志"
        result.add_detail(f"变更日志: 跳过={len(skipped)}, 强制重导={len(forced)}")

        result.success("重复导入冲突检测、提示、强制重导全流程正确")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_export_comparison(test_dir: Path, example_dir: Path) -> TestResult:
    """测试4: 导出前后对比与变更摘要"""
    result = TestResult("导出前后对比与变更摘要")
    try:
        work_dir = test_dir / "export_compare"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        shutil.copy(example_dir / "app.log", work_dir / "examples" / "app.log")

        run_cli(["create", "--name", "导出对比测试"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))

        code, out, err = run_cli(
            ["export", "--format", "markdown", "--output", "report1.md", "--save-internal"],
            work_dir=str(work_dir)
        )
        assert code == 0, f"首次导出失败: {err}"
        result.add_detail("首次导出成功")

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()

        code, out, err = run_cli(["timeline", "--limit", "1"], work_dir=str(work_dir))
        event_ids = extract_event_ids(out)
        assert event_ids, "无法获取事件 ID"

        run_cli(
            ["label", "--status", "root", event_ids[0], "--notes", "根因标记"],
            work_dir=str(work_dir)
        )
        result.add_detail("已标注一个事件")

        code, out, err = run_cli(["overview", "--diff", "previous"], work_dir=str(work_dir))
        assert code == 0, f"overview --diff (标注后) 失败: {err}"
        assert "标注变更" in out or "新标注" in out, "变更摘要应显示标注"
        result.add_detail("标注后变更摘要正确显示标注变更")

        code, out, err = run_cli(
            ["export", "--format", "markdown", "--output", "report2.md", "--save-internal"],
            work_dir=str(work_dir)
        )
        assert code == 0, f"二次导出失败: {err}"
        result.add_detail("二次导出成功")

        code, out, err = run_cli(["overview", "--export-diff"], work_dir=str(work_dir))
        assert code == 0, f"overview --export-diff 失败: {err}"
        assert "导出历史与对比" in out, "应显示导出对比标题"
        assert "最近两次导出对比" in out, "应显示两次导出对比"
        assert "report1" in out and "report2" in out, "应显示两个报告文件名"
        result.add_detail("导出对比正确显示两次导出")

        export_comp = store.get_export_comparison(batch_id)
        assert export_comp["comparison"] is not None, "应有对比数据"
        assert len(export_comp["exports"]) >= 2, "应有至少2次导出"
        comp = export_comp["comparison"]
        assert "size_diff" in comp, "对比数据应包含大小差异"
        result.add_detail(f"导出对比: 大小差={comp.get('size_diff')}字节")

        code, out, err = run_cli(["overview", "--diff", "previous"], work_dir=str(work_dir))
        assert code == 0, f"overview --diff (导出后) 失败: {err}"
        assert "新增导出" in out or "导出变更" in out, "变更摘要应显示导出"
        result.add_detail("导出后变更摘要正确显示导出变更")

        result.success("导出对比与变更摘要功能正常")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_snapshot_corruption_recovery(test_dir: Path, example_dir: Path) -> TestResult:
    """测试5: 快照损坏自动恢复"""
    result = TestResult("快照损坏自动恢复")
    try:
        work_dir = test_dir / "snap_corrupt"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        shutil.copy(example_dir / "app.log", work_dir / "examples" / "app.log")

        run_cli(["create", "--name", "快照损坏测试"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()

        snap_path = work_dir / ".timeline_review" / f"batch_{batch_id}" / "overview_snapshot.json"
        assert snap_path.exists(), "快照文件应存在"

        with open(snap_path, "w", encoding="utf-8") as f:
            f.write("{ this is corrupted JSON !!! }")
        result.add_detail("故意损坏快照文件")

        code, out, err = run_cli(["overview"], work_dir=str(work_dir))
        assert code == 0, f"损坏快照后 CLI 不应崩溃: {err}"
        assert "重建" in out or "概览数据加载失败" in out or "批次概览" in out
        result.add_detail("CLI 在损坏快照场景下正常运行并自动恢复")

        snap_after = store.load_overview_snapshot(batch_id, auto_refresh=True)
        assert snap_after["event_count"] > 0, "恢复后应有事件"
        assert snap_after["imported_file_count"] == 1, "恢复后应有导入文件"
        result.add_detail(f"自动恢复后: 事件={snap_after['event_count']}, 导入={snap_after['imported_file_count']}")

        with open(snap_path, "w", encoding="utf-8") as f:
            f.write("")
        result.add_detail("清空快照文件")

        snap_empty = store.load_overview_snapshot(batch_id, auto_refresh=True)
        assert snap_empty["event_count"] > 0, "空快照也应恢复"
        result.add_detail("空快照文件也能正确恢复")

        snapshot_dir = work_dir / ".timeline_review" / f"batch_{batch_id}" / "snapshots_history"
        for f in snapshot_dir.glob("*.json"):
            with open(f, "w", encoding="utf-8") as fh:
                fh.write("corrupted")
        result.add_detail("损坏所有历史快照")

        history = store.list_historical_snapshots(batch_id, limit=10)
        assert len(history) == 0, "损坏的历史快照应被跳过"
        result.add_detail("损坏的历史快照正确跳过，不崩溃")

        change_log = store.get_change_log(batch_id, limit=10)
        code, out, err = run_cli(["overview", "--change-log"], work_dir=str(work_dir))
        assert code == 0, "变更日志在损坏场景下也不应崩溃"
        result.add_detail("变更日志在损坏场景下正常")

        result.success("各种快照损坏场景都能正确处理并自动恢复")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_historical_snapshots(test_dir: Path, example_dir: Path) -> TestResult:
    """测试6: 历史快照记录与对比"""
    result = TestResult("历史快照记录与对比")
    try:
        work_dir = test_dir / "hist_snaps"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        shutil.copy(example_dir / "app.log", work_dir / "examples" / "app.log")
        shutil.copy(example_dir / "alerts.csv", work_dir / "examples" / "alerts.csv")

        run_cli(["create", "--name", "历史快照测试"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))
        time.sleep(0.6)
        run_cli(["import", "examples/alerts.csv"], work_dir=str(work_dir))
        time.sleep(0.6)
        run_cli(["config", "--gap-threshold", "1200", "--bump-version"], work_dir=str(work_dir))

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()

        code, out, err = run_cli(["overview", "--history"], work_dir=str(work_dir))
        assert code == 0, f"overview --history 失败: {err}"
        assert "历史快照记录" in out, "应显示历史快照标题"
        assert "触发原因" in out, "应显示触发原因列"
        assert "创建批次" in out or "导入数据" in out or "配置变更" in out
        result.add_detail("CLI 历史快照列表正确显示")

        history = store.list_historical_snapshots(batch_id, limit=20)
        assert len(history) >= 3, f"应至少有3个历史快照，实际有 {len(history)}"

        triggers = {s["trigger"] for s in history}
        assert "create" in triggers or "import" in triggers or "config" in triggers
        result.add_detail(f"历史快照触发类型: {triggers}")

        first_snap_id = history[-1]["snapshot_id"]
        result.add_detail(f"最早快照 ID: {first_snap_id}")

        first_snap = store.load_historical_snapshot(batch_id, first_snap_id)
        assert first_snap is not None, "应能加载历史快照"
        assert "batch_id" in first_snap, "历史快照应包含 batch_id"
        result.add_detail(f"最早快照事件数: {first_snap.get('event_count', 0)}")

        code, out, err = run_cli(["overview", "--diff", first_snap_id], work_dir=str(work_dir))
        assert code == 0, f"overview --diff <snap_id> 失败: {err}"
        assert "变更摘要" in out, "应显示变更摘要标题"
        assert first_snap_id[:16] in out, "应显示对比的快照 ID"
        result.add_detail("与指定快照对比功能正常")

        code, out, err = run_cli(["overview", "--diff", "first"], work_dir=str(work_dir))
        assert code == 0, f"overview --diff first 失败: {err}"
        assert "变更摘要" in out
        result.add_detail("与最初快照对比功能正常")

        result.success("历史快照记录、列表、对比功能全部正常")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_change_summary(test_dir: Path, example_dir: Path) -> TestResult:
    """测试7: 变更摘要功能"""
    result = TestResult("变更摘要功能")
    try:
        work_dir = test_dir / "change_summary"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        shutil.copy(example_dir / "app.log", work_dir / "examples" / "app.log")
        shutil.copy(example_dir / "alerts.csv", work_dir / "examples" / "alerts.csv")

        run_cli(["create", "--name", "变更摘要测试"], work_dir=str(work_dir))

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()

        summary0 = store.get_change_summary(batch_id, compare_with="previous")
        assert "note" in summary0, "初始状态应有 note"
        assert "初始状态" in summary0.get("note", ""), "应提示初始状态"
        result.add_detail("初始状态提示正确")

        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))
        time.sleep(0.6)

        summary1 = store.get_change_summary(batch_id, compare_with="previous")
        assert summary1.get("diff") is not None, "导入后应有 diff 数据"
        diff1 = summary1["diff"]
        assert "import_changes" in diff1, "diff 应包含导入变更"
        added1 = [c for c in diff1["import_changes"] if c.get("type") == "added"]
        assert len(added1) >= 1, "应显示新增的导入文件"
        assert added1[0]["filename"] == "app.log", "新增文件应为 app.log"
        result.add_detail(f"导入变更: 新增 {added1[0]['filename']} ({added1[0].get('event_count', 0)}事件)")

        run_cli(["import", "examples/alerts.csv"], work_dir=str(work_dir))
        time.sleep(0.6)

        summary2 = store.get_change_summary(batch_id, compare_with="previous")
        assert summary2.get("diff") is not None, "应有 diff 数据"
        diff = summary2["diff"]
        assert "import_changes" in diff, "diff 应包含导入变更"
        added = [c for c in diff["import_changes"] if c.get("type") == "added"]
        assert len(added) >= 1, "应显示新增的导入文件"
        assert added[0]["filename"] == "alerts.csv", "新增文件应为 alerts.csv"
        result.add_detail(f"导入变更: 新增 {added[0]['filename']} ({added[0].get('event_count', 0)}事件)")

        if diff.get("event_count_change"):
            result.add_detail(f"事件数变化: {diff['event_count_change']}")

        code, out, err = run_cli(["overview", "--diff", "previous", "--refresh"], work_dir=str(work_dir))
        assert code == 0, f"overview --diff --refresh 失败: {err}"
        assert "新增导入" in out or "新增:" in out, "CLI 应显示新增导入"
        assert "alerts.csv" in out, "CLI 应显示文件名"
        assert "事件数变化" in out, "CLI 应显示事件数变化"
        result.add_detail("CLI 变更摘要显示正确")

        time.sleep(0.6)
        run_cli(["config", "--dedup-window", "180", "--bump-version"], work_dir=str(work_dir))

        summary3 = store.get_change_summary(batch_id, compare_with="previous")
        diff3 = summary3.get("diff", {})
        assert "config_changes" in diff3, "diff 应包含配置变更"
        config_changes = diff3["config_changes"]
        assert "dedup_window_seconds" in config_changes or "rule_version" in config_changes
        result.add_detail(f"配置变更字段: {list(config_changes.keys())}")

        result.success("变更摘要功能完整，支持导入、配置等各种变更")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_consistency_check_and_fix(test_dir: Path, example_dir: Path) -> TestResult:
    """测试8: 快照一致性检查与自动修复"""
    result = TestResult("快照一致性检查与自动修复")
    try:
        work_dir = test_dir / "consistency"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        shutil.copy(example_dir / "app.log", work_dir / "examples" / "app.log")

        run_cli(["create", "--name", "一致性测试"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()

        check1 = store.check_snapshot_consistency(batch_id)
        assert check1["consistent"] == True, "初始状态应一致"
        result.add_detail("初始状态快照一致")

        code, out, err = run_cli(["overview", "--check-consistency"], work_dir=str(work_dir))
        assert code == 0, f"overview --check-consistency 失败: {err}"
        assert "一致" in out or "✅" in out, "应显示一致"
        result.add_detail("CLI 一致性检查显示一致")

        snap_path = work_dir / ".timeline_review" / f"batch_{batch_id}" / "overview_snapshot.json"
        with open(snap_path, "r", encoding="utf-8") as f:
            snap_data = json.load(f)
        real_events = len(store.load_events(batch_id))
        snap_data["event_count"] = 9999
        snap_data["imported_file_count"] = 99
        snap_data["rule_version"] = "99.99.99"
        with open(snap_path, "w", encoding="utf-8") as f:
            json.dump(snap_data, f)
        result.add_detail("手动篡改快照数据（事件数、导入数、版本号）")

        check2 = store.check_snapshot_consistency(batch_id)
        assert check2["consistent"] == False, "篡改后应不一致"
        incs = check2["inconsistencies"]
        assert len(incs) >= 3, f"应至少检测到3处不一致，实际 {len(incs)}"
        fields = {i["field"] for i in incs}
        assert "event_count" in fields, "应检测到 event_count 不一致"
        assert "imported_file_count" in fields, "应检测到 imported_file_count 不一致"
        assert "rule_version" in fields, "应检测到 rule_version 不一致"
        result.add_detail(f"检测到 {len(incs)} 处不一致: {fields}")

        code, out, err = run_cli(["overview", "--check-consistency"], work_dir=str(work_dir))
        assert code == 0, f"不一致时 CLI 不应崩溃: {err}"
        assert "不一致" in out or "❌" in out, "应显示不一致"
        assert "event_count" in out, "应显示不一致字段"
        assert "--fix" in out, "应提示使用 --fix"
        result.add_detail("CLI 正确显示不一致并提示修复")

        fix_result = store.fix_snapshot_inconsistencies(batch_id)
        assert fix_result["fixed"] == True, "修复应成功"
        assert len(fix_result["inconsistencies_fixed"]) >= 3
        result.add_detail(f"已修复 {len(fix_result['inconsistencies_fixed'])} 处不一致")

        check3 = store.check_snapshot_consistency(batch_id)
        assert check3["consistent"] == True, "修复后应一致"
        assert check3["current_snapshot"]["event_count"] == real_events
        result.add_detail("修复后快照一致，事件数正确")

        code, out, err = run_cli(["overview", "--fix"], work_dir=str(work_dir))
        assert code == 0, f"overview --fix 失败: {err}"
        assert "无需修复" in out or "一致" in out, "一致时应显示无需修复"
        result.add_detail("CLI --fix 在一致时正确提示无需修复")

        repair_logs = store.get_change_log(batch_id, limit=10, change_type="snapshot_repair")
        assert len(repair_logs) >= 1, "应有快照修复日志"
        result.add_detail(f"快照修复日志数: {len(repair_logs)}")

        result.success("一致性检查、修复、日志记录功能完整")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_change_log(test_dir: Path, example_dir: Path) -> TestResult:
    """测试9: 变更日志记录"""
    result = TestResult("变更日志记录")
    try:
        work_dir = test_dir / "change_log"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        shutil.copy(example_dir / "app.log", work_dir / "examples" / "app.log")

        run_cli(["create", "--name", "变更日志测试"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))
        run_cli(["config", "--bump-version"], work_dir=str(work_dir))

        code, out, err = run_cli(
            ["export", "--format", "markdown", "--save-internal", "--output", "test.md"],
            work_dir=str(work_dir)
        )
        assert code == 0

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()

        code, out, err = run_cli(["overview", "--change-log"], work_dir=str(work_dir))
        assert code == 0, f"overview --change-log 失败: {err}"
        assert "变更日志" in out, "应显示变更日志标题"
        assert "导入变更" in out or "配置变更" in out or "导出变更" in out
        result.add_detail("CLI 变更日志正确显示")

        all_logs = store.get_change_log(batch_id, limit=100)
        assert len(all_logs) >= 3, f"应有至少3条变更日志，实际 {len(all_logs)}"
        result.add_detail(f"总变更日志数: {len(all_logs)}")

        import_logs = store.get_change_log(batch_id, limit=10, change_type="import_change")
        assert len(import_logs) >= 1, "应有导入变更日志"
        result.add_detail(f"导入变更日志: {len(import_logs)} 条")

        config_logs = store.get_change_log(batch_id, limit=10, change_type="config_change")
        assert len(config_logs) >= 1, "应有配置变更日志"
        result.add_detail(f"配置变更日志: {len(config_logs)} 条")

        export_logs = store.get_change_log(batch_id, limit=10, change_type="export_change")
        assert len(export_logs) >= 1, "应有导出变更日志"
        result.add_detail(f"导出变更日志: {len(export_logs)} 条")

        for log in all_logs:
            assert "change_type" in log, "每条日志应有 change_type"
            assert "created_at" in log, "每条日志应有 created_at"
            assert "detail" in log, "每条日志应有 detail"
            assert "id" in log, "每条日志应有 id"

        code, out, err = run_cli(
            ["overview", "--change-log", "--log-type", "import_change"],
            work_dir=str(work_dir)
        )
        assert code == 0, f"overview --change-log --log-type 失败: {err}"
        assert "import_change" in out or "导入变更" in out, "应只显示导入变更"
        result.add_detail("按类型过滤变更日志功能正常")

        result.success("变更日志记录完整，支持按类型过滤")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_cli_help_parameters(test_dir: Path) -> TestResult:
    """测试10: CLI 参数帮助信息"""
    result = TestResult("CLI 参数帮助信息")
    try:
        work_dir = test_dir / "cli_help"
        work_dir.mkdir()

        code, out, err = run_cli(["overview", "--help"], work_dir=str(work_dir))
        assert code == 0, f"overview --help 失败: {err}"

        expected_params = [
            "--diff", "--compare-with",
            "--history",
            "--check-consistency",
            "--fix",
            "--change-log",
            "--export-diff",
            "--refresh",
        ]
        for param in expected_params:
            assert param in out, f"帮助信息应包含 {param}"
        result.add_detail("所有新参数都在帮助信息中")

        run_cli(["create", "--name", "帮助测试"], work_dir=str(work_dir))
        code, out, err = run_cli(["overview", "--history-limit", "5"], work_dir=str(work_dir))
        assert code == 0, "--history-limit 参数应被接受"
        result.add_detail("--history-limit 参数有效")

        code, out, err = run_cli(["overview", "--log-limit", "10"], work_dir=str(work_dir))
        assert code == 0, "--log-limit 参数应被接受"
        result.add_detail("--log-limit 参数有效")

        result.success("所有 CLI 新参数帮助和解析都正常")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_full_cli_chain(test_dir: Path, example_dir: Path) -> TestResult:
    """测试11: 完整 CLI 命令链路验证"""
    result = TestResult("完整 CLI 命令链路验证")
    try:
        work_dir = test_dir / "full_chain"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        for f in ["app.log", "alerts.csv", "notes.json"]:
            shutil.copy(example_dir / f, work_dir / "examples" / f)

        steps = [
            ("create", ["create", "--name", "完整链路测试", "--description", "CLI全链路验证"]),
            ("overview初始", ["overview"]),
            ("import1", ["import", "examples/app.log"]),
            ("overview导入1", ["overview"]),
            ("diff1", ["overview", "--diff"]),
            ("import2", ["import", "examples/alerts.csv"]),
            ("history1", ["overview", "--history"]),
            ("diff2", ["overview", "--diff", "previous"]),
            ("config", ["config", "--dedup-window", "180", "--bump-version"]),
            ("diff3", ["overview", "--diff"]),
            ("一致性检查1", ["overview", "--check-consistency"]),
            ("import3", ["import", "examples/notes.json"]),
            ("重复导入", ["import", "examples/app.log"]),
            ("timeline", ["timeline", "--limit", "2"]),
        ]

        batch_id = None
        event_id = None

        for step_name, args in steps:
            code, out, err = run_cli(args, work_dir=str(work_dir))
            assert code == 0, f"步骤 {step_name} 失败: {err}"
            result.add_detail(f"✅ {step_name}: 成功")

            if step_name == "timeline":
                ids = extract_event_ids(out)
                if ids:
                    event_id = ids[0]
                    result.add_detail(f"获取事件ID: {event_id[:12]}...")

        if event_id:
            label_steps = [
                ("label", ["label", "--status", "root", event_id, "--notes", "根因确认"]),
                ("diff4", ["overview", "--diff"]),
                ("export1", ["export", "--format", "markdown", "--save-internal", "--output", "report1.md"]),
                ("diff5", ["overview", "--diff"]),
                ("导出对比", ["overview", "--export-diff"]),
                ("undo-label", ["undo-label"]),
                ("diff6", ["overview", "--diff"]),
                ("export2", ["export", "--format", "markdown", "--save-internal", "--output", "report2.md"]),
                ("导出对比2", ["overview", "--export-diff"]),
                ("undo-import", ["undo-import"]),
                ("diff7", ["overview", "--diff"]),
                ("一致性检查2", ["overview", "--check-consistency"]),
                ("变更日志", ["overview", "--change-log"]),
                ("diff8", ["overview", "--diff", "first"]),
            ]

            for step_name, args in label_steps:
                code, out, err = run_cli(args, work_dir=str(work_dir))
                assert code == 0, f"步骤 {step_name} 失败: {err}"
                result.add_detail(f"✅ {step_name}: 成功")

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()
        final_check = store.check_snapshot_consistency(batch_id)
        assert final_check["consistent"] == True, "最终快照应一致"
        result.add_detail("✅ 最终一致性检查通过")

        history = store.list_historical_snapshots(batch_id, limit=100)
        result.add_detail(f"📜 历史快照总数: {len(history)}")

        logs = store.get_change_log(batch_id, limit=100)
        result.add_detail(f"📝 变更日志总数: {len(logs)}")

        exports = store.get_exports(batch_id)
        result.add_detail(f"📤 导出记录总数: {len(exports)}")

        result.success("完整 CLI 链路所有命令执行成功，数据一致")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


if __name__ == "__main__":
    run_tests()
