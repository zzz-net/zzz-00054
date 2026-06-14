#!/usr/bin/env python3
"""
批次概览功能回归测试套件
覆盖场景：
1. 空批次概览
2. 正常导入链路概览刷新
3. 重启后概览持久化
4. 配置变更后摘要更新
5. 导出后摘要刷新
6. 标注/撤销标注后摘要更新
7. 冲突兜底（文件缺失、状态不一致、损坏快照）
8. 实际命令链路（create/import/config/export/undo）验证一致性
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
    test_dir = Path(tempfile.mkdtemp(prefix="tlr_overview_test_"))
    example_dir = Path(__file__).parent / "examples"
    print(f"🧪 测试工作目录: {test_dir}")
    print()

    try:
        results.append(test_empty_batch_overview(test_dir))
        results.append(test_empty_batch_no_active(test_dir))
        results.append(test_import_updates_overview(test_dir, example_dir))
        results.append(test_overview_persistence_after_restart(test_dir, example_dir))
        results.append(test_config_change_updates_overview(test_dir, example_dir))
        results.append(test_export_updates_overview(test_dir, example_dir))
        results.append(test_label_and_undo_updates_overview(test_dir, example_dir))
        results.append(test_undo_import_updates_overview(test_dir, example_dir))
        results.append(test_corrupted_snapshot_recovery(test_dir, example_dir))
        results.append(test_missing_files_graceful_degradation(test_dir, example_dir))
        results.append(test_cli_full_chain_consistency(test_dir, example_dir))
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
    print("\n🎉 所有批次概览测试通过!")


def test_empty_batch_overview(test_dir: Path) -> TestResult:
    """测试1: 空批次概览 - 刚创建的批次应该有正确的空状态"""
    result = TestResult("空批次概览 - 初始空状态正确")
    try:
        work_dir = test_dir / "empty_batch"
        work_dir.mkdir()

        store = StateStore(str(work_dir))
        meta = store.create_batch("空批次测试", "测试空批次概览")
        batch_id = meta["id"]
        result.add_detail(f"创建批次: {batch_id}")

        snapshot = store.load_overview_snapshot(batch_id, auto_refresh=False)
        result.add_detail("快照持久化: 加载已保存的快照")

        assert snapshot.get("batch_name") == "空批次测试", f"批次名不一致: {snapshot.get('batch_name')}"
        assert snapshot.get("batch_id") == batch_id
        assert snapshot.get("event_count") == 0, f"空批次事件数应为0: {snapshot.get('event_count')}"
        assert snapshot.get("parse_error_count") == 0
        assert snapshot.get("imported_file_count") == 0
        assert snapshot.get("imported_files") == []
        assert snapshot.get("export_count") == 0
        assert snapshot.get("last_export") is None
        assert snapshot.get("last_label_action") is None
        assert snapshot.get("label_action_count") == 0
        assert snapshot.get("rule_version") == "1.0.0"
        assert snapshot.get("dedup_window_seconds") == 300
        assert snapshot.get("gap_threshold_seconds") == 600
        result.add_detail("所有空状态字段正确")

        snapshot_path = work_dir / ".timeline_review" / f"batch_{batch_id}" / "overview_snapshot.json"
        assert snapshot_path.exists(), "快照文件应该被持久化到磁盘"
        result.add_detail(f"快照文件存在: {snapshot_path}")

        result.success("空批次概览所有字段正确，快照已持久化")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_empty_batch_no_active(test_dir: Path) -> TestResult:
    """测试2: 没有活动批次时概览命令不崩溃"""
    result = TestResult("无活动批次时概览命令不崩溃")
    try:
        work_dir = test_dir / "no_active"
        work_dir.mkdir()

        code, out, err = run_cli(["overview"], work_dir=str(work_dir))
        assert code == 0, f"无活动批次时不应退出非0: {code}, err: {err}"
        assert "没有活动批次" in out or "活动批次" in out, f"应该提示没有活动批次: {out[:200]}"
        result.add_detail("无活动批次时给出明确提示，不崩溃")

        result.success("无活动批次场景正确处理")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_import_updates_overview(test_dir: Path, example_dir: Path) -> TestResult:
    """测试3: 导入数据后概览正确刷新"""
    result = TestResult("导入后概览正确刷新")
    try:
        work_dir = test_dir / "import_overview"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        for f in ["app.log", "alerts.csv", "notes.json"]:
            shutil.copy(example_dir / f, work_dir / "examples" / f)

        store = StateStore(str(work_dir))
        meta = store.create_batch("导入概览测试")
        batch_id = meta["id"]

        code, out, err = run_cli(
            ["import", "examples/app.log", "examples/alerts.csv", "examples/notes.json"],
            work_dir=str(work_dir)
        )
        assert code == 0, f"import 失败: {err}"

        snapshot = store.load_overview_snapshot(batch_id, auto_refresh=False)
        result.add_detail(f"导入后事件数: {snapshot.get('event_count')}")
        result.add_detail(f"导入文件数: {snapshot.get('imported_file_count')}")

        assert snapshot.get("event_count") > 0, "导入后应该有事件"
        assert snapshot.get("imported_file_count") == 3, f"应该导入3个文件: {snapshot.get('imported_file_count')}"
        assert snapshot.get("parse_error_count") > 0, "样例数据应该有解析错误"

        imported = snapshot.get("imported_files", [])
        assert len(imported) == 3
        filenames = {f.get("filename") for f in imported}
        assert filenames == {"app.log", "alerts.csv", "notes.json"}, f"文件名不对: {filenames}"

        source_types = {f.get("source_type") for f in imported}
        assert "日志(LOG)" in source_types, f"应该有日志类型: {source_types}"
        assert "告警(CSV)" in source_types, f"应该有告警类型: {source_types}"
        assert "备注(JSON)" in source_types, f"应该有备注类型: {source_types}"

        by_source = snapshot.get("events_by_source", {})
        assert "log" in by_source and by_source["log"] > 0, "应该有日志来源事件"
        assert "alert" in by_source and by_source["alert"] > 0, "应该有告警来源事件"
        assert "note" in by_source and by_source["note"] > 0, "应该有备注来源事件"

        assert snapshot.get("time_range_start") is not None
        assert snapshot.get("time_range_end") is not None
        result.add_detail(f"时间范围: {snapshot.get('time_range_start')} ~ {snapshot.get('time_range_end')}")

        result.success("导入后概览所有字段正确刷新")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_overview_persistence_after_restart(test_dir: Path, example_dir: Path) -> TestResult:
    """测试4: 重启后概览查询结果一致"""
    result = TestResult("重启后概览持久化一致")
    try:
        work_dir = test_dir / "restart_persist"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        shutil.copy(example_dir / "app.log", work_dir / "examples" / "app.log")

        store1 = StateStore(str(work_dir))
        meta = store1.create_batch("重启持久化测试")
        batch_id = meta["id"]

        from timeline_review.importers import LogParser, raw_events_to_events, compute_file_hash
        config = store1.load_config(batch_id)
        parser = LogParser(config)
        raw, errors = parser.parse(str(work_dir / "examples" / "app.log"))
        events = raw_events_to_events(raw, config)
        store1.save_events(batch_id, events)
        store1.save_parse_errors(batch_id, errors)
        fhash = compute_file_hash(str(work_dir / "examples" / "app.log"))
        store1.mark_file_imported(batch_id, str(work_dir / "examples" / "app.log"), fhash, len(events), len(errors))

        snapshot_before = store1.load_overview_snapshot(batch_id, auto_refresh=False)
        result.add_detail(f"重启前快照时间: {snapshot_before.get('updated_at')}")
        result.add_detail(f"重启前事件数: {snapshot_before.get('event_count')}")

        store2 = StateStore(str(work_dir))
        snapshot_after = store2.load_overview_snapshot(batch_id, auto_refresh=False)
        result.add_detail(f"重启后快照时间: {snapshot_after.get('updated_at')}")

        assert snapshot_after.get("batch_id") == batch_id
        assert snapshot_after.get("event_count") == snapshot_before.get("event_count")
        assert snapshot_after.get("imported_file_count") == snapshot_before.get("imported_file_count")
        assert snapshot_after.get("parse_error_count") == snapshot_before.get("parse_error_count")
        assert snapshot_after.get("rule_version") == snapshot_before.get("rule_version")
        result.add_detail("重启后所有关键字段一致")

        code, out, err = run_cli(["overview"], work_dir=str(work_dir))
        assert code == 0, f"重启后 overview 命令失败: {err}"
        assert "批次概览" in out, f"CLI 输出应包含概览标题: {out[:200]}"
        assert "已导入数据" in out
        assert "数据统计" in out
        assert "规则配置" in out
        result.add_detail("重启后 CLI 概览命令正常输出")

        result.success("重启后概览数据完全一致")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_config_change_updates_overview(test_dir: Path, example_dir: Path) -> TestResult:
    """测试5: 配置变更后概览摘要更新"""
    result = TestResult("配置变更后概览更新")
    try:
        work_dir = test_dir / "config_update"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        shutil.copy(example_dir / "app.log", work_dir / "examples" / "app.log")

        run_cli(["create", "--name", "配置变更测试"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()
        snapshot_before = store.load_overview_snapshot(batch_id, auto_refresh=False)
        old_version = snapshot_before.get("rule_version")
        old_dedup = snapshot_before.get("dedup_window_seconds")
        old_gap = snapshot_before.get("gap_threshold_seconds")
        result.add_detail(f"变更前: 版本={old_version}, 去重={old_dedup}s, 缺口={old_gap}s")

        code, out, err = run_cli(
            ["config", "--dedup-window", "600", "--gap-threshold", "1200", "--bump-version"],
            work_dir=str(work_dir)
        )
        assert code == 0, f"config 命令失败: {err}"

        snapshot_after = store.load_overview_snapshot(batch_id, auto_refresh=False)
        new_version = snapshot_after.get("rule_version")
        new_dedup = snapshot_after.get("dedup_window_seconds")
        new_gap = snapshot_after.get("gap_threshold_seconds")
        result.add_detail(f"变更后: 版本={new_version}, 去重={new_dedup}s, 缺口={new_gap}s")

        assert new_version != old_version, f"版本号应该升级: {old_version} -> {new_version}"
        assert new_dedup == 600, f"去重窗口应该更新: {new_dedup}"
        assert new_gap == 1200, f"缺口阈值应该更新: {new_gap}"

        code, out, err = run_cli(["overview"], work_dir=str(work_dir))
        assert "600s" in out or "600" in out, f"CLI 应显示新的去重窗口: {out[:500]}"
        assert "1200s" in out or "1200" in out, f"CLI 应显示新的缺口阈值"
        assert new_version in out, f"CLI 应显示新版本号"
        result.add_detail("CLI 概览正确显示更新后的配置")

        result.success("配置变更后概览正确更新")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_export_updates_overview(test_dir: Path, example_dir: Path) -> TestResult:
    """测试6: 导出后概览摘要刷新"""
    result = TestResult("导出后概览刷新")
    try:
        work_dir = test_dir / "export_refresh"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        shutil.copy(example_dir / "app.log", work_dir / "examples" / "app.log")

        run_cli(["create", "--name", "导出概览测试"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()
        snapshot_before = store.load_overview_snapshot(batch_id, auto_refresh=False)
        assert snapshot_before.get("export_count") == 0
        assert snapshot_before.get("last_export") is None
        result.add_detail("导出前: 无导出记录")

        code, out, err = run_cli(
            ["export", "--format", "markdown", "--output", "test_report.md", "--save-internal"],
            work_dir=str(work_dir)
        )
        assert code == 0, f"export 失败: {err}"

        snapshot_after = store.load_overview_snapshot(batch_id, auto_refresh=False)
        assert snapshot_after.get("export_count") >= 1, f"应该有导出记录: {snapshot_after.get('export_count')}"
        assert snapshot_after.get("last_export") is not None
        last_exp = snapshot_after.get("last_export", {})
        assert "test_report" in last_exp.get("filename", ""), f"导出文件名不对: {last_exp.get('filename')}"
        assert last_exp.get("size", 0) > 0, "导出文件大小应该 > 0"
        assert last_exp.get("exported_at"), "应该有导出时间"
        result.add_detail(f"导出后: 文件={last_exp.get('filename')}, 大小={last_exp.get('size')}字节")

        code, out, err = run_cli(["overview"], work_dir=str(work_dir))
        assert "最近导出" in out
        assert "test_report" in out, f"CLI 应显示导出文件名: {out[:500]}"
        result.add_detail("CLI 概览正确显示最近导出")

        result.success("导出后概览正确刷新")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_label_and_undo_updates_overview(test_dir: Path, example_dir: Path) -> TestResult:
    """测试7: 标注和撤销标注后概览更新"""
    result = TestResult("标注/撤销标注后概览更新")
    try:
        work_dir = test_dir / "label_overview"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        shutil.copy(example_dir / "app.log", work_dir / "examples" / "app.log")

        run_cli(["create", "--name", "标注概览测试"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))

        code, out, err = run_cli(["timeline", "--limit", "1"], work_dir=str(work_dir))
        event_ids = extract_event_ids(out)
        assert event_ids, "无法获取事件 ID"
        event_id = event_ids[0]
        result.add_detail(f"测试事件 ID: {event_id[:12]}...")

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()
        snapshot_before = store.load_overview_snapshot(batch_id, auto_refresh=False)
        assert snapshot_before.get("last_label_action") is None
        assert snapshot_before.get("label_action_count") == 0
        result.add_detail("标注前: 无标注记录")

        code, out, err = run_cli(
            ["label", "--status", "root", event_id, "--notes", "概览测试根因备注"],
            work_dir=str(work_dir)
        )
        assert code == 0, f"label 失败: {err}"

        snapshot_label = store.load_overview_snapshot(batch_id, auto_refresh=False)
        assert snapshot_label.get("label_action_count") == 1
        last_action = snapshot_label.get("last_label_action")
        assert last_action is not None
        assert last_action.get("operation") == "修改状态+备注" or last_action.get("operation") == "set_both"
        assert last_action.get("new_status") == "根因" or last_action.get("new_status") == EventStatus.ROOT_CAUSE.value
        assert "概览测试根因备注" in (last_action.get("new_notes_preview") or "")
        result.add_detail(f"标注后: 操作={last_action.get('operation')}, 新状态={last_action.get('new_status')}")

        code, out, err = run_cli(["overview"], work_dir=str(work_dir))
        assert "最近标注动作" in out
        assert "根因" in out or "修改状态" in out, f"CLI 应显示标注信息: {out[:500]}"
        result.add_detail("CLI 正确显示最近标注动作")

        code, out, err = run_cli(["undo-label"], work_dir=str(work_dir))
        assert code == 0, f"undo-label 失败: {err}"

        snapshot_undo = store.load_overview_snapshot(batch_id, auto_refresh=False)
        assert snapshot_undo.get("label_action_count") == 0
        assert snapshot_undo.get("last_label_action") is None
        result.add_detail("撤销后: 标注记录已清空")

        result.success("标注和撤销标注后概览正确更新")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_undo_import_updates_overview(test_dir: Path, example_dir: Path) -> TestResult:
    """测试8: 撤销导入后概览更新"""
    result = TestResult("撤销导入后概览更新")
    try:
        work_dir = test_dir / "undo_import_overview"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        shutil.copy(example_dir / "app.log", work_dir / "examples" / "app.log")
        shutil.copy(example_dir / "alerts.csv", work_dir / "examples" / "alerts.csv")

        run_cli(["create", "--name", "撤销导入概览测试"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))
        run_cli(["import", "examples/alerts.csv"], work_dir=str(work_dir))

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()
        snapshot_before = store.load_overview_snapshot(batch_id, auto_refresh=False)
        assert snapshot_before.get("imported_file_count") == 2
        result.add_detail(f"撤销前导入文件数: {snapshot_before.get('imported_file_count')}")

        code, out, err = run_cli(["undo-import"], work_dir=str(work_dir))
        assert code == 0, f"undo-import 失败: {err}"

        snapshot_after = store.load_overview_snapshot(batch_id, auto_refresh=False)
        assert snapshot_after.get("imported_file_count") == 1, f"撤销后应该剩1个文件: {snapshot_after.get('imported_file_count')}"
        imported = snapshot_after.get("imported_files", [])
        assert len(imported) == 1
        assert imported[0].get("filename") == "app.log", f"应该剩 app.log: {imported[0].get('filename')}"
        result.add_detail(f"撤销后剩: {imported[0].get('filename')}")

        result.success("撤销导入后概览正确更新")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_corrupted_snapshot_recovery(test_dir: Path, example_dir: Path) -> TestResult:
    """测试9: 损坏的快照文件能自动恢复"""
    result = TestResult("损坏快照自动恢复")
    try:
        work_dir = test_dir / "corrupted_snapshot"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        shutil.copy(example_dir / "app.log", work_dir / "examples" / "app.log")

        run_cli(["create", "--name", "损坏快照测试"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()

        snapshot_path = work_dir / ".timeline_review" / f"batch_{batch_id}" / "overview_snapshot.json"
        with open(snapshot_path, "w", encoding="utf-8") as f:
            f.write("{ this is NOT valid JSON !!! corrupted data }")
        result.add_detail("故意损坏快照文件")

        snapshot = store.load_overview_snapshot(batch_id, auto_refresh=True)
        assert snapshot, "损坏后自动刷新应该返回有效快照"
        assert snapshot.get("event_count") > 0, f"恢复后应该有事件: {snapshot.get('event_count')}"
        assert snapshot.get("imported_file_count") == 1
        assert snapshot.get("rule_version") == "1.0.0"
        result.add_detail(f"自动恢复后事件数: {snapshot.get('event_count')}")

        with open(snapshot_path, "w", encoding="utf-8") as f:
            f.write("")
        result.add_detail("清空快照文件")

        snapshot2 = store.load_overview_snapshot(batch_id, auto_refresh=True)
        assert snapshot2 and snapshot2.get("event_count") > 0
        result.add_detail("空快照文件也能正确恢复")

        code, out, err = run_cli(["overview"], work_dir=str(work_dir))
        assert code == 0, f"损坏快照后 CLI 不应崩溃: {err}"
        assert "批次概览" in out
        result.add_detail("CLI 在损坏快照场景下正常运行")

        result.success("损坏快照场景正确处理并自动恢复")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_missing_files_graceful_degradation(test_dir: Path, example_dir: Path) -> TestResult:
    """测试10: 依赖文件缺失时优雅降级不崩溃"""
    result = TestResult("依赖文件缺失时优雅降级")
    try:
        work_dir = test_dir / "missing_files"
        work_dir.mkdir()
        store = StateStore(str(work_dir))
        meta = store.create_batch("缺失文件测试")
        batch_id = meta["id"]
        batch_dir = work_dir / ".timeline_review" / f"batch_{batch_id}"

        events_path = batch_dir / "events.json"
        if events_path.exists():
            events_path.unlink()
        errors_path = batch_dir / "parse_errors.json"
        if errors_path.exists():
            errors_path.unlink()
        imports_path = batch_dir / "imports_index.json"
        if imports_path.exists():
            imports_path.unlink()
        config_path = batch_dir / "rules_config.json"
        if config_path.exists():
            config_path.unlink()
        history_path = batch_dir / "label_history.json"
        if history_path.exists():
            history_path.unlink()
        exports_dir = batch_dir / "exports"
        if exports_dir.exists():
            shutil.rmtree(exports_dir)
        result.add_detail("删除所有依赖文件")

        snapshot = store.refresh_overview_snapshot(batch_id)
        assert snapshot, "所有文件缺失时仍应返回快照"
        assert snapshot.get("event_count") == 0
        assert snapshot.get("parse_error_count") == 0
        assert snapshot.get("imported_file_count") == 0
        assert snapshot.get("label_action_count") == 0
        assert snapshot.get("export_count") == 0
        result.add_detail("所有字段都有合理的默认值")

        meta_path = batch_dir / "batch_meta.json"
        if meta_path.exists():
            meta_path.unlink()
        result.add_detail("删除批次元数据文件")

        snapshot2 = store.refresh_overview_snapshot(batch_id)
        assert snapshot2, "元数据缺失时仍应返回快照"
        assert "_meta_error" in snapshot2 or snapshot2.get("batch_name") == "批次元数据缺失"
        result.add_detail("元数据缺失时有错误标识且不崩溃")

        result.success("所有文件缺失场景优雅降级，不崩溃")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_cli_full_chain_consistency(test_dir: Path, example_dir: Path) -> TestResult:
    """测试11: 完整 CLI 链路概览与真实状态一致"""
    result = TestResult("完整 CLI 链路概览与真实状态一致")
    try:
        work_dir = test_dir / "full_chain"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        for f in ["app.log", "alerts.csv", "notes.json"]:
            shutil.copy(example_dir / f, work_dir / "examples" / f)

        code, out, err = run_cli(
            ["create", "--name", "完整链路验证批次", "--description", "概览全链路一致性测试"],
            work_dir=str(work_dir)
        )
        assert code == 0, f"create 失败: {err}"
        result.add_detail("✅ create 成功")

        code, out, err = run_cli(["overview"], work_dir=str(work_dir))
        assert code == 0, f"overview (create后) 失败: {err}"
        assert "完整链路验证批次" in out
        assert "暂无已导入文件" in out
        assert "暂无标注记录" in out
        assert "暂无导出记录" in out
        assert "1.0.0" in out
        result.add_detail("✅ create 后概览正确")

        code, out, err = run_cli(
            ["import", "examples/app.log", "examples/alerts.csv"],
            work_dir=str(work_dir)
        )
        assert code == 0, f"import (前2个文件) 失败: {err}"
        result.add_detail("✅ import (前2个) 成功")

        code, out, err = run_cli(["overview"], work_dir=str(work_dir))
        assert code == 0, f"overview (import后) 失败: {err}"
        assert "app.log" in out
        assert "alerts.csv" in out
        assert "日志(LOG)" in out
        assert "告警(CSV)" in out
        assert "事件总数" in out
        result.add_detail("✅ import 后概览正确显示2个文件")

        code, out, err = run_cli(
            ["import", "examples/notes.json"],
            work_dir=str(work_dir)
        )
        assert code == 0, f"import (第3个) 失败: {err}"
        result.add_detail("✅ import (第3个) 成功")

        code, out, err = run_cli(
            ["config", "--dedup-window", "180", "--gap-threshold", "900", "--bump-version"],
            work_dir=str(work_dir)
        )
        assert code == 0, f"config 失败: {err}"
        result.add_detail("✅ config 成功")

        code, out, err = run_cli(["overview"], work_dir=str(work_dir))
        assert code == 0, f"overview (config后) 失败: {err}"
        assert "180s" in out or "180" in out
        assert "900s" in out or "900" in out
        assert "1.0.1" in out
        assert "备注(JSON)" in out
        result.add_detail("✅ config 后概览正确更新")

        code, out, err = run_cli(["timeline", "--limit", "2"], work_dir=str(work_dir))
        event_ids = extract_event_ids(out)
        assert len(event_ids) >= 2
        eid1, eid2 = event_ids[0], event_ids[1]

        code, out, err = run_cli(
            ["label", "--status", "root", eid1, "--notes", "根因事件确认"],
            work_dir=str(work_dir)
        )
        assert code == 0, f"label 失败: {err}"
        result.add_detail("✅ label 成功")

        code, out, err = run_cli(["overview"], work_dir=str(work_dir))
        assert code == 0, f"overview (label后) 失败: {err}"
        assert "修改状态+备注" in out or "根因" in out
        assert "根因事件确认" in out
        result.add_detail("✅ label 后概览显示最近标注")

        code, out, err = run_cli(
            ["export", "--format", "markdown", "--output", "chain_report.md", "--save-internal"],
            work_dir=str(work_dir)
        )
        assert code == 0, f"export 失败: {err}"
        result.add_detail("✅ export 成功")

        code, out, err = run_cli(["overview"], work_dir=str(work_dir))
        assert code == 0, f"overview (export后) 失败: {err}"
        assert "chain_report" in out
        assert "字节" in out
        result.add_detail("✅ export 后概览显示最近导出")

        code, out, err = run_cli(["undo-label"], work_dir=str(work_dir))
        assert code == 0, f"undo-label 失败: {err}"
        result.add_detail("✅ undo-label 成功")

        code, out, err = run_cli(["overview"], work_dir=str(work_dir))
        assert code == 0, f"overview (undo-label后) 失败: {err}"
        assert "最近记录已被撤销" in out or "暂无标注记录" in out
        result.add_detail("✅ undo-label 后概览清空标注记录")

        code, out, err = run_cli(["undo-import"], work_dir=str(work_dir))
        assert code == 0, f"undo-import 失败: {err}"
        result.add_detail("✅ undo-import 成功")

        code, out, err = run_cli(["overview"], work_dir=str(work_dir))
        assert code == 0, f"overview (undo-import后) 失败: {err}"
        assert "notes.json" not in out, f"撤销后不应再显示 notes.json: {out[:500]}"
        result.add_detail("✅ undo-import 后概览移除已撤销文件")

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()
        snapshot = store.load_overview_snapshot(batch_id, auto_refresh=False)
        real_events = len(store.load_events(batch_id))
        real_imports = len(store.get_imported_files(batch_id))
        real_errors = len(store.load_parse_errors(batch_id))
        config = store.load_config(batch_id)
        exports = store.get_exports(batch_id)
        history = store.get_label_history(batch_id)

        assert snapshot.get("event_count") == real_events, f"概览事件数 {snapshot.get('event_count')} != 真实 {real_events}"
        assert snapshot.get("imported_file_count") == real_imports, f"概览导入文件数 {snapshot.get('imported_file_count')} != 真实 {real_imports}"
        assert snapshot.get("parse_error_count") == real_errors, f"概览错误数 {snapshot.get('parse_error_count')} != 真实 {real_errors}"
        assert snapshot.get("rule_version") == config.rule_version, f"概览版本 {snapshot.get('rule_version')} != 真实 {config.rule_version}"
        assert snapshot.get("dedup_window_seconds") == config.dedup_window_seconds
        assert snapshot.get("gap_threshold_seconds") == config.gap_threshold_seconds
        assert snapshot.get("export_count") == len(exports), f"概览导出数 {snapshot.get('export_count')} != 真实 {len(exports)}"
        assert snapshot.get("label_action_count") == len(history), f"概览标注数 {snapshot.get('label_action_count')} != 真实 {len(history)}"

        result.add_detail(f"✅ 最终校验: 事件={real_events}, 导入={real_imports}, 错误={real_errors}")
        result.add_detail(f"✅ 规则版本={config.rule_version}, 导出={len(exports)}, 标注历史={len(history)}")

        result.success("完整 CLI 链路概览与真实状态完全一致")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


if __name__ == "__main__":
    run_tests()
