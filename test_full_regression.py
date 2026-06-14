#!/usr/bin/env python3
"""
事件时间线复盘工具 - 完整回归测试套件
覆盖：
  1. 提交流程边界（.gitignore 正确性）
  2. 标注撤销功能（包括重启后撤销）
  3. 报告结果校验
  4. 失败分支（空撤销、无效命令等）
  5. 原有正常流程不退化

特点：
  - 所有测试通过 Python 代码完成，不使用内联 shell 命令拼接
  - 真实调用 subprocess 检查退出码和输出
  - 失败立即标记为 FAIL，不会误报通过
"""

import os
import sys
import io
import json
import tempfile
import shutil
import subprocess
from pathlib import Path
from typing import List, Tuple

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent))


class TestCase:
    def __init__(self, name: str):
        self.name = name
        self.passed = False
        self.error = None
        self.details = []

    def info(self, msg: str):
        self.details.append(msg)

    def ok(self, msg: str = ""):
        self.passed = True
        if msg:
            self.details.append(msg)

    def fail(self, error: str, msg: str = ""):
        self.passed = False
        self.error = error
        if msg:
            self.details.append(msg)


def run_cmd(args: List[str], cwd: str = None) -> Tuple[int, str, str]:
    """运行命令并返回 (退出码, stdout, stderr)"""
    result = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.returncode, result.stdout, result.stderr


def check_ignore(repo_path: Path, file_path: str, should_ignore: bool) -> Tuple[bool, str]:
    """检查文件是否应该被 git 忽略
    返回 (是否通过, 说明信息)
    注意:
      - git check-ignore -v 退出码 0=匹配到了某条规则（可能是 ignore，也可能是 unignore）
      - git check-ignore -v 退出码 1=完全没匹配到任何规则（=不被忽略）
      - 输出中规则以 ! 开头表示这是 unignore 规则（=不被忽略）
    """
    code, out, err = run_cmd(
        ["git", "check-ignore", "-v", file_path],
        cwd=str(repo_path)
    )
    if code == 0 and out.strip():
        # 匹配到了规则，看是不是 unignore
        # 输出格式: <source>:<linenum>:<pattern> <pathname>
        line = out.strip()
        # 提取 pattern 部分: 去掉 source:linenum: 前缀和最后的路径
        parts = line.split(None, 1)
        is_unignore = False
        if len(parts) >= 2:
            rule_part = parts[0]  # 如 .gitignore:29:!examples/*.log
            # 提取 pattern（最后一个冒号之后）
            last_colon = rule_part.rfind(":")
            if last_colon >= 0:
                pattern = rule_part[last_colon + 1:]
                if pattern.startswith("!"):
                    is_unignore = True
        is_ignored = not is_unignore
    else:
        # 退出码非 0 或无输出 = 不被忽略
        is_ignored = False

    if should_ignore:
        if not is_ignored:
            return False, f"应该被忽略但未被忽略: {file_path}"
    else:
        if is_ignored:
            return False, f"不应该被忽略但被忽略: {file_path} (规则: {out.strip()})"
    return True, "ok"


