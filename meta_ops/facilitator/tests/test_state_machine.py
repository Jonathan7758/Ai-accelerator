"""Phase 3 Step 5: state_machine.transition 单元测试。

覆盖:
1-7  每条合法 transition 各测一遍
8    illegal transition 在终态(approved/rejected/deferred)上拒绝
9    illegal action(未知 action 名)拒绝
10   unknown current_state 拒绝
11   click_approve 的 side_effects 字段齐全
12   rationale_received 的 side_effects 携带 actor + rationale
"""
from __future__ import annotations

import pytest

from meta_ops.facilitator import state_machine as sm


def test_proposed_to_displayed():
    r = sm.transition("proposed", "send_displayed")
    assert r.new_state == "displayed"
    assert r.error is None


def test_displayed_to_approved_pending_rationale():
    r = sm.transition("displayed", "click_approve", {"actor_username": "jonathan"})
    assert r.new_state == "approved_pending_rationale"
    assert r.error is None
    assert r.side_effects["edit_card"]["remove_buttons"] is True


def test_displayed_to_rejected():
    r = sm.transition("displayed", "click_reject", {"actor_username": "jonathan"})
    assert r.new_state == "rejected"
    assert r.error is None
    assert "@jonathan" in r.side_effects["edit_card"]["append_text"]


def test_displayed_to_in_discussion():
    r = sm.transition("displayed", "click_discuss")
    assert r.new_state == "in_discussion"
    assert r.error is None
    assert "call_llm_discussion" in r.side_effects


def test_in_discussion_back_to_displayed():
    r = sm.transition("in_discussion", "discussion_done")
    assert r.new_state == "displayed"
    assert r.error is None


def test_approved_pending_rationale_to_approved():
    r = sm.transition(
        "approved_pending_rationale",
        "rationale_received",
        {"actor_username": "jonathan", "rationale": "T2 已用 3 周,该轮换了"},
    )
    assert r.new_state == "approved"
    assert r.error is None
    assert r.side_effects["write_ops_decision"]["rationale"] == "T2 已用 3 周,该轮换了"
    assert r.side_effects["write_ops_decision"]["actor_username"] == "jonathan"


@pytest.mark.parametrize("from_state", ["displayed", "in_discussion", "approved_pending_rationale"])
def test_archive_stale_paths(from_state: str):
    r = sm.transition(from_state, "archive_stale")
    assert r.new_state == "deferred"
    assert r.error is None
    assert "deferred" in r.side_effects["edit_card"]["append_text"]


@pytest.mark.parametrize("terminal", ["approved", "rejected", "deferred"])
def test_terminal_states_reject_all_actions(terminal: str):
    """终态不接受任何 action(包括 archive_stale)— 已经结束的不能再变。"""
    for action in sm.ACTIONS:
        r = sm.transition(terminal, action)
        assert r.new_state == terminal, f"{terminal} should stay on action={action}"
        assert r.error is not None
        assert "illegal" in r.error.lower() or "已过期" in r.error or "已处理" in r.error


def test_unknown_action_rejected():
    r = sm.transition("displayed", "fly_to_moon")
    assert r.new_state == "displayed"
    assert r.error is not None
    assert "unknown action" in r.error


def test_unknown_state_rejected():
    r = sm.transition("flying", "click_approve")
    assert r.new_state == "flying"
    assert r.error is not None
    assert "unknown current_state" in r.error


def test_double_click_approve_rejected():
    """已经 approved_pending_rationale 的 thread,再点 ✅ 应被拒。"""
    r = sm.transition("approved_pending_rationale", "click_approve")
    assert r.new_state == "approved_pending_rationale"
    assert r.error is not None


def test_rationale_without_pending_rejected():
    """displayed 状态下收到 reply 不应转去 approved。"""
    r = sm.transition("displayed", "rationale_received", {"rationale": "x"})
    assert r.new_state == "displayed"
    assert r.error is not None
