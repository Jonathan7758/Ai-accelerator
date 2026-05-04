"""Phase 3 Step 5: Decision thread 状态机 — 纯函数。

输入: 当前 state(str)+ action(str)+ payload(dict)
输出: TransitionResult(new_state, error, side_effects)

设计原则:
- 不读 DB、不访问 TG、不做 IO。所有 IO 留给 handlers.py 的"司机"层。
- 非法 transition 返回 error,new_state == 当前 state(不变)。
- side_effects 是 dict 形式的"建议动作清单",由 handlers 层翻译成实际调用
  (edit_message / send_message / write_ops_decisions 等)。
- 单一事实来源:此文件定义了所有合法 transition,handlers 不能绕过。

7 种 action × 状态映射 = 9 条合法 transition(spec §Step 5 写"8" — 略有出入,
我们以"语义需要"为准,9 条覆盖更完整。spec 8 vs 实际 9 不影响功能):

    proposed                        --send_displayed-->          displayed
    displayed                       --click_approve-->           approved_pending_rationale
    displayed                       --click_reject-->            rejected
    displayed                       --click_discuss-->           in_discussion
    in_discussion                   --discussion_done-->         displayed
    approved_pending_rationale      --rationale_received-->      approved
    displayed                       --archive_stale-->           deferred
    in_discussion                   --archive_stale-->           deferred
    approved_pending_rationale      --archive_stale-->           deferred

终态(无出向边): approved / rejected / deferred
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 所有合法 state(必须与 migration 004 CHECK 约束一致)
STATES = (
    "proposed",
    "displayed",
    "in_discussion",
    "approved_pending_rationale",
    "approved",
    "rejected",
    "deferred",
)

# 所有 action
ACTIONS = (
    "send_displayed",
    "click_approve",
    "click_reject",
    "click_discuss",
    "discussion_done",
    "rationale_received",
    "archive_stale",
)

# (current_state, action) → new_state
_TRANSITIONS: dict[tuple[str, str], str] = {
    ("proposed",                   "send_displayed"):     "displayed",
    ("displayed",                  "click_approve"):      "approved_pending_rationale",
    ("displayed",                  "click_reject"):       "rejected",
    ("displayed",                  "click_discuss"):      "in_discussion",
    ("in_discussion",              "discussion_done"):    "displayed",
    ("approved_pending_rationale", "rationale_received"): "approved",
    ("displayed",                  "archive_stale"):      "deferred",
    ("in_discussion",              "archive_stale"):      "deferred",
    ("approved_pending_rationale", "archive_stale"):      "deferred",
}


@dataclass
class TransitionResult:
    """状态转移结果。

    new_state:
        - 成功:目标状态
        - 失败:与传入 current_state 一致(不改)
    error:
        - 成功 = None
        - 失败 = 人话错误描述,可直接回到 TG
    side_effects:
        - 给 handlers 层的"建议清单",见各 transition 的语义
    """
    new_state: str
    error: str | None = None
    side_effects: dict[str, Any] = field(default_factory=dict)


def transition(current_state: str, action: str, payload: dict[str, Any] | None = None) -> TransitionResult:
    """执行一次状态转移。纯函数。

    payload 字段(取决于 action,可选):
    - click_approve:        {"actor_username": str}     → 记 decided_by
    - click_reject:         {"actor_username": str}
    - click_discuss:        {"actor_username": str}
    - discussion_done:      {}
    - rationale_received:   {"actor_username": str, "rationale": str}
    - archive_stale:        {}

    side_effects keys(handlers 层负责实现):
    - "edit_card":           dict {"append_text": str, "remove_buttons": bool}
    - "send_followup":       dict {"role": str, "text": str}
    - "call_llm_discussion": dict {"thread_id_hint": ...}  (handlers 自己组 prompt)
    - "write_ops_decision":  dict {"actor_username": ..., "rationale": ...}
    """
    payload = payload or {}

    if current_state not in STATES:
        return TransitionResult(
            new_state=current_state,
            error=f"unknown current_state {current_state!r}",
        )
    if action not in ACTIONS:
        return TransitionResult(
            new_state=current_state,
            error=f"unknown action {action!r}",
        )

    key = (current_state, action)
    new_state = _TRANSITIONS.get(key)
    if new_state is None:
        return TransitionResult(
            new_state=current_state,
            error=f"illegal transition: state={current_state!r} action={action!r} — 此消息可能已过期或已处理",
        )

    side_effects = _side_effects_for(action, payload)
    return TransitionResult(new_state=new_state, error=None, side_effects=side_effects)


def _side_effects_for(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    """根据 action 生成建议的 side effects(handlers 层翻译成实际调用)。"""
    if action == "send_displayed":
        # 推送时由 pusher 直接写 DB,不走 transition。这条 transition 主要给
        # 状态图完整性 + 测试用,实际不会被 handlers 触发。
        return {}

    if action == "click_approve":
        return {
            "edit_card": {
                "append_text": (
                    "\n\n**✅ 采用中** — 请直接 reply 此消息,写一句理由。"
                    "\n12 小时无回应将自动归档为 deferred。"
                ),
                "remove_buttons": True,
            },
        }

    if action == "click_reject":
        actor = payload.get("actor_username") or "unknown"
        return {
            "edit_card": {
                "append_text": f"\n\n**❌ 已否决** by @{actor}",
                "remove_buttons": True,
            },
        }

    if action == "click_discuss":
        return {
            "edit_card": {
                "append_text": "\n\n**💬 答疑中…** Facilitator 正在调 LLM,请稍候。",
                "remove_buttons": True,  # 临时移除,LLM 答完后还原
            },
            "call_llm_discussion": {},
        }

    if action == "discussion_done":
        # 答完后还原按钮,允许继续 ✅/❌
        return {
            "edit_card": {
                "append_text": "",  # handlers 会另外发 LLM 答疑消息;卡本身保持
                "remove_buttons": False,  # 还原原始 4 按钮
                "restore_buttons": True,
            },
        }

    if action == "rationale_received":
        rationale = payload.get("rationale") or ""
        actor = payload.get("actor_username") or "unknown"
        return {
            "edit_card": {
                "append_text": (
                    f"\n\n**✅ 已采纳** by @{actor}"
                    f"\nrationale: {rationale}"
                ),
                "remove_buttons": True,
            },
            "write_ops_decision": {
                "actor_username": actor,
                "rationale": rationale,
            },
        }

    if action == "archive_stale":
        return {
            "edit_card": {
                "append_text": "\n\n**⏰ 已归档为 deferred**(12 小时无回应)",
                "remove_buttons": True,
            },
        }

    return {}