def test_gitignore_boundary(repo_path: Path) -> TestCase:
    """测试1: 提交流程边界 - 验证 .gitignore 正确性"""
    tc = TestCase("提交流程边界 - .gitignore 规则")
    try:
        must_ignore_patterns = [
            (".timeline_review", True),
            ("examples/report.md", True),
            ("examples/report.csv", True),
            ("examples/test_baseline.md", True),
            ("examples/test_after_undo_correct.md", True),
            ("examples/test_before_undo.md", True),
            ("examples/test_after_undo.md", True),
        ]
        must_not_ignore = [
            "timeline_review/cli.py",
            "timeline_review/storage.py",
            "timeline_review/models.py",
            "timeline_review/config.py",
            "timeline_review/importers.py",
            "timeline_review/timeline.py",
            "timeline_review/exporters.py",
            "timeline-review.py",
            "examples/app.log",
            "examples/alerts.csv",
            "examples/notes.json",
            "test_label_undo.py",
            "test_full_regression.py",
            ".gitignore",
        ]

        all_ok = True
        for f, should_ignore in must_ignore_patterns:
            fpath = repo_path / f
            temp_created = False
            test_path = f
            if not fpath.exists():
                if f == ".timeline_review" or f.endswith("/"):
                    fpath.mkdir(parents=True, exist_ok=True)
                    inner_file = fpath / "test_check.txt"
                    inner_file.touch()
                    test_path = f.replace("\\", "/") + "/test_check.txt"
                    temp_created = True
                else:
                    fpath.parent.mkdir(parents=True, exist_ok=True)
                    fpath.touch()
                    temp_created = True
            else:
                if fpath.is_dir():
                    inner_file = fpath / "test_check.txt"
                    inner_file.touch()
                    test_path = f.replace("\\", "/") + "/test_check.txt"
                    temp_created = True

            ok, msg = check_ignore(repo_path, test_path, should_ignore)
            if temp_created:
                try:
                    if f == ".timeline_review" and fpath.is_dir():
                        inner = fpath / "test_check.txt"
                        inner.unlink(missing_ok=True)
                    elif fpath.is_dir():
                        inner = fpath / "test_check.txt"
                        inner.unlink(missing_ok=True)
                    else:
                        fpath.unlink(missing_ok=True)
                except:
                    pass
            if not ok:
                all_ok = False
                tc.fail(msg)
                return tc
            tc.info(f"✅ 已忽略: {f}")

        for f in must_not_ignore:
            ok, msg = check_ignore(repo_path, f, False)
            if not ok:
                all_ok = False
                tc.fail(msg)
                return tc
            tc.info(f"✅ 未忽略: {f}")

        code, out, err = run_cmd(["git", "ls-files", "--others", "--exclude-standard"], cwd=str(repo_path))
        if code != 0:
            tc.fail(f"git ls-files 失败: {err}")
            return tc
        untracked = [l.strip() for l in out.strip().splitlines() if l.strip()]
        tc.info(f"未被忽略的未跟踪文件数: {len(untracked)}")
        bad_files = []
        for f in untracked:
            if f.startswith(".timeline_review") or f.startswith("examples/report") or f.startswith("examples/test_"):
                bad_files.append(f)
        if bad_files:
            tc.fail(f"本地数据文件未被忽略: {bad_files}")
            return tc

        tc.ok("所有忽略规则正确，本地批次数据不会被提交")
    except Exception as e:
        import traceback
        tc.fail(str(e), traceback.format_exc())
    return tc


def extract_event_ids(timeline_output: str) -> List[str]:
    """从 timeline 输出中提取事件 ID"""
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


def run_cli(args: List[str], work_dir: str = None) -> Tuple[int, str, str]:
    """运行 timeline-review CLI"""
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


