#!/usr/bin/env python3
"""
真实命令跑通导入-撤销-恢复完整流程验证
模拟用户真实操作场景
"""
import os
import sys
import io
import tempfile
import shutil
import subprocess
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

WORK = Path(tempfile.mkdtemp(prefix="tlr_manual_"))
EXAMPLES = Path(__file__).parent / "examples"
(WORK / "examples").mkdir()
for f in ["app.log", "alerts.csv", "notes.json"]:
    shutil.copy(EXAMPLES / f, WORK / "examples" / f)
os.chdir(WORK)

print("=" * 70)
print(f"📂 工作目录: {WORK}")
print("=" * 70)

PYTHON = sys.executable
BASE = [PYTHON, "-m", "timeline_review"]
REPO = str(Path(__file__).parent.resolve())
env = os.environ.copy()
env["PYTHONPATH"] = REPO + (os.pathsep + env["PYTHONPATH"] if "PYTHONPATH" in env else "")

def run(desc, *args, should_pass=True):
    full = BASE + list(args)
    print(f"\n{'─'*70}")
    print(f"▶️  [{desc}] timeline-review {' '.join(args)}")
    print(f"{'─'*70}")
    r = subprocess.run(full, cwd=str(WORK), capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if out:
        print(out)
    if err:
        print(err, file=sys.stderr)
    print(f"   [exit={r.returncode}]")
    if should_pass and r.returncode != 0:
        print(f"\n❌ 步骤失败，终止在 [{desc}]")
        sys.exit(1)
    return r

print()
print("第1步: 创建批次")
run("创建批次", "create", "--name", "真实命令全流程验证")

print()
print("第2步: 导入 app.log")
run("导入app.log", "import", "examples/app.log")

print()
print("第3步: 导入 alerts.csv")
run("导入alerts.csv", "import", "examples/alerts.csv")

print()
print("第4步: 查看概览 + 一致性检查")
run("概览+一致性", "overview", "--check-consistency")

print()
print("第5步: 查看导入详情（最近一次）")
run("导入详情", "import-detail")

print()
print("第6步: 撤销最近一次导入（alerts.csv）")
run("撤销最近导入", "undo-import")

print()
print("第7步: 查看概览验证撤销生效")
run("撤销后概览", "overview", "--check-consistency")

print()
print("第8步: 恢复被撤销的导入")
run("恢复导入", "restore-import")

print()
print("第9步: 导入 notes.json（完成3文件导入）")
run("导入notes.json", "import", "examples/notes.json")

print()
print("第10步: 查看概览，3文件都在")
run("3文件概览", "overview", "--check-consistency")

print()
print("第11步: 按轮次号撤销中间的 alerts.csv（查看imports_index确定round号）")
r = run("查看imports（通过timeline间接看）", "import-detail")
print()
run("按轮次撤销2号", "undo-import", "--round", "2")

print()
print("第12步: 撤销后验证概览一致性")
run("撤销round2后验证", "overview", "--check-consistency")

print()
print("第13步: 查看2号轮次详情（已撤销）")
run("已撤销轮次详情", "import-detail", "--round", "2")

print()
print("第14步: 强制重新导入同名app.log（测试多轮）")
run("强制重导app.log", "import", "--force", "examples/app.log")

print()
print("第15步: 配置冲突检测")
run("配置冲突检测", "config", "--check-conflict", "--dedup-window", "600")

print()
print("第16步: 配置修改生效（正常修改）")
run("修改配置", "config", "--dedup-window", "300")

print()
print("第17步: 导出markdown + 核对内容")
run("导出+核对", "export", "--format", "markdown", "--output", "final_report.md", "--verify", "--save-internal")

print()
print("第18步: 查看概览最终状态，全程没有分叉")
r = run("最终概览校验", "overview", "--check-consistency")

print()
print("第19步: 历史快照列表（确认有连续记录）")
run("历史快照", "history", "--limit", "20")

print()
print("=" * 70)
print("✅ 真实命令完整流程跑完，所有步骤通过！")
print("=" * 70)
print(f"   📂 工作目录保留在: {WORK}")
print(f"   📄 导出报告: {WORK / 'final_report.md'}")
print()
