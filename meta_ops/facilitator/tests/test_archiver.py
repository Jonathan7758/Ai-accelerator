"""Phase 3 Step 6: archiver 轻单测。

只覆盖纯函数 / SQL 列映射;真正的端到端走生产 DB 实跑(spec Step 6 验收节)。

state_machine.transition('<stale>', 'archive_stale') 的 9 transition 已在
test_state_machine.py::test_archive_stale_paths 覆盖,这里不重测。
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from meta_ops.facilitator import archiver


class _FakeCursor:
    """模拟 psycopg2 cursor 上下文管理器,捕获 execute 参数 + 提供 fetchall。"""

    def __init__(self, fetchall_rows: list[tuple]):
        self.fetchall_rows = fetchall_rows
        self.executed: list[tuple[str, tuple]] = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "UPDATE" in sql:
            self.rowcount = 1

    def fetchall(self):
        return self.fetchall_rows


class _FakeConn:
    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor
        self.committed = False
        self.closed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


def test_select_stale_threads_uses_correct_states_and_columns():
    cur = _FakeCursor(fetchall_rows=[])
    conn = _FakeConn(cur)

    rows = archiver._select_stale_threads(conn, hours=12)

    assert rows == []
    assert len(cur.executed) == 1
    sql, params = cur.executed[0]
    # SQL 必须基于 STALE_STATES + interval
    assert "ops_decision_threads" in sql
    assert "state = ANY" in sql
    assert "created_at < NOW()" in sql
    # params: (states_list, hours_str)
    assert params[0] == list(archiver.STALE_STATES)
    assert params[1] == "12"


def test_select_stale_threads_maps_columns():
    """fetchall 返 7 列 tuple → dict[7 keys]。"""
    fake_row = (
        "uuid-1",
        "displayed",
        -1003926659115,
        15,
        "2026W19",
        3,
        "2026-05-04 11:41:22+08",
    )
    cur = _FakeCursor(fetchall_rows=[fake_row])
    conn = _FakeConn(cur)

    rows = archiver._select_stale_threads(conn, hours=12)

    assert len(rows) == 1
    r = rows[0]
    assert r["id"] == "uuid-1"
    assert r["state"] == "displayed"
    assert r["tg_chat_id"] == -1003926659115
    assert r["tg_message_id"] == 15
    assert r["report_week"] == "2026W19"
    assert r["candidate_index"] == 3


def test_archive_one_in_db_happy_path():
    cur = _FakeCursor(fetchall_rows=[])
    conn = _FakeConn(cur)

    result = archiver._archive_one_in_db(conn, "uuid-1", "displayed")

    assert result == {"ok": True, "new_state": "deferred"}
    assert len(cur.executed) == 1
    sql, params = cur.executed[0]
    assert "UPDATE ops_decision_threads" in sql
    assert params == ("deferred", "uuid-1", "displayed")


def test_archive_one_in_db_rejects_terminal_state():
    """approved / rejected / deferred — state_machine 拒,archiver 不写 DB。"""
    cur = _FakeCursor(fetchall_rows=[])
    conn = _FakeConn(cur)

    for terminal in ("approved", "rejected", "deferred"):
        result = archiver._archive_one_in_db(conn, "uuid-x", terminal)
        assert result["ok"] is False
        assert "illegal" in result["reason"].lower() or "已过期" in result["reason"] or "已处理" in result["reason"]
    # 拒绝的都不应触发 UPDATE
    assert cur.executed == []


def test_archive_stale_threads_no_stale_short_circuits():
    """没有 stale → 不调 TG bot,不需要 token。"""
    with patch.object(archiver, "get_local_db") as mock_db:
        cur = _FakeCursor(fetchall_rows=[])
        conn = _FakeConn(cur)
        mock_db.return_value = conn

        # 故意清掉 token,确保短路时也不会触发
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TG_BOT_TOKEN_ANALYST", None)
            result = archiver.archive_stale_threads(hours=12, dry_run=False)

    assert result["status"] == "ok"
    assert result["stale_count"] == 0
    assert result["archived"] == 0
    assert "reason" not in result  # 没触发 token 检查
    assert conn.closed is True


def test_archive_stale_threads_dry_run_skips_db_writes_and_tg():
    """dry_run=True 时 DB UPDATE / TG 都不该跑;只 SELECT + log。"""
    fake_row = (
        "uuid-A", "displayed", -1003926659115, 15,
        "2026W19", 3, "2026-05-04 11:41:22+08",
    )
    cur = _FakeCursor(fetchall_rows=[fake_row])
    conn = _FakeConn(cur)

    with patch.object(archiver, "get_local_db", return_value=conn), \
         patch.dict(os.environ, {"TG_BOT_TOKEN_ANALYST": "fake:token"}), \
         patch.object(archiver, "Bot") as mock_bot_cls:
        # asyncio.run 会跑 _process_all,但 dry_run 短路前不该触 bot 方法
        result = archiver.archive_stale_threads(hours=12, dry_run=True)

    assert result["status"] == "ok"
    assert result["stale_count"] == 1
    assert result["archived"] == 1
    assert conn.committed is False  # dry_run 不 commit
    # 只跑了 SELECT,没跑 UPDATE
    assert all("UPDATE" not in sql for sql, _ in cur.executed)
    mock_bot_cls.assert_called_once()  # Bot 被实例化(因有 stale)
    # 但 bot 实例上的方法不该被调
    bot_instance = mock_bot_cls.return_value
    bot_instance.edit_message_reply_markup.assert_not_called()
    bot_instance.send_message.assert_not_called()
