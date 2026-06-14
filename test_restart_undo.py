#!/usr/bin/env python3
"""验证重启后仍可撤销上一标注"""
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

event_id = "6e5d4bdb7d4c093d"
event_before = store.get_event_by_id(batch_id, event_id)
print(f"\n标注后状态: {event_before.status.value}")
print(f"标注后备注: '{event_before.notes}'")
print(f"标注后历史记录数: {len(store.get_label_history(batch_id))}")

# 模拟重启
print("\n=== 模拟重启（创建新的 StateStore 实例） ===")
store2 = StateStore()
meta = store2.switch_batch(batch_id)
print(f"重新加载批次: {meta['id']}")

history_after_restart = store2.get_label_history(batch_id)
print(f"重启后历史记录数: {len(history_after_restart)}")
assert len(history_after_restart) == 1, "重启后历史记录应该保留"

event_after_restart = store2.get_event_by_id(batch_id, event_id)
print(f"重启后事件状态: {event_after_restart.status.value}")
print(f"重启后事件备注: '{event_after_restart.notes}'")
assert event_after_restart.status.value == "噪声", "重启后状态应该保持"
assert event_after_restart.notes == "噪声事件，重复告警", "重启后备注应该保持"

# 重启后撤销
print("\n=== 重启后撤销标注 ===")
undone = store2.undo_last_label(batch_id)
assert undone is not None, "重启后应该可以撤销"
print(f"撤销成功，操作类型: {undone.operation}")

event_after_undo = store2.get_event_by_id(batch_id, event_id)
print(f"撤销后状态: {event_after_undo.status.value}")
print(f"撤销后备注: '{event_after_undo.notes}'")
assert event_after_undo.status.value == "待确认", "撤销后状态应该恢复"
assert event_after_undo.notes == "", "撤销后备注应该恢复"

print("\n✅ 重启后撤销标注功能正常！")
print("\n🎉 所有验证通过！")
