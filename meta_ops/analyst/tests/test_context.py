"""Phase 2 Analyst v0 Step 1 — context.py 单测。

覆盖 spec §2 Step 1 验收点:
  - gather_metrics 0 行 / 跨周聚合 / 缺 metrics 字段降级
  - gather_decisions 0 行 / 含历史
  - gather_run_log_health data_sparse 判断
  + 工具函数 parse_iso_week / previous_iso_week
"""
from __future__ import annotations
from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import uuid4
import pytest

from meta_ops.analyst.context import (
    gather_metrics,
    gather_decisions,
    gather_playbook,
    gather_run_log_health,
    parse_iso_week,
    previous_iso_week,
    iso_week_range_utc,
)


def _make_db_with_rows(*row_lists):
    """Build a MagicMock db whose cursor.fetchall() returns row_lists in sequence.

    Each call to db.cursor(...) returns a context manager whose __enter__ gives
    a cursor object with .execute(no-op) and .fetchall() popping from queue.
    """
    queue = list(row_lists)

    def cursor_factory(*args, **kwargs):
        cur = MagicMock()
        cur.fetchall.side_effect = lambda: queue.pop(0) if queue else []
        cur.execute = MagicMock()
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=cur)
        ctx.__exit__ = MagicMock(return_value=False)
        return ctx

    db = MagicMock()
    db.cursor = MagicMock(side_effect=cursor_factory)
    return db


# ── ISO week 工具 ──────────────────────────────────────────

def test_parse_iso_week_ok():
    assert parse_iso_week("2026W18") == (2026, 18)
    assert parse_iso_week("2026W01") == (2026, 1)


def test_parse_iso_week_rejects_bad_input():
    with pytest.raises(ValueError):
        parse_iso_week("2026-18")
    with pytest.raises(ValueError):
        parse_iso_week("not-a-week")


def test_previous_iso_week_basic():
    assert previous_iso_week("2026W18") == "2026W17"
    # year boundary: 2026W01 → 2025W?? (depends on ISO calendar)
    prev = previous_iso_week("2026W01")
    assert prev.startswith("2025W")


def test_iso_week_range_utc_is_one_week():
    start, end = iso_week_range_utc("2026W18")
    assert (end - start).days == 7
    assert start.tzinfo == timezone.utc


# ── gather_metrics ─────────────────────────────────────────

def test_gather_metrics_zero_rows():
    """All weeks empty → n_rows=0,by_subject_type={}, sample=[]."""
    db = _make_db_with_rows([], [], [], [], [])  # 5 weeks (current + 4)
    result = gather_metrics(db, current_week_iso="2026W18", weeks_back=4)
    assert result["current_week"] == "2026W18"
    assert len(result["weeks"]) == 5
    for w in result["weeks"]:
        assert w["n_rows"] == 0
        assert w["by_subject_type"] == {}
        assert w["sample"] == []


def test_gather_metrics_aggregates_subject_types():
    """One week with 3 articles + 2 topics → by_subject_type counts."""
    rows_w18 = [
        {"subject_type": "article", "subject_id": "a1",
         "snapshot_at": datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc),
         "metrics": {"views": 100, "ctr": 0.1}, "source": "pulse_db"},
        {"subject_type": "article", "subject_id": "a2",
         "snapshot_at": datetime(2026, 4, 28, 13, 0, tzinfo=timezone.utc),
         "metrics": {"views": 200, "ctr": 0.12}, "source": "pulse_db"},
        {"subject_type": "article", "subject_id": "a3",
         "snapshot_at": datetime(2026, 4, 28, 14, 0, tzinfo=timezone.utc),
         "metrics": {"views": 50}, "source": "pulse_db"},
        {"subject_type": "topic", "subject_id": "t1",
         "snapshot_at": datetime(2026, 4, 28, 15, 0, tzinfo=timezone.utc),
         "metrics": {"approval_rate": 0.7}, "source": "computed"},
        {"subject_type": "topic", "subject_id": "t2",
         "snapshot_at": datetime(2026, 4, 28, 16, 0, tzinfo=timezone.utc),
         "metrics": {"approval_rate": 0.8}, "source": "computed"},
    ]
    db = _make_db_with_rows(rows_w18, [], [], [], [])
    result = gather_metrics(db, current_week_iso="2026W18", weeks_back=4)

    cur_week = result["weeks"][0]
    assert cur_week["week"] == "2026W18"
    assert cur_week["n_rows"] == 5
    assert cur_week["by_subject_type"] == {"article": 3, "topic": 2}
    assert "views" in cur_week["metric_keys_seen"]
    assert "ctr" in cur_week["metric_keys_seen"]
    assert "approval_rate" in cur_week["metric_keys_seen"]
    assert len(cur_week["sample"]) == 5