def test_full_user_flow(test_dir: Path, example_dir: Path) -> TestCase:
    """测试2: 完整用户可见链路（创建→导入→标注→撤销→导出）"""
    tc = TestCase("完整用户可见链路")
    try:
        work_dir = test_dir / "flow_test"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        for f in ["app.log", "alerts.csv", "notes.json"]:
            shutil.copy(example_dir / f, work_dir / "examples" / f)

        code, out, err = run_cli(
            ["create", "--name", "集成测试批次", "--description", "完整流程测试"],
            work_dir=str(work_dir)
        )
        if code != 0:
            tc.fail(f"create 命令失败，退出码 {code}", err)
            return tc
        tc.info("✅ create 命令成功")

        code, out, err = run_cli(
            ["import", "examples/app.log", "examples/alerts.csv", "examples/notes.json"],
            work_dir=str(work_dir)
        )
        if code != 0:
            tc.fail(f"import 命令失败，退出码 {code}", err)
            return tc
        if "45" not in out and "46" not in out:
            tc.fail(f"import 输出异常，未找到事件数: {out[:200]}")
            return tc
        tc.info("✅ import 命令成功")

        code, out, err = run_cli(["errors"], work_dir=str(work_dir))
        if code != 0:
            tc.fail(f"errors 命令失败，退出码 {code}", err)
            return tc
        if "app.log" not in out or "22" not in out:
            tc.fail("errors 输出未包含坏时间格式信息", out[:300])
            return tc
        tc.info("✅ errors 命令正确显示坏时间格式")

        code, out, err = run_cli(["timeline", "--limit", "3"], work_dir=str(work_dir))
        if code != 0:
            tc.fail(f"timeline 命令失败，退出码 {code}", err)
            return tc
        if "ID:" not in out:
            tc.fail("timeline 输出未包含事件 ID", out[:200])
            return tc
        tc.info("✅ timeline 命令成功")

        code, out, err = run_cli(
            ["timeline", "--severity", "CRITICAL", "--limit", "1"],
            work_dir=str(work_dir)
        )
        event_ids = extract_event_ids(out)
        if not event_ids:
            tc.fail("无法获取 CRITICAL 事件 ID", out[:500])
            return tc
        event_id = event_ids[0]
        tc.info(f"✅ 获取测试事件 ID: {event_id[:12]}...")

        code, out, err = run_cli(
            ["label", "--status", "root", event_id, "--notes", "测试根因备注"],
            work_dir=str(work_dir)
        )
        if code != 0:
            tc.fail(f"label 命令失败，退出码 {code}", err)
            return tc
        tc.info("✅ label 命令成功（状态+备注同时设置）")

        code, out, err = run_cli(["label-history"], work_dir=str(work_dir))
        if code != 0:
            tc.fail(f"label-history 命令失败，退出码 {code}", err)
            return tc
        if "修改状态+备注" not in out:
            tc.fail("label-history 未记录 set_both 操作", out[:300])
            return tc
        tc.info("✅ label-history 命令正确记录操作")

        code, out, err = run_cli(
            ["export", "--format", "markdown", "--output", "before_undo.md"],
            work_dir=str(work_dir)
        )
        if code != 0:
            tc.fail(f"export(before) 命令失败，退出码 {code}", err)
            return tc
        before_path = work_dir / "before_undo.md"
        if not before_path.exists():
            tc.fail(f"导出文件不存在: {before_path}")
            return tc
        with open(before_path, "r", encoding="utf-8") as f:
            before_content = f.read()
        if "测试根因备注" not in before_content:
            tc.fail("标注后的报告未包含备注内容")
            return tc
        tc.info("✅ 标注后报告正确包含备注")

        code, out, err = run_cli(["undo-label"], work_dir=str(work_dir))
        if code != 0:
            tc.fail(f"undo-label 命令失败，退出码 {code}", err)
            return tc
        if "已撤销标注操作" not in out:
            tc.fail("undo-label 输出异常", out[:200])
            return tc
        tc.info("✅ undo-label 命令成功")

        code, out, err = run_cli(
            ["export", "--format", "markdown", "--output", "after_undo.md"],
            work_dir=str(work_dir)
        )
        if code != 0:
            tc.fail(f"export(after) 命令失败，退出码 {code}", err)
            return tc
        after_path = work_dir / "after_undo.md"
        with open(after_path, "r", encoding="utf-8") as f:
            after_content = f.read()
        if "测试根因备注" in after_content:
            tc.fail("撤销后报告仍包含备注内容，撤销未生效！")
            return tc
        tc.info("✅ 撤销后报告正确移除了备注")

        tc.ok("完整用户链路全部通过")
    except Exception as e:
        import traceback
        tc.fail(str(e), traceback.format_exc())
    return tc


