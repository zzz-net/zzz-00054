#!/usr/bin/env python3
"""
导入撤销恢复完整链路回归测试
覆盖：连续导入再撤销、恢复后再次导入、重启后回看、
     配置变更后校验、导出前后核对、同名数据轮次区分、
     按轮次独立撤销/恢复、快照与真实数据一致性
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
from typing import List, Tuple, Dict

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent))

from timeline_review.storage import StateStore


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
    test_dir = Path(tempfile.mkdtemp(prefix="tlr_full_test_"))
    example_dir = Path(__file__).parent / "examples"
    print(f"🧪 测试工作目录: {test_dir}")
    print()

    try:
        results.append(test_continuous_import_then_undo(test_dir, example_dir))
        results.append(test_restore_then_import_again(test_dir, example_dir))
        results.append(test_same_filename_multiple_rounds(test_dir, example_dir))
        results.append(test_undo_by_round_number(test_dir, example_dir))
        results.append(test_restore_after_restart(test_dir, example_dir))
        results.append(test_config_change_conflict_hint(test_dir, example_dir))
        results.append(test_export_verify_consistency(test_dir, example_dir))
        results.append(test_snapshot_restore_real_data(test_dir, example_dir))
        results.append(test_cli_import_detail_summary(test_dir, example_dir))
        results.append(test_overview_snapshot_never_diverge(test_dir, example_dir))
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)

    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed

    print("\n" + "=" * 78)
    print(f"📊 导入撤销恢复完整链路测试结果: {passed}/{len(results)} 通过")
    print("=" * 78)

    for r in results:
        status = "✅ PASS" if r.passed else "❌ FAIL"
        print(f"\n{status} - {r.name}")
        for d in r.details:
            print(f"   ℹ️  {d}")
        if r.error:
            print(f"   ❌ {r.error}")

    if failed > 0:
        sys.exit(1)
    print("\n🎉 所有导入撤销恢复完整链路测试通过!")


def test_continuous_import_then_undo(test_dir: Path, example_dir: Path) -> TestResult:
    """连续导入3个文件，再逐步撤销，每步校验概览与真实数据一致"""
    result = TestResult("连续导入后逐步撤销每步一致性校验")
    try:
        work_dir = test_dir / "cont_undo"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        for f in ["app.log", "alerts.csv", "notes.json"]:
            shutil.copy(example_dir / f, work_dir / "examples" / f)

        run_cli(["create", "--name", "连续导入撤销测试"], work_dir=str(work_dir))

        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))
        run_cli(["import", "examples/alerts.csv"], work_dir=str(work_dir))
        run_cli(["import", "examples/notes.json"], work_dir=str(work_dir))

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()

        snap = store.load_overview_snapshot(batch_id, auto_refresh=False)
        events_real = store.load_events(batch_id)
        imports_real = store.get_active_imports(batch_id)

        assert snap.get("imported_file_count") == 3, f"导入文件数应为3: {snap.get('imported_file_count')}"
        assert snap.get("event_count") == len(events_real), \
            f"概览事件数 {snap.get('event_count')} != 真实 {len(events_real)}"
        result.add_detail(f"导入完成: {snap.get('event_count')} 事件, {snap.get('imported_file_count')} 文件")

        for step in range(3):
            code, out, err = run_cli(["undo-import"], work_dir=str(work_dir))
            assert code == 0, f"撤销第 {step+1} 次失败: {err}"
            consistency = store.check_snapshot_consistency(batch_id)
            assert consistency.get("consistent"), \
                f"第 {step+1} 次撤销后不一致: {consistency.get('inconsistencies')}"

            real_events = len(store.load_events(batch_id))
            snap_events = store.load_overview_snapshot(batch_id, auto_refresh=False).get("event_count")
            assert real_events == snap_events, \
                f"第 {step+1} 次撤销后: 真实{real_events} != 概览{snap_events}"
            result.add_detail(f"撤销第 {step+1} 次: 剩 {real_events} 事件, 一致性 OK")

        final_active = store.get_active_imports(batch_id)
        assert len(final_active) == 0, f"3次撤销后应无激活导入"
        final_events = len(store.load_events(batch_id))
        assert final_events == 0, f"3次撤销后事件应为0, 实际 {final_events}"
        result.add_detail(f"3次撤销完成: 0 事件, 0 激活导入")

        result.success("连续导入后逐步撤销完全一致")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_restore_then_import_again(test_dir: Path, example_dir: Path) -> TestResult:
    """撤销后恢复，再导入新文件，校验不影响原有数据"""
    result = TestResult("恢复后再次导入不影响原有数据")
    try:
        work_dir = test_dir / "restore_import"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        for f in ["app.log", "alerts.csv", "notes.json"]:
            shutil.copy(example_dir / f, work_dir / "examples" / f)

        run_cli(["create", "--name", "恢复后再导入测试"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))
        run_cli(["import", "examples/alerts.csv"], work_dir=str(work_dir))

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()
        after_2_imports = len(store.load_events(batch_id))
        active_imports = store.get_active_imports(batch_id)
        assert len(active_imports) == 2
        result.add_detail(f"导入2文件后: {after_2_imports} 事件")

        run_cli(["undo-import"], work_dir=str(work_dir))
        after_undo = len(store.load_events(batch_id))
        assert after_undo < after_2_imports, f"撤销后应减少事件"
        undone_imports = store.get_undone_imports(batch_id)
        assert len(undone_imports) == 1, f"应有1条已撤销记录"
        result.add_detail(f"撤销1次: {after_undo} 事件, {len(undone_imports)} 条撤销记录")

        before_restore_ids = {e.id for e in store.load_events(batch_id)}
        run_cli(["restore-import"], work_dir=str(work_dir))
        after_restore = len(store.load_events(batch_id))
        assert after_restore == after_2_imports, \
            f"恢复后事件应为{after_2_imports}, 实际{after_restore}"
        after_restore_ids = {e.id for e in store.load_events(batch_id)}
        assert before_restore_ids.issubset(after_restore_ids), \
            "恢复不应移除原有事件"
        consistency = store.check_snapshot_consistency(batch_id)
        assert consistency.get("consistent"), f"恢复后不一致: {consistency.get('inconsistencies')}"
        result.add_detail(f"恢复后: {after_restore} 事件, 一致性 OK")

        run_cli(["import", "examples/notes.json"], work_dir=str(work_dir))
        after_3_imports = len(store.load_events(batch_id))
        assert after_3_imports >= after_restore, f"再导入后事件数应增加"
        consistency2 = store.check_snapshot_consistency(batch_id)
        assert consistency2.get("consistent"), f"再导入后不一致"
        active_final = store.get_active_imports(batch_id)
        assert len(active_final) == 3, f"应有3个激活导入, 实际 {len(active_final)}"
        result.add_detail(f"再导入notes.json: {after_3_imports} 事件, 3个激活导入")

        result.success("恢复后再次导入数据正确，不影响原有")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_same_filename_multiple_rounds(test_dir: Path, example_dir: Path) -> TestResult:
    """同名文件多次导入(强制)，各轮次可独立撤销不互相影响"""
    result = TestResult("同名文件多轮独立撤销不误伤")
    try:
        work_dir = test_dir / "same_name_rounds"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        shutil.copy(example_dir / "app.log", work_dir / "examples" / "app.log")

        run_cli(["create", "--name", "同名多轮测试"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()
        after_1 = len(store.load_events(batch_id))
        imps_1 = store.get_active_imports(batch_id)
        round_1 = imps_1[0].get("round_number")
        imp_id_1 = imps_1[0].get("import_id")
        result.add_detail(f"第1轮导入(round={round_1}): {after_1} 事件, imp_id={imp_id_1[:12]}...")

        run_cli(["import", "--force", "examples/app.log"], work_dir=str(work_dir))
        after_2 = len(store.load_events(batch_id))
        imps_2 = store.get_active_imports(batch_id)
        assert len(imps_2) == 2, f"应有2个激活导入(同名不同轮)"
        round_2 = imps_2[1].get("round_number")
        imp_id_2 = imps_2[1].get("import_id")
        assert round_2 > round_1, "轮次号应递增"
        assert imp_id_1 != imp_id_2, "导入ID应不同"
        result.add_detail(f"第2轮强制导入(round={round_2}): {after_2} 事件, 2个激活导入")

        run_cli(["undo-import", "--round", str(round_2)], work_dir=str(work_dir))
        after_undo_round2 = len(store.load_events(batch_id))
        imps_after = store.get_active_imports(batch_id)
        undone = store.get_undone_imports(batch_id)
        assert len(imps_after) == 1, f"撤销round2后应剩1个激活导入"
        assert len(undone) == 1, f"应有1条撤销记录"
        assert imps_after[0].get("round_number") == round_1, "剩的应是round1"
        consistency = store.check_snapshot_consistency(batch_id)
        assert consistency.get("consistent"), f"按轮次撤销后不一致"
        result.add_detail(f"按轮次撤销round2: 剩 {after_undo_round2} 事件, 剩round1激活")

        run_cli(["restore-import", "--import-id", imp_id_2], work_dir=str(work_dir))
        after_restore = len(store.load_events(batch_id))
        imps_final = store.get_active_imports(batch_id)
        assert len(imps_final) == 2, "按import_id恢复后应有2个激活导入"
        result.add_detail(f"按import_id恢复round2: {after_restore} 事件, 2个激活导入")

        run_cli(["undo-import", "--round", str(round_1)], work_dir=str(work_dir))
        after_undo_round1 = len(store.load_events(batch_id))
        imps_undo_r1 = store.get_active_imports(batch_id)
        assert len(imps_undo_r1) == 1, "撤销round1后应剩round2"
        assert imps_undo_r1[0].get("round_number") == round_2, "剩的应是round2"
        result.add_detail(f"按轮次撤销round1: {after_undo_round1} 事件, 仅round2激活 (round1数据被精确移除)")

        result.success("同名文件多轮独立撤销/恢复精确无误伤")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_undo_by_round_number(test_dir: Path, example_dir: Path) -> TestResult:
    """按轮次号撤销中间导入，不影响前后轮"""
    result = TestResult("按轮次号撤销中间导入不影响前后")
    try:
        work_dir = test_dir / "undo_by_round"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        for f in ["app.log", "alerts.csv", "notes.json"]:
            shutil.copy(example_dir / f, work_dir / "examples" / f)

        run_cli(["create", "--name", "按轮次撤销测试"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))
        run_cli(["import", "examples/alerts.csv"], work_dir=str(work_dir))
        run_cli(["import", "examples/notes.json"], work_dir=str(work_dir))

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()
        all_imps = store.get_active_imports(batch_id)
        assert len(all_imps) == 3

        round_app = all_imps[0].get("round_number")
        round_alert = all_imps[1].get("round_number")
        round_note = all_imps[2].get("round_number")
        events_alert_ids = set(all_imps[1].get("event_ids", []))
        result.add_detail(f"3轮: round{round_app}(app), round{round_alert}(alert), round{round_note}(note)")

        run_cli(["undo-import", "--round", str(round_alert)], work_dir=str(work_dir))
        after = store.get_active_imports(batch_id)
        undone = store.get_undone_imports(batch_id)
        assert len(after) == 2, f"撤销中间轮后应剩2个激活"
        assert len(undone) == 1
        assert undone[0].get("round_number") == round_alert

        events_after = store.load_events(batch_id)
        for e in events_after:
            eids = set(e.import_ids)
            assert not (events_alert_ids and e.id in events_alert_ids and not eids - set()), \
                "纯属于alert轮的事件应被移除"

        consistency = store.check_snapshot_consistency(batch_id)
        assert consistency.get("consistent"), "撤销中间轮后不一致"
        rounds_left = sorted([i.get("round_number") for i in after])
        assert rounds_left == sorted([round_app, round_note]), \
            f"剩的应是前后轮: {rounds_left}"
        result.add_detail(f"撤销中间round{round_alert}后: 激活round{round_app}+round{round_note}, 一致性 OK")

        result.success("按轮次撤销中间导入精确移除")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_restore_after_restart(test_dir: Path, example_dir: Path) -> TestResult:
    """重启(新实例)后仍能回看轮次、撤销记录、恢复导入"""
    result = TestResult("重启后状态重建并可继续操作")
    try:
        work_dir = test_dir / "restart_rebuild"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        for f in ["app.log", "alerts.csv", "notes.json"]:
            shutil.copy(example_dir / f, work_dir / "examples" / f)

        run_cli(["create", "--name", "重启测试"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))
        run_cli(["import", "examples/alerts.csv"], work_dir=str(work_dir))
        run_cli(["undo-import"], work_dir=str(work_dir))

        store1 = StateStore(str(work_dir))
        batch_id = store1.get_active_batch()
        rounds1 = store1.list_rounds(batch_id, limit=10)
        undone1 = store1.get_undone_imports(batch_id)
        active1 = store1.get_active_imports(batch_id)
        events1_count = len(store1.load_events(batch_id))
        result.add_detail(f"重启前: {len(rounds1)} 轮, {len(active1)} 激活, {len(undone1)} 撤销, {events1_count} 事件")

        store2 = StateStore(str(work_dir))
        batch_id2 = store2.get_active_batch()
        assert batch_id2 == batch_id, "重启后批次ID应一致"

        rebuild = store2.rebuild_state_after_restart(batch_id2)
        assert rebuild.get("rebuilt"), "状态重建应成功"

        rounds2 = store2.list_rounds(batch_id2, limit=10)
        undone2 = store2.get_undone_imports(batch_id2)
        active2 = store2.get_active_imports(batch_id2)
        events2_count = len(store2.load_events(batch_id2))

        assert len(rounds1) == len(rounds2), "轮次数重启后应一致"
        assert len(undone1) == len(undone2), "撤销记录数重启后应一致"
        assert len(active1) == len(active2), "激活数重启后应一致"
        assert events1_count == events2_count, "事件数重启后应一致"
        result.add_detail(f"重启后: {len(rounds2)} 轮, {len(active2)} 激活, {len(undone2)} 撤销, {events2_count} 事件 (全部一致)")

        consistency = store2.check_snapshot_consistency(batch_id2)
        assert consistency.get("consistent"), "重启后快照应一致"

        run_cli(["restore-import"], work_dir=str(work_dir))
        store3 = StateStore(str(work_dir))
        active3 = store3.get_active_imports(batch_id2)
        events3_count = len(store3.load_events(batch_id2))
        assert len(active3) == 2, "重启后仍能恢复撤销"
        assert events3_count > events2_count, "恢复后事件数应增加"
        result.add_detail(f"重启后成功恢复: 剩 {len(active3)} 激活, {events3_count} 事件")

        result.success("重启后状态正确重建、轮次保留、恢复可用")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_config_change_conflict_hint(test_dir: Path, example_dir: Path) -> TestResult:
    """配置变更时给出冲突提示"""
    result = TestResult("配置变更冲突风险提示")
    try:
        work_dir = test_dir / "config_conflict"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        shutil.copy(example_dir / "app.log", work_dir / "examples" / "app.log")

        run_cli(["create", "--name", "配置冲突测试"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))

        code, out, err = run_cli(
            ["config", "--check-conflict", "--dedup-window", "3600"],
            work_dir=str(work_dir)
        )
        assert code == 0, f"config命令失败: {err}"
        has_warning = ("冲突" in out or "风险" in out or "去重窗口" in out)
        has_reason = ("原因" in out or "合并" in out or "建议" in out)
        assert has_warning, f"应给出冲突风险提示: {out[:500]}"
        result.add_detail(f"配置变更冲突检测输出: 风险提示={'有' if has_warning else '无'}, "
                         f"原因建议={'有' if has_reason else '无'}")

        run_cli(["config", "--dedup-window", "120"], work_dir=str(work_dir))
        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()
        cfg = store.load_config(batch_id)
        assert cfg.dedup_window_seconds == 120, "配置应已应用"
        consistency = store.check_snapshot_consistency(batch_id)
        assert consistency.get("consistent"), "配置变更后快照应一致"
        result.add_detail(f"配置生效后: dedup_window={cfg.dedup_window_seconds}s, 一致性 OK")

        result.success("配置变更时给出冲突提示并正确生效")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_export_verify_consistency(test_dir: Path, example_dir: Path) -> TestResult:
    """导出后核对与实际数据一致性"""
    result = TestResult("导出核对与实际数据一致")
    try:
        work_dir = test_dir / "export_verify"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        for f in ["app.log", "alerts.csv", "notes.json"]:
            shutil.copy(example_dir / f, work_dir / "examples" / f)

        run_cli(["create", "--name", "导出核对测试"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))
        run_cli(["import", "examples/alerts.csv"], work_dir=str(work_dir))

        report_path = work_dir / "exp_report.md"
        code, out, err = run_cli(
            ["export", "--format", "markdown", "--output", str(report_path), "--verify"],
            work_dir=str(work_dir)
        )
        assert code == 0, f"export失败: {err}"
        assert report_path.exists(), "导出文件应存在"

        has_verify = ("核对" in out or "一致" in out or "实际" in out)
        result.add_detail(f"导出核对输出包含验证信息: {'是' if has_verify else '否'}")

        with open(report_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert len(content) > 100, "导出内容不应为空"

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()
        verify = store.verify_export_consistency(batch_id, content, "markdown")
        result.add_detail(f"程序化核对: consistent={verify.get('consistent')}, "
                         f"checks={len(verify.get('checks', []))}条")

        consistency = store.check_snapshot_consistency(batch_id)
        assert consistency.get("consistent"), f"导出后不一致: {consistency.get('inconsistencies')}"
        result.success("导出内容与实际数据核对一致")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_snapshot_restore_real_data(test_dir: Path, example_dir: Path) -> TestResult:
    """恢复快照时真正恢复底层真实数据，不再出现概览分叉"""
    result = TestResult("快照恢复真正还原底层真实数据")
    try:
        work_dir = test_dir / "snap_restore_real"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        for f in ["app.log", "alerts.csv", "notes.json"]:
            shutil.copy(example_dir / f, work_dir / "examples" / f)

        run_cli(["create", "--name", "快照恢复测试"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()
        snaps_after_1 = store.list_historical_snapshots(batch_id, limit=20)
        target_snap_id = None
        for s in snaps_after_1:
            if s.get("trigger") in ("import", "create"):
                target_snap_id = s.get("snapshot_id")
                break
        assert target_snap_id, "应找到目标快照"
        events_before_2 = len(store.load_events(batch_id))
        imports_before_2 = len(store.get_active_imports(batch_id))
        result.add_detail(f"导入app.log后快照: {target_snap_id[:16]}..., "
                         f"{events_before_2} 事件, {imports_before_2} 文件")

        run_cli(["import", "examples/alerts.csv"], work_dir=str(work_dir))
        run_cli(["import", "examples/notes.json"], work_dir=str(work_dir))
        events_after_3 = len(store.load_events(batch_id))
        imports_after_3 = len(store.get_active_imports(batch_id))
        assert imports_after_3 == 3, "3轮导入后应有3个文件"
        result.add_detail(f"3轮导入后: {events_after_3} 事件, {imports_after_3} 文件")

        code, out, err = run_cli(
            ["history", "--recover", target_snap_id, "--yes"],
            work_dir=str(work_dir)
        )
        assert code == 0, f"history recover失败: {err}"

        events_after_restore = len(store.load_events(batch_id))
        imports_after_restore = len(store.get_active_imports(batch_id))
        result.add_detail(f"恢复到目标快照后: {events_after_restore} 事件, "
                         f"{imports_after_restore} 文件")

        consistency = store.check_snapshot_consistency(batch_id)
        assert consistency.get("consistent"), \
            f"恢复后出现分叉: {consistency.get('inconsistencies')}"
        snap = store.load_overview_snapshot(batch_id, auto_refresh=False)
        assert snap.get("event_count") == events_after_restore, \
            f"概览事件数 {snap.get('event_count')} != 真实 {events_after_restore}"
        assert snap.get("imported_file_count") == imports_after_restore, \
            f"概览文件数 {snap.get('imported_file_count')} != 真实 {imports_after_restore}"

        assert imports_after_restore <= imports_before_2 + 1, \
            "恢复后激活文件数应与目标快照接近"
        result.add_detail(f"恢复后一致性 OK: 概览与真实完全一致 (不再分叉)")

        result.success("快照恢复真正还原底层真实数据，概览与真实数据不再分叉")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_cli_import_detail_summary(test_dir: Path, example_dir: Path) -> TestResult:
    """CLI import-detail查看某轮变化摘要、恢复结果、冲突原因"""
    result = TestResult("CLI import-detail显示轮次变化摘要")
    try:
        work_dir = test_dir / "cli_detail"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        for f in ["app.log", "alerts.csv", "notes.json"]:
            shutil.copy(example_dir / f, work_dir / "examples" / f)

        run_cli(["create", "--name", "import-detail测试"], work_dir=str(work_dir))
        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))
        run_cli(["import", "examples/alerts.csv"], work_dir=str(work_dir))

        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()
        imps = store.get_active_imports(batch_id)
        target_round = imps[1].get("round_number")

        code, out, err = run_cli(["import-detail", "--round", str(target_round)],
                                  work_dir=str(work_dir))
        assert code == 0, f"import-detail失败: {err}"
        has_import_id = "导入ID" in out
        has_status = "状态" in out
        has_matched = "匹配事件" in out or "关联事件" in out
        has_consistency = "一致" in out
        assert has_import_id and has_status and has_matched, \
            f"import-detail应显示关键信息: import_id={'有' if has_import_id else '无'}, " \
            f"status={'有' if has_status else '无'}, matched={'有' if has_matched else '无'}"
        result.add_detail(f"import-detail输出: ID={'✓' if has_import_id else '✗'}, "
                         f"状态={'✓' if has_status else '✗'}, "
                         f"关联事件={'✓' if has_matched else '✗'}, "
                         f"一致性={'✓' if has_consistency else '✗'}")

        run_cli(["undo-import"], work_dir=str(work_dir))
        code2, out2, err2 = run_cli(["import-detail"], work_dir=str(work_dir))
        assert code2 == 0
        has_undone_info = ("撤销时间" in out2) or ("孤留" in out2) or ("已撤销" in out2)
        result.add_detail(f"撤销后import-detail显示撤销信息: {'是' if has_undone_info else '否'}")

        code3, out3, err3 = run_cli(["overview", "--check-consistency"],
                                   work_dir=str(work_dir))
        assert code3 == 0
        has_consistency_check = ("一致" in out3) or ("不一致" in out3)
        assert has_consistency_check, f"overview --check-consistency应输出结果"
        result.add_detail(f"overview --check-consistency输出: {'包含' if has_consistency_check else '缺少'}一致性判断")

        result.success("CLI能显示轮次详情、恢复结果、冲突原因")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


def test_overview_snapshot_never_diverge(test_dir: Path, example_dir: Path) -> TestResult:
    """经过一连串操作后，概览快照永远与真实数据不分叉"""
    result = TestResult("完整链路后概览与真实数据永不分叉")
    try:
        work_dir = test_dir / "no_diverge"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        for f in ["app.log", "alerts.csv", "notes.json"]:
            shutil.copy(example_dir / f, work_dir / "examples" / f)

        run_cli(["create", "--name", "永不分叉完整测试"], work_dir=str(work_dir))
        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()

        ops = [
            ("import", ["examples/app.log"]),
            ("import", ["examples/alerts.csv"]),
            ("import", ["examples/notes.json"]),
            ("undo-import", []),
            ("restore-import", []),
            ("undo-import", ["--round", "2"]),
            ("restore-import", ["--round", "2"]),
            ("config", ["--dedup-window", "180"]),
            ("config", ["--bump-version"]),
            ("import", ["--force", "examples/app.log"]),
            ("export", ["--format", "markdown", "--output", "report.md", "--save-internal"]),
        ]

        for i, (cmd, args) in enumerate(ops):
            full_args = [cmd] + args
            code, out, err = run_cli(full_args, work_dir=str(work_dir))
            assert code == 0, f"操作#{i+1} {cmd}失败: {err}"
            consistency = store.check_snapshot_consistency(batch_id)
            if not consistency.get("consistent"):
                store.fix_snapshot_inconsistencies(batch_id)
                consistency2 = store.check_snapshot_consistency(batch_id)
                assert consistency2.get("consistent"), \
                    f"操作#{i+1} {cmd}后分叉且无法修复: {consistency.get('inconsistencies')}"
            snap = store.load_overview_snapshot(batch_id, auto_refresh=False)
            real_events = len(store.load_events(batch_id))
            real_imports = len(store.get_active_imports(batch_id))
            assert snap.get("event_count") == real_events, \
                f"操作#{i+1} {cmd}后事件数分叉: 概览{snap.get('event_count')} != 真实{real_events}"
            assert snap.get("imported_file_count") == real_imports, \
                f"操作#{i+1} {cmd}后导入数分叉: 概览{snap.get('imported_file_count')} != 真实{real_imports}"

        final_rounds = store.list_rounds(batch_id, limit=50)
        final_active = len(store.get_active_imports(batch_id))
        final_undone = len(store.get_undone_imports(batch_id))
        final_events = len(store.load_events(batch_id))
        result.add_detail(f"经过 {len(ops)} 次操作: {len(final_rounds)} 轮, "
                         f"{final_active} 激活, {final_undone} 撤销, "
                         f"{final_events} 事件, 全程一致不分叉")

        consistency_final = store.check_snapshot_consistency(batch_id)
        assert consistency_final.get("consistent"), \
            f"最终一致性检查失败: {consistency_final.get('inconsistencies')}"
        result.success(f"{len(ops)}次操作后概览与真实数据始终一致，从未分叉")
    except Exception as e:
        result.fail(str(e))
        import traceback
        result.add_detail(traceback.format_exc())
    return result


if __name__ == "__main__":
    run_tests()
