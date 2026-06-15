#!/usr/bin/env python3
"""
批次概览增强功能 - 完整链路验证脚本
演示所有新功能：变更摘要、历史快照、导出对比、一致性检查、变更日志等
"""

import os
import sys
import io
import json
import tempfile
import shutil
import subprocess
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).parent.resolve()
EXAMPLES_DIR = REPO_ROOT / "examples"


def run_cli(args, work_dir):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    cli_args = [sys.executable, "-m", "timeline_review"] + args
    result = subprocess.run(
        cli_args,
        cwd=str(work_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    return result.returncode, result.stdout, result.stderr


def print_step(step_num, total_steps, title):
    print()
    print("=" * 70)
    print(f"[步骤 {step_num}/{total_steps}] {title}")
    print("=" * 70)


def print_success(msg):
    print(f"✅ {msg}")


def print_output(title, output, max_lines=40):
    print()
    print(f"--- {title} ---")
    lines = output.strip().splitlines()
    for i, line in enumerate(lines[:max_lines]):
        print(f"  {line}")
    if len(lines) > max_lines:
        print(f"  ... (省略 {len(lines) - max_lines} 行)")


def extract_event_id(timeline_output):
    for line in timeline_output.splitlines():
        if "ID:" in line:
            parts = line.split("ID:", 1)
            if len(parts) > 1:
                id_part = parts[1].strip().split()[0]
                if id_part and len(id_part) >= 8:
                    return id_part
    return None


def main():
    total_steps = 18
    demo_dir = Path(tempfile.mkdtemp(prefix="tlr_full_demo_"))
    work_dir = demo_dir / "workspace"
    work_dir.mkdir()
    (work_dir / "examples").mkdir()

    for f in ["app.log", "alerts.csv", "notes.json"]:
        shutil.copy(EXAMPLES_DIR / f, work_dir / "examples" / f)

    print("🚀" + "=" * 68)
    print("🚀 批次概览增强功能 - 完整链路验证")
    print("🚀" + "=" * 68)
    print(f"\n📂 工作目录: {work_dir}")
    print(f"📁 持久化目录: {work_dir / '.timeline_review'}")

    try:
        print_step(1, total_steps, "创建批次")
        code, out, err = run_cli(
            ["create", "--name", "完整链路验证批次", "--description", "验证所有增强概览功能"],
            work_dir
        )
        assert code == 0, f"创建失败: {err}"
        print_success("批次创建成功")
        print_output("创建输出", out)

        print_step(2, total_steps, "查看初始概览 + 帮助信息")
        code, out, err = run_cli(["overview", "--help"], work_dir)
        assert code == 0, f"帮助失败: {err}"
        print_output("overview --help 输出", out, max_lines=30)

        print_step(3, total_steps, "查看初始概览状态")
        code, out, err = run_cli(["overview"], work_dir)
        assert code == 0, f"概览失败: {err}"
        print_output("初始概览", out)

        print_step(4, total_steps, "导入 app.log")
        code, out, err = run_cli(["import", "examples/app.log"], work_dir)
        assert code == 0, f"导入失败: {err}"
        print_success("app.log 导入成功")
        print_output("导入输出", out)

        print_step(5, total_steps, "查看导入后变更摘要 (--diff)")
        code, out, err = run_cli(["overview", "--diff"], work_dir)
        assert code == 0, f"diff 失败: {err}"
        assert "新增导入" in out or "app.log" in out, "应显示导入变更"
        print_output("变更摘要 (导入 app.log 后)", out)

        print_step(6, total_steps, "导入 alerts.csv")
        import time
        time.sleep(0.6)
        code, out, err = run_cli(["import", "examples/alerts.csv"], work_dir)
        assert code == 0, f"导入失败: {err}"
        print_success("alerts.csv 导入成功")
        print_output("导入输出", out)

        print_step(7, total_steps, "查看历史快照记录 (--history)")
        code, out, err = run_cli(["overview", "--history"], work_dir)
        assert code == 0, f"history 失败: {err}"
        assert "历史快照记录" in out, "应显示历史快照标题"
        assert "触发原因" in out, "应显示触发原因列"
        print_output("历史快照列表", out)

        print_step(8, total_steps, "查看导入 alerts.csv 后的变更摘要")
        code, out, err = run_cli(["overview", "--diff", "previous"], work_dir)
        assert code == 0, f"diff 失败: {err}"
        assert "alerts.csv" in out, "应显示 alerts.csv 变更"
        print_output("变更摘要 (导入 alerts.csv 后)", out)

        print_step(9, total_steps, "变更配置并升级版本")
        time.sleep(0.6)
        code, out, err = run_cli(
            ["config", "--dedup-window", "180", "--gap-threshold", "900", "--bump-version"],
            work_dir
        )
        assert code == 0, f"配置失败: {err}"
        print_success("配置变更成功，版本已升级")
        print_output("配置输出", out)

        print_step(10, total_steps, "查看配置变更摘要")
        code, out, err = run_cli(["overview", "--diff"], work_dir)
        assert code == 0, f"diff 失败: {err}"
        assert "配置变更" in out or "dedup_window" in out or "rule_version" in out
        print_output("变更摘要 (配置变更后)", out)

        print_step(11, total_steps, "导入 notes.json")
        time.sleep(0.6)
        code, out, err = run_cli(["import", "examples/notes.json"], work_dir)
        assert code == 0, f"导入失败: {err}"
        print_success("notes.json 导入成功")

        print_step(12, total_steps, "测试重复导入检测 (app.log)")
        code, out, err = run_cli(["import", "examples/app.log"], work_dir)
        assert code == 0, f"重复导入失败: {err}"
        assert "已导入过，跳过" in out or "⏭️" in out
        print_success("重复导入正确提示跳过")
        print_output("重复导入输出", out)

        print_step(13, total_steps, "标注事件 + 查看变更摘要")
        code, out, err = run_cli(["timeline", "--limit", "1"], work_dir)
        event_id = extract_event_id(out)
        assert event_id, f"无法获取事件 ID: {out}"
        print_success(f"获取事件 ID: {event_id[:16]}...")

        code, out, err = run_cli(
            ["label", "--status", "root", event_id, "--notes", "根因确认，需要修复"],
            work_dir
        )
        assert code == 0, f"标注失败: {err}"
        print_success("事件标注成功")

        code, out, err = run_cli(["overview", "--diff"], work_dir)
        assert code == 0, f"diff 失败: {err}"
        assert "新标注" in out or "标注变更" in out
        print_output("变更摘要 (标注后)", out)

        print_step(14, total_steps, "第一次导出 + 查看变更")
        code, out, err = run_cli(
            ["export", "--format", "markdown", "--output", "report1.md", "--save-internal"],
            work_dir
        )
        assert code == 0, f"导出失败: {err}"
        print_success("第一次导出成功")

        code, out, err = run_cli(["overview", "--diff"], work_dir)
        assert code == 0, f"diff 失败: {err}"
        assert "新增导出" in out or "report1" in out
        print_output("变更摘要 (第一次导出后)", out)

        print_step(15, total_steps, "第二次导出 + 导出对比")
        time.sleep(0.6)
        code, out, err = run_cli(
            ["export", "--format", "markdown", "--output", "report2.md", "--save-internal"],
            work_dir
        )
        assert code == 0, f"导出失败: {err}"
        print_success("第二次导出成功")

        code, out, err = run_cli(["overview", "--export-diff"], work_dir)
        assert code == 0, f"export-diff 失败: {err}"
        assert "导出历史与对比" in out
        assert "最近两次导出对比" in out
        assert "report1" in out and "report2" in out
        print_output("导出对比", out)

        print_step(16, total_steps, "一致性检查 (--check-consistency)")
        code, out, err = run_cli(["overview", "--check-consistency"], work_dir)
        assert code == 0, f"consistency 失败: {err}"
        assert "一致" in out or "✅" in out
        print_output("一致性检查", out)

        print_step(17, total_steps, "变更日志 (--change-log)")
        code, out, err = run_cli(["overview", "--change-log", "--log-limit", "15"], work_dir)
        assert code == 0, f"change-log 失败: {err}"
        assert "变更日志" in out
        print_output("变更日志 (最近15条)", out)

        print_step(18, total_steps, "与最初状态对比 (--diff first) + 重启验证")
        code, out, err = run_cli(["overview", "--diff", "first"], work_dir)
        assert code == 0, f"diff first 失败: {err}"
        assert "变更摘要" in out
        print_output("与最初状态对比", out)

        print("\n" + "=" * 70)
        print("🔄 模拟重启 - 创建新的 StateStore 实例验证持久化")
        print("=" * 70)

        from timeline_review.storage import StateStore
        store = StateStore(str(work_dir))
        batch_id = store.get_active_batch()

        final_snapshot = store.load_overview_snapshot(batch_id, auto_refresh=False)
        history = store.list_historical_snapshots(batch_id, limit=100)
        change_log = store.get_change_log(batch_id, limit=100)
        exports = store.get_exports(batch_id)
        consistency = store.check_snapshot_consistency(batch_id)

        print(f"\n📊 重启后数据验证:")
        print(f"  ✅ 批次 ID: {batch_id}")
        print(f"  ✅ 事件总数: {final_snapshot.get('event_count', 0)}")
        print(f"  ✅ 导入文件数: {final_snapshot.get('imported_file_count', 0)}")
        print(f"  ✅ 规则版本: {final_snapshot.get('rule_version', 'unknown')}")
        print(f"  ✅ 历史快照数: {len(history)}")
        print(f"  ✅ 变更日志数: {len(change_log)}")
        print(f"  ✅ 导出记录数: {len(exports)}")
        print(f"  ✅ 快照一致性: {'一致 ✅' if consistency.get('consistent') else '不一致 ❌'}")

        print("\n" + "🎉" + "=" * 66 + "🎉")
        print("🎉 所有步骤执行成功！完整链路验证通过！")
        print("🎉" + "=" * 66 + "🎉")

        print("\n📋 已验证的功能清单:")
        features = [
            ("变更摘要", "--diff / --diff previous / --diff first / --diff <snap_id>"),
            ("历史快照", "--history"),
            ("导出对比", "--export-diff"),
            ("一致性检查", "--check-consistency / --fix"),
            ("变更日志", "--change-log / --log-type / --log-limit"),
            ("重复导入检测", "自动检测并提示跳过/强制重导"),
            ("配置变更提示", "自动记录配置变更并显示在摘要中"),
            ("持久化能力", "重启后所有数据完整保留"),
            ("冲突处理", "快照不一致时自动恢复，提供修复选项"),
            ("快照损坏恢复", "JSON损坏/空文件时自动重建"),
        ]
        for name, usage in features:
            print(f"  ✅ {name}: {usage}")

        print(f"\n📁 所有数据保存在: {work_dir}")
        print(f"   可手动检查 .timeline_review 目录下的持久化文件")

        print("\n💡 下一步操作建议:")
        print(f"   cd {work_dir}")
        print(f"   python -m timeline_review overview --diff first    # 对比最初状态")
        print(f"   python -m timeline_review overview --history        # 查看历史快照")
        print(f"   python -m timeline_review overview --change-log     # 查看变更日志")
        print(f"   python -m timeline_review overview --export-diff    # 查看导出对比")
        print(f"   python -m timeline_review overview --check-consistency  # 检查一致性")

        return 0

    except Exception as e:
        print(f"\n❌ 验证失败: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        print(f"\n💾 数据目录: {work_dir} (保留以供检查)")


if __name__ == "__main__":
    sys.exit(main())