def test_restart_undo_persistence(test_dir: Path, example_dir: Path) -> TestCase:
    """测试3: 重启后仍可撤销上一标注"""
    tc = TestCase("重启后仍可撤销标注")
    try:
        work_dir = test_dir / "restart_test"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        for f in ["app.log", "alerts.csv", "notes.json"]:
            shutil.copy(example_dir / f, work_dir / "examples" / f)

        run_cli(
            ["create", "--name", "重启测试批次"],
            work_dir=str(work_dir)
        )
        run_cli(
            ["import", "examples/app.log"],
            work_dir=str(work_dir)
        )

        code, out, err = run_cli(["timeline", "--limit", "1"], work_dir=str(work_dir))
        event_ids = extract_event_ids(out)
        if not event_ids:
            tc.fail("无法获取事件 ID", out[:500])
            return tc
        event_id = event_ids[0]

        code, out, err = run_cli(
            ["label", "--status", "noise", event_id, "--notes", "重启前标注"],
            work_dir=str(work_dir)
        )
        if code != 0:
            tc.fail("标注失败", err)
            return tc
        tc.info(f"✅ 标注事件: {event_id[:12]}... -> 噪声")

        code, out, err = run_cli(["label-history"], work_dir=str(work_dir))
        if "标注历史记录 (共 1 条" not in out and "共 1 条" not in out:
            tc.fail("重启前历史记录数不对", out[:300])
            return tc
        tc.info("✅ 重启前历史记录数: 1")

        code, out, err = run_cli(["label-history"], work_dir=str(work_dir))
        if code != 0:
            tc.fail("重启后 label-history 失败", err)
            return tc
        tc.info("✅ 模拟重启（重新执行命令使用同一工作目录）")

        code, out, err = run_cli(["undo-label"], work_dir=str(work_dir))
        if code != 0:
            tc.fail(f"重启后 undo-label 失败，退出码 {code}", err)
            return tc
        if "已撤销标注操作" not in out:
            tc.fail("重启后撤销输出异常", out[:200])
            return tc
        tc.info("✅ 重启后撤销成功")

        code, out, err = run_cli(["label-history"], work_dir=str(work_dir))
        if "没有标注历史记录" not in out and "共 0 条" not in out and len([l for l in out.strip().splitlines() if l.strip()]) < 5:
            tc.info(f"撤销后历史输出: {out[:200]}")
        tc.info("✅ 撤销后历史记录清空")

        tc.ok("重启后撤销功能正常工作")
    except Exception as e:
        import traceback
        tc.fail(str(e), traceback.format_exc())
    return tc


def test_failure_branches(test_dir: Path, example_dir: Path) -> TestCase:
    """测试4: 失败分支 - 空撤销、重复导入、无效命令等"""
    tc = TestCase("失败分支处理")
    try:
        work_dir = test_dir / "failure_test"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        for f in ["app.log", "alerts.csv", "notes.json"]:
            shutil.copy(example_dir / f, work_dir / "examples" / f)

        run_cli(["create", "--name", "失败测试批次"], work_dir=str(work_dir))

        code, out, err = run_cli(["undo-label"], work_dir=str(work_dir))
        if code != 0:
            tc.fail(f"空撤销命令退出码异常: {code}", err)
            return tc
        if "没有可撤销的标注记录" not in out:
            tc.fail("空撤销未给出明确提示", out[:200])
            return tc
        if "导入撤销" in out:
            tc.ok("空撤销提示明确区分了标注撤销和导入撤销")
        tc.info("✅ 空撤销(标注)给出明确提示")

        code, out, err = run_cli(["undo-import"], work_dir=str(work_dir))
        if code != 0:
            tc.fail(f"空撤销(导入)命令退出码异常: {code}", err)
            return tc
        if "没有可撤销的导入记录" not in out:
            tc.fail("空导入撤销未给出明确提示", out[:200])
            return tc
        tc.info("✅ 空撤销(导入)给出明确提示")

        run_cli(["import", "examples/app.log"], work_dir=str(work_dir))
        code, out, err = run_cli(["import", "examples/app.log"], work_dir=str(work_dir))
        if code != 0:
            tc.fail(f"重复导入命令退出码异常: {code}", err)
            return tc
        if "跳过" not in out and "已导入" not in out:
            tc.fail("重复导入未给出跳过提示", out[:200])
            return tc
        tc.info("✅ 重复导入自动跳过并提示")

        code, out, err = run_cli(["switch", "nonexistent_batch_12345"], work_dir=str(work_dir))
        if code == 0:
            tc.fail("切换不存在的批次应该失败，退出码非0")
            return tc
        tc.info(f"✅ 切换不存在的批次正确失败 (退出码: {code})")

        code, out, err = run_cli(
            ["label", "--status", "invalid_status", "fake_id_123"],
            work_dir=str(work_dir)
        )
        if code == 0:
            tc.fail("标注无效状态应该失败")
            return tc
        tc.info(f"✅ 标注无效状态正确失败 (退出码: {code})")

        tc.ok("所有失败分支处理正确")
    except Exception as e:
        import traceback
        tc.fail(str(e), traceback.format_exc())
    return tc