def test_gather_metrics_handles_non_dict_metrics():
    """Watcher quirk:metrics 字段意外是 None 或字符串 → 不崩,跳过 key 收集。"""
    rows = [
        {"subject_type": "article", "subject_id": "a1",
         "snapshot_at": datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc),
         "metrics": None, "source": "pulse_db"},
        {"subject_type": "article", "subject_id": "a2",
         "snapshot_at": datetime(2026, 4, 28, 13, 0, tzinfo=timezone.utc),
         "metrics": "broken_string", "source": "pulse_db"},
    ]
    db = _make_db_with_rows(rows, [], [], [], [])
    result = gather_metrics(db, current_week_iso="2026W18", weeks_back=4)
    cur = result["weeks"][0]
    assert cur["n_rows"] == 2
    assert cur["by_subject_type"] == {"article": 2}
    assert cur["metric_keys_seen"] == []  # 没收集到任何 dict key


def test_gather_metrics_sample_capped():
    """sample_size_per_week 限制返回的 sample 长度。"""
    rows = [
        {"subject_type": "article", "subject_id": f"a{i}",
         "snapshot_at": datetime(2026, 4, 28, 12, i, tzinfo=timezone.utc),
         "metrics": {"views": i}, "source": "pulse_db"}
        for i in range(20)
    ]
    db = _make_db_with_rows(rows, [], [], [], [])
    result = gather_metrics(db, current_week_iso="2026W18", weeks_back=4,
                            sample_size_per_week=3)
    cur = result["weeks"][0]
    assert cur["n_rows"] == 20
    assert len(cur["sample"]) == 3


# ── gather_decisions ───────────────────────────────────────

def test_gather_decisions_zero_rows():
    db = _make_db_with_rows([])
    result = gather_decisions(db, weeks_back=4)
    assert result == []


def test_gather_decisions_serializes_dt():
    decided = datetime(2026, 4, 30, 10, 0, tzinfo=timezone.utc)
    rows = [
        {"id": uuid4(), "tenant_id": "history", "decided_at": decided,
         "decision_type": "prompt_change", "subject": "title_T1",
         "rationale": "test", "evidence": [], "verification_plan": "watch CTR",
         "status": "active"},
    ]
    db = _make_db_with_rows(rows)
    result = gather_decisions(db, weeks_back=4)
    assert len(result) == 1
    assert result[0]["subject"] == "title_T1"
    assert result[0]["decided_at"] == decided.isoformat()
    assert isinstance(result[0]["id"], str)


# ── gather_run_log_health ──────────────────────────────────

def test_gather_run_log_health_data_sparse_true_when_few_watcher_runs():
    rows = [
        {"kind": "watcher", "status": "ok"},
        {"kind": "watcher", "status": "ok"},
        {"kind": "watcher", "status": "partial"},
        # 3 successful watcher runs < 5 → data_sparse=True
        {"kind": "librarian", "status": "ok"},
    ]
    db = _make_db_with_rows(rows)
    result = gather_run_log_health(db, days=7)
    assert result["watcher_ok_runs_7d"] == 3
    assert result["data_sparse"] is True
    assert result["librarian_ok_runs_7d"] == 1


def test_gather_run_log_health_data_sparse_false_when_5plus_watcher_runs():
    rows = [{"kind": "watcher", "status": "ok"} for _ in range(5)]
    db = _make_db_with_rows(rows)
    result = gather_run_log_health(db, days=7)
    assert result["watcher_ok_runs_7d"] == 5
    assert result["data_sparse"] is False


# ── gather_playbook ────────────────────────────────────────

def test_gather_playbook_returns_active_high_confidence():
    rows = [
        {"id": uuid4(), "tenant_id": "history", "pattern_name": "alt-titles",
         "context": "low CTR weeks", "recipe": "rotate 3 templates",
         "confidence": 0.8, "last_validated_at": None, "status": "active"},
    ]
    db = _make_db_with_rows(rows)
    result = gather_playbook(db)
    assert len(result) == 1
    assert result[0]["pattern_name"] == "alt-titles"
    assert result[0]["confidence"] == 0.8
