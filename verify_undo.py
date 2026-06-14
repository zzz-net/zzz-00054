#!/usr/bin/env python3
"""验证标注撤销后报告内容正确"""
import sys
import io

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))

from timeline_review.storage import StateStore

store = StateStore()
batch_id = store.get_active_batch()
print(f"当前批次: {batch_id}")

event_id = "3188353c103be5f2"
event = store.get_event_by_id(batch_id, event_id)

print(f"\n事件 ID: {event_id}")
print(f"当前状态: {event.status.value}")
print(f"当前备注: '{event.notes}'")
print(f"预期状态: 待确认")
print(f"预期备注: ''")

assert event.status.value == "待确认", f"状态错误，应为'待确认'，实际'{event.status.value}'"
assert event.notes == "", f"备注错误，应为空，实际'{event.notes}'"

print("\n✅ 事件状态和备注都正确恢复了！")

# 验证报告内容
with open("examples/test_baseline.md", "r", encoding="utf-8") as f:
    baseline = f.read()
with open("examples/test_after_undo_correct.md", "r", encoding="utf-8") as f:
    after = f.read()

# 检查报告中不包含"测试备注"
assert "测试备注" not in after, "撤销后报告不应该包含'测试备注'"
assert "测试备注" not in baseline, "baseline 报告不应该包含'测试备注'"

# 检查报告中事件状态
assert event_id in after
assert event_id in baseline
assert "待确认" in after
assert "已确认" not in after, "撤销后报告不应该包含'已确认'"

print("✅ 报告内容正确！撤销后没有测试备注，状态为待确认")
print("\n🎉 所有验证通过！")