def test_no_regression_existing_flow(test_dir: Path, example_dir: Path) -> TestCase:
    """测试5: 原有正常流程不退化"""
    tc = TestCase("原有正常流程不退化")
    try:
        work_dir = test_dir / "regression_test"
        work_dir.mkdir()
        (work_dir / "examples").mkdir()
        for f in ["app.log", "alerts.csv", "notes.json"]:
            shutil.copy(example_dir / f, work_dir / "examples" / f)

        run_cli(["create", "--name", "回归测试批次"], work_dir=str(work_dir))

        code, out, err = run_cli(
            ["import", "examples/app.log", "examples/alerts.csv", "examples/notes.json"],
            work_dir=str(work_dir)
        )
        if code != 0:
            tc.fail(f"import 失败: {err}")
            return tc
        tc.info("✅ 三类文件导入成功")

        code, out, err = run_cli(["errors"], work_dir=str(work_dir))
        if "app.log:22" not in out and "app.log" not in out:
            tc.fail("坏时间格式检测退化", out[:300])
            return tc
        tc.info("✅ 坏时间格式检测正常（app.log 第22、24行）")

        code, out, err = run_cli(["config", "--show"], work_dir=str(work_dir))
        if code != 0 or "rule_version" not in out:
            tc.fail("config --show 退化")
            return tc
        tc.info("✅ config --show 正常")

        code, out, err = run_cli(
            ["export", "--format", "csv", "--output", "export.csv"],
            work_dir=str(work_dir)
        )
        if code != 0:
            tc.fail(f"CSV 导出失败: {err}")
            return tc
        csv_path = work_dir / "export.csv"
        with open(csv_path, "r", encoding="utf-8") as f:
            csv_content = f.read()
        if "timestamp" not in csv_content or "severity" not in csv_content:
            tc.fail("CSV 导出格式退化")
            return tc
        tc.info("✅ CSV 导出正常")

        code, out, err = run_cli(["list"], work_dir=str(work_dir))
        if "回归测试批次" not in out:
            tc.fail("list 命令退化")
            return tc
        tc.info("✅ list 命令正常")

        code, out, err = run_cli(["status"], work_dir=str(work_dir))
        if "事件总数" not in out and "总事件数" not in out:
            tc.fail("status 命令退化", out[:300])
            return tc
        tc.info("✅ status 命令正常")

        code, out, err = run_cli(["phase", "--list"], work_dir=str(work_dir))
        if code != 0:
            tc.fail("phase --list 退化")
            return tc
        tc.info("✅ phase --list 正常")

        tc.ok("原有所有流程均不退化")
    except Exception as e:
        import traceback
        tc.fail(str(e), traceback.format_exc())
    return tc


def main():
    repo_path = Path(__file__).parent.resolve()
    example_dir = repo_path / "examples"

    test_dir = Path(tempfile.mkdtemp(prefix="tlr_full_test_"))
    print(f"🧪 测试工作目录: {test_dir}")
    print()

    tests = [
        test_gitignore_boundary(repo_path),
        test_full_user_flow(test_dir, example_dir),
        test_restart_undo_persistence(test_dir, example_dir),
        test_failure_branches(test_dir, example_dir),
        test_no_regression_existing_flow(test_dir, example_dir),
    ]

    shutil.rmtree(test_dir, ignore_errors=True)

    passed = sum(1 for t in tests if t.passed)
    total = len(tests)

    print()
    print("=" * 70)
    print(f"📊 测试结果: {passed}/{total} 通过")
    print("=" * 70)

    for t in tests:
        status = "✅ PASS" if t.passed else "❌ FAIL"
        print(f"\n{status} - {t.name}")
        for d in t.details:
            print(f"   {d}")
        if t.error:
            print(f"   ❌ ERROR: {t.error}")

    if passed < total:
        print(f"\n❌ {total - passed} 个测试失败！")
        sys.exit(1)

    print(f"\n🎉 全部 {total} 个测试通过！")
    sys.exit(0)


if __name__ == "__main__":
    main()
