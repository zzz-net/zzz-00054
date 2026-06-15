#!/usr/bin/env python3
"""
导入历史增强功能 - 完整测试套件
测试覆盖:
1. 连续快速导入（无sleep）
2. 跨重启回看
3. 配置切换后刷新
4. 导出前后核对
5. 损坏快照恢复
6. 冲突检测与恢复
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

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).parent.resolve()
EXAMPLES_DIR = REPO_ROOT / "examples"


def run_cli(args, work_dir, input_data=None):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    cli_args = [sys.executable, "-m", "timeline_review"] + args
    stdin = subprocess.PIPE if input_data else None
    result = subprocess.run(
        cli_args,
        cwd=str(work_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        input=input_data,
        stdin=stdin,
    )
    return result.returncode, result.stdout, result.stderr


def print_section(title):
    print()
    print("=" * 80)
    print(f"🔬 {title}")
    print("=" * 80)


def assert_true(condition, message="断言失败"):
    if not condition:
        raise AssertionError(message)
    print(f"✅ {message}")


def assert_equal(actual, expected, message="值不相等"):
    if actual != expected:
        raise AssertionError(f"{message}: expected={expected}, actual={actual}")
    print(f"✅ {message}")


class TestSetup:
    def __init__(self):
        self.demo_dir = Path(tempfile.mkdtemp(prefix="tlr_import_history_test_"))
        self.work_dir = self.demo_dir / "workspace"
        self.work_dir.mkdir()
        (self.work_dir / "examples").mkdir()
        for f in ["app.log", "alerts.csv", "notes.json"]:
            shutil.copy(EXAMPLES_DIR / f, self.work_dir / "examples" / f)

    def cleanup(self):
        print(f"\n💾 测试数据保存在: {self.work_dir} (保留以供检查)")


def test_1_rapid_imports_without_sleep(setup):
    """测试1: 连续快速导入，验证每轮都生成独立快照"""
    print_section("测试1: 连续快速导入（无sleep）")

    files_to_import = [
        "examples/app.log",
        "examples/alerts.csv",
        "examples/notes.json",
    ]

    code, out, err = run_cli(
        ["create", "--name", "快速导入测试批次", "--description", "测试连续快速导入"],
        setup.work_dir
    )
    assert_equal(code, 0, "创建批次成功")

    for i, file_path in enumerate(files_to_import, 1):
        code, out, err = run_cli(["import", file_path], setup.work_dir)
        assert_equal(code, 0, f"第{i}次导入 {file_path} 成功")
        print(f"   已导入: {file_path}")

    code, out, err = run_cli(["overview", "--rounds"], setup.work_dir)
    assert_equal(code, 0, "获取轮次列表成功")

    round_count = 0
    for line in out.splitlines():
        if line.strip().startswith("1") or line.strip().startswith("2") or line.strip().startswith("3"):
            parts = line.split()
            if len(parts) >= 2 and parts[0].isdigit():
                round_count += 1

    assert_true(round_count >= 3, f"至少生成3轮独立快照，实际: {round_count}")

    import_rounds = [1, 2, 3]
    for r in import_rounds:
        code, out, err = run_cli(["overview", "--round", str(r)], setup.work_dir)
        assert_equal(code, 0, f"查看第{r}轮详情成功")
        assert_true("轮次详情" in out, f"第{r}轮详情包含标题")
        print(f"   第{r}轮详情查看成功")

    print()
    print("🎉 测试1通过: 连续快速导入每轮都生成独立快照，无需sleep")


def test_2_cross_restart_review(setup):
    """测试2: 跨重启回看，验证重启后仍能查看历史轮次"""
    print_section("测试2: 跨重启回看")

    code, out, err = run_cli(["overview", "--rounds"], setup.work_dir)
    assert_equal(code, 0, "重启前获取轮次列表成功")
    rounds_before = []
    for line in out.splitlines():
        parts = line.split()
        if parts and parts[0].isdigit() and len(parts) >= 2:
            try:
                round_num = int(parts[0])
                if 1 <= round_num <= 100:
                    rounds_before.append(round_num)
            except ValueError:
                pass

    print(f"   重启前轮次: {rounds_before}")

    code, out, err = run_cli(["overview"], setup.work_dir)
    assert_equal(code, 0, "重启前概览获取成功")

    print("\n🔄 模拟重启 - 创建新的 StateStore 实例")
    from timeline_review.storage import StateStore
    store = StateStore(str(setup.work_dir))
    batch_id = store.get_active_batch()

    rounds_after = store.list_rounds(batch_id, limit=100)
    round_nums_after = [r["round_number"] for r in rounds_after]
    print(f"   重启后轮次: {round_nums_after}")

    assert_equal(len(rounds_after), len(rounds_before), "重启后轮次数量一致")
    assert_equal(round_nums_after, rounds_before, "重启后轮次序号一致")

    for round_num in rounds_before:
        round_info = store.get_round(batch_id, round_num)
        assert_true(round_info is not None, f"重启后可查看第{round_num}轮详情")
        assert_true("before_snapshot" in round_info, f"第{round_num}轮包含前快照")
        assert_true("after_snapshot" in round_info, f"第{round_num}轮包含后快照")

        diff = store.get_round_diff(batch_id, round_num)
        assert_true(diff is not None, f"重启后可查看第{round_num}轮差异")

    final_snapshot = store.load_overview_snapshot(batch_id, auto_refresh=False)
    assert_true(final_snapshot.get("event_count", 0) > 0, "重启后事件数据完整")
    assert_equal(final_snapshot.get("imported_file_count", 0), 3, "重启后导入文件数正确")

    print()
    print("🎉 测试2通过: 跨重启后所有轮次和数据完整保留")


def test_3_config_switch_refresh(setup):
    """测试3: 配置切换后刷新，验证冲突检测"""
    print_section("测试3: 配置切换与冲突检测")

    from timeline_review.storage import StateStore
    store = StateStore(str(setup.work_dir))
    batch_id = store.get_active_batch()

    rounds_before = store.list_rounds(batch_id, limit=100)
    round_count_before = len(rounds_before)

    code, out, err = run_cli(
        ["config", "--dedup-window", "180", "--gap-threshold", "900", "--bump-version"],
        setup.work_dir
    )
    assert_equal(code, 0, "配置变更成功")

    rounds_after = store.list_rounds(batch_id, limit=100)
    round_count_after = len(rounds_after)

    assert_true(round_count_after > round_count_before, "配置变更生成新轮次")
    print(f"   配置变更前轮次: {round_count_before}, 变更后: {round_count_after}")

    config_round = rounds_after[0]
    assert_equal(config_round["trigger"], "config", "新轮次触发原因是配置变更")

    config_round_detail = store.get_round(batch_id, config_round["round_number"])
    diff = config_round_detail.get("diff", {})
    config_changes = diff.get("config_changes", {})

    assert_true("dedup_window_seconds" in config_changes, "检测到去重窗口配置变更")
    assert_true("gap_threshold_seconds" in config_changes, "检测到缺口阈值配置变更")
    assert_true("rule_version" in config_changes, "检测到规则版本变更")
    print(f"   检测到 {len(config_changes)} 项配置变更")

    code, out, err = run_cli(["history", "--check-config"], setup.work_dir)
    assert_equal(code, 0, "配置冲突检查成功")
    assert_true("配置一致" in out or "无冲突" in out, "当前配置无冲突")

    from timeline_review.config import RuleConfig
    new_config = RuleConfig()
    new_config.dedup_window_seconds = 600
    conflict_result = store.check_config_conflict(batch_id, new_config)
    assert_true(conflict_result["has_conflict"], "正确检测到配置冲突")
    print(f"   检测到配置冲突，共 {len(conflict_result['conflicts'])} 项")

    print()
    print("🎉 测试3通过: 配置变更正确生成轮次，冲突检测有效")


def test_4_export_before_after_verification(setup):
    """测试4: 导出前后核对，验证历史摘要包含在导出中"""
    print_section("测试4: 导出前后核对")

    code, out, err = run_cli(
        ["export", "--format", "markdown", "--output", "report_before.md", "--save-internal"],
        setup.work_dir
    )
    assert_equal(code, 0, "第一次导出成功")
    assert_true("已包含" in out and "轮操作历史摘要" in out, "导出包含历史摘要")

    report_path = setup.work_dir / "report_before.md"
    assert_true(report_path.exists(), "导出文件存在")

    with open(report_path, "r", encoding="utf-8") as f:
        content_before = f.read()

    assert_true("操作历史摘要" in content_before, "导出报告包含历史摘要标题")
    assert_true("共执行" in content_before and "轮操作" in content_before, "导出包含轮次统计")

    round_table_lines = [l for l in content_before.splitlines() if "| 轮次 |" in l or "导入数据" in l]
    assert_true(len(round_table_lines) >= 1, "导出包含轮次表格")

    code, out, err = run_cli(["import", "--force", "examples/app.log"], setup.work_dir)
    assert_equal(code, 0, "强制重新导入成功")

    code, out, err = run_cli(
        ["export", "--format", "markdown", "--output", "report_after.md", "--save-internal"],
        setup.work_dir
    )
    assert_equal(code, 0, "第二次导出成功")

    report_after_path = setup.work_dir / "report_after.md"
    with open(report_after_path, "r", encoding="utf-8") as f:
        content_after = f.read()

    assert_true("操作历史摘要" in content_after, "第二次导出也包含历史摘要")

    rounds_before = content_before.count("导入数据")
    rounds_after = content_after.count("导入数据")
    assert_true(rounds_after > rounds_before, "第二次导出包含更多轮次")

    assert_true("数据一致性检查" in content_after, "导出包含一致性检查")
    assert_true("导出历史" in content_after, "导出包含导出历史")

    print()
    print("🎉 测试4通过: 导出前后历史摘要正确包含，核对一致")


def test_5_corrupted_snapshot_recovery(setup):
    """测试5: 损坏快照恢复"""
    print_section("测试5: 损坏快照恢复")

    from timeline_review.storage import StateStore
    store = StateStore(str(setup.work_dir))
    batch_id = store.get_active_batch()

    snapshots = store.list_historical_snapshots(batch_id, limit=10)
    assert_true(len(snapshots) >= 2, "至少有2个快照可用于测试")

    target_snapshot = snapshots[1]
    snapshot_id = target_snapshot["snapshot_id"]
    snapshot_path = Path(target_snapshot["filepath"])

    print(f"   目标快照: {snapshot_id}")
    print(f"   文件路径: {snapshot_path}")

    with open(snapshot_path, "w", encoding="utf-8") as f:
        f.write("{ this is corrupted json !!! }")

    loaded = store.load_historical_snapshot(batch_id, snapshot_id)
    assert_true(loaded is not None, "快照损坏后自动从备份恢复")

    repair_result = store.repair_snapshot_file(batch_id, snapshot_id)
    assert_true(repair_result["repaired"], "快照修复成功")
    print(f"   修复结果: {repair_result['message']}")
    for action in repair_result["actions"]:
        print(f"   - {action}")

    snapshots_dir = store._historical_snapshots_dir(batch_id)
    another_snapshot = snapshots[2]
    another_path = Path(another_snapshot["filepath"])
    backup_path = another_path.with_suffix(another_path.suffix + ".bak")
    assert_true(backup_path.exists(), "所有快照都有备份文件")

    with open(another_path, "w", encoding="utf-8") as f:
        f.write("CORRUPTED DATA")

    loaded2 = store.load_historical_snapshot(batch_id, another_snapshot["snapshot_id"])
    assert_true(loaded2 is not None, "第二张快照损坏后也能从备份恢复")

    print()
    print("🎉 测试5通过: 快照损坏后可从备份或相邻快照恢复")


def test_6_duplicate_import_detection(setup):
    """测试6: 重复导入检测与提示"""
    print_section("测试6: 重复导入检测")

    code, out, err = run_cli(["import", "examples/app.log"], setup.work_dir)
    assert_equal(code, 0, "重复导入命令执行成功")
    assert_true("跳过" in out or "已导入过" in out or "⏭️" in out, "正确提示重复导入")
    print(f"   重复导入提示正确")

    code, out, err = run_cli(["import", "--force", "examples/app.log"], setup.work_dir)
    assert_equal(code, 0, "强制重新导入成功")
    assert_true("强制" in out or "重新导入" in out, "正确提示强制重新导入")
    print(f"   强制重新导入提示正确")

    from timeline_review.storage import StateStore
    store = StateStore(str(setup.work_dir))
    batch_id = store.get_active_batch()
    rounds = store.list_rounds(batch_id, limit=10)

    force_rounds = [r for r in rounds if r.get("detail", {}).get("files_imported")]
    assert_true(len(force_rounds) >= 1, "强制重新导入生成独立轮次")
    print(f"   强制重新导入生成新轮次")

    print()
    print("🎉 测试6通过: 重复导入检测和强制重新导入正常")


def test_7_database_state_lag_detection(setup):
    """测试7: 数据库状态落后检测"""
    print_section("测试7: 数据库状态落后检测")

    from timeline_review.storage import StateStore
    store = StateStore(str(setup.work_dir))
    batch_id = store.get_active_batch()

    lag_result = store.check_database_state_lag(batch_id)
    assert_true(not lag_result["is_lagged"], "正常状态下检测为不落后")
    print(f"   正常状态检测通过: 快照={lag_result['snapshot_event_count']}, 实际={lag_result['actual_event_count']}")

    code, out, err = run_cli(["history", "--check-lag"], setup.work_dir)
    assert_equal(code, 0, "CLI状态落后检查成功")
    assert_true("一致" in out, "CLI显示状态一致")

    snapshot_path = store._overview_snapshot_path(batch_id)
    with open(snapshot_path, "r", encoding="utf-8") as f:
        snapshot = json.load(f)
    snapshot["event_count"] = 99999
    with open(snapshot_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f)

    lag_result2 = store.check_database_state_lag(batch_id)
    assert_true(lag_result2["is_lagged"], "人为制造落后状态后检测为落后")
    print(f"   落后状态检测通过: 快照={lag_result2['snapshot_event_count']}, 实际={lag_result2['actual_event_count']}")

    fix_result = store.fix_snapshot_inconsistencies(batch_id)
    assert_true(fix_result["fixed"], "快照不一致修复成功")
    print(f"   快照修复成功")

    lag_result3 = store.check_database_state_lag(batch_id)
    assert_true(not lag_result3["is_lagged"], "修复后状态恢复正常")
    print(f"   修复后检测通过")

    print()
    print("🎉 测试7通过: 数据库状态落后检测和修复正常")


def test_8_snapshot_restore(setup):
    """测试8: 快照恢复功能"""
    print_section("测试8: 快照恢复")

    from timeline_review.storage import StateStore
    store = StateStore(str(setup.work_dir))
    batch_id = store.get_active_batch()

    snapshots = store.list_historical_snapshots(batch_id, limit=10)
    assert_true(len(snapshots) >= 3, "至少有3个快照可用于恢复测试")

    target_snapshot = snapshots[-1]
    target_id = target_snapshot["snapshot_id"]
    target_event_count = target_snapshot["event_count"]

    print(f"   恢复目标: {target_id}")
    print(f"   目标事件数: {target_event_count}")

    rounds_before = store.list_rounds(batch_id, limit=100)
    round_count_before = len(rounds_before)

    restore_result = store.restore_to_snapshot(batch_id, target_id)
    assert_true(restore_result["success"], "快照恢复成功")
    print(f"   恢复结果: {restore_result['message']}")
    print(f"   恢复轮次: {restore_result['round_number']}")

    rounds_after = store.list_rounds(batch_id, limit=100)
    round_count_after = len(rounds_after)
    assert_true(round_count_after > round_count_before, "恢复操作生成新轮次")

    restore_round = rounds_after[0]
    assert_equal(restore_round["trigger"], "restore", "新轮次触发原因是恢复")

    current_snapshot = store.load_overview_snapshot(batch_id, auto_refresh=False)
    assert_equal(current_snapshot.get("event_count", -1), target_event_count, "恢复后事件数正确")

    print()
    print("🎉 测试8通过: 快照恢复功能正常，生成独立恢复轮次")


def test_9_cli_commands(setup):
    """测试9: CLI所有新增命令"""
    print_section("测试9: CLI新增命令验证")

    test_cases = [
        (["history", "--rounds"], "轮次时间线", "列出轮次"),
        (["history", "--round", "1"], "轮次详情", "查看轮次详情"),
        (["history", "--round-diff", "1"], "轮次变更差异", "查看轮次差异"),
        (["history", "--check-lag"], "状态落后", "检查状态落后"),
        (["history", "--check-config"], "配置冲突", "检查配置冲突"),
        (["overview", "--rounds"], "轮次时间线", "overview --rounds"),
        (["overview", "--round", "2"], "轮次详情", "overview --round"),
        (["overview", "--round-diff", "2"], "轮次变更差异", "overview --round-diff"),
    ]

    for args, expected_keyword, description in test_cases:
        code, out, err = run_cli(args, setup.work_dir)
        assert_equal(code, 0, f"{description} 命令执行成功")
        print(f"   ✅ {description}: 成功")

    print()
    print("🎉 测试9通过: 所有CLI新增命令正常工作")


def test_10_round_diff_comparison(setup):
    """测试10: 轮次差异对比，验证与上一轮、当前轮的差异"""
    print_section("测试10: 轮次差异对比")

    from timeline_review.storage import StateStore
    store = StateStore(str(setup.work_dir))
    batch_id = store.get_active_batch()

    rounds = store.list_rounds(batch_id, limit=100)
    assert_true(len(rounds) >= 5, "至少有5轮可用于对比测试")

    for i in range(1, min(5, len(rounds) + 1)):
        diff = store.get_round_diff(batch_id, i)
        assert_true(diff is not None, f"第{i}轮差异存在")
        assert_true("diff" in diff, f"第{i}轮包含diff字段")
        assert_true("before_snapshot_id" in diff, f"第{i}轮包含前快照ID")
        assert_true("after_snapshot_id" in diff, f"第{i}轮包含后快照ID")

        diff_content = diff["diff"]
        assert_true("summary" in diff_content, f"第{i}轮差异包含摘要")
        print(f"   第{i}轮: {len(diff_content['summary'])} 项变更")

    first_round = store.get_round(batch_id, 1)
    last_round = store.get_round(batch_id, rounds[0]["round_number"])

    if first_round and last_round:
        first_events = first_round["after_snapshot"].get("event_count", 0)
        last_events = last_round["after_snapshot"].get("event_count", 0)
        print(f"   首轮事件数: {first_events}, 末轮事件数: {last_events}")
        assert_true(last_events >= first_events, "事件数总体趋势正确")

    code, out, err = run_cli(["overview", "--diff", "first"], setup.work_dir)
    assert_equal(code, 0, "与最初状态对比成功")
    assert_true("变更摘要" in out, "包含变更摘要")

    print()
    print("🎉 测试10通过: 轮次差异对比功能完整")


def main():
    print("\n" + "🚀" * 40)
    print("🚀  导入历史增强功能 - 完整测试套件")
    print("🚀" * 40)

    setup = TestSetup()
    print(f"\n📂 工作目录: {setup.work_dir}")

    all_passed = True
    tests = [
        test_1_rapid_imports_without_sleep,
        test_2_cross_restart_review,
        test_3_config_switch_refresh,
        test_4_export_before_after_verification,
        test_5_corrupted_snapshot_recovery,
        test_6_duplicate_import_detection,
        test_7_database_state_lag_detection,
        test_8_snapshot_restore,
        test_9_cli_commands,
        test_10_round_diff_comparison,
    ]

    passed_count = 0
    failed_count = 0

    for i, test in enumerate(tests, 1):
        try:
            test(setup)
            passed_count += 1
        except Exception as e:
            all_passed = False
            failed_count += 1
            print(f"\n❌ 测试{i}失败: {e}")
            import traceback
            traceback.print_exc()

    print()
    print("=" * 80)
    print("📊 测试结果汇总")
    print("=" * 80)
    print(f"   总测试数: {len(tests)}")
    print(f"   ✅ 通过: {passed_count}")
    print(f"   ❌ 失败: {failed_count}")
    print()

    if all_passed:
        print("🎉 所有测试通过！导入历史增强功能验证完成！")
        print()
        print("📋 已验证的功能清单:")
        features = [
            ("连续快速导入", "每轮生成独立快照，无需sleep"),
            ("跨重启回看", "重启后可查看任意轮次及差异"),
            ("配置切换检测", "配置变更生成轮次，冲突检测有效"),
            ("导出历史摘要", "导出报告包含完整历史摘要"),
            ("快照损坏恢复", "损坏快照可从备份/相邻快照恢复"),
            ("重复导入检测", "正确提示重复和强制重新导入"),
            ("状态落后检测", "检测并修复快照与数据库不一致"),
            ("快照恢复", "可恢复到任意历史快照状态"),
            ("CLI命令", "所有新增命令正常工作"),
            ("轮次差异对比", "可查看任意轮次与上一轮、当前轮差异"),
        ]
        for name, desc in features:
            print(f"   ✅ {name}: {desc}")
    else:
        print(f"❌ 有 {failed_count} 个测试失败，请检查错误信息")

    setup.cleanup()

    print()
    print("💡 下一步操作建议:")
    print(f"   cd {setup.work_dir}")
    print(f"   python -m timeline_review history --rounds          # 查看所有轮次")
    print(f"   python -m timeline_review history --round 1        # 查看第1轮详情")
    print(f"   python -m timeline_review history --round-diff 2   # 查看第2轮差异")
    print(f"   python -m timeline_review history --check-lag      # 检查状态落后")
    print(f"   python -m timeline_review history --check-config   # 检查配置冲突")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
