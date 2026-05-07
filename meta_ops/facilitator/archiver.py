"""Phase 3 Step 6: 决策线程 12h 自动归档器。

定期(每小时)扫描 ops_decision_threads,把超 N 小时仍未推进到终态的 thread
归档为 'deferred',并刷新对应 TG 消息(移按钮 + reply 一条归档通知)。

调用方:
- systemd `acc-facilitator-archiver.timer`(每小时,自动)
- `acc facilitator archive [--hours N] [--dry-run]`(手动 / 测试)

设计:
- 状态机层:复用 state_machine.transition(state, 'archive_stale')—— 三态都允许
- DB 层:UPDATE state='deferred',逐行原子(state guard 防并发)
- TG 层:edit_message_reply_markup(remove buttons) + send_message(reply 到原卡)
  保留原决策卡正文(不动 text),仅去按钮 + 加底注;比覆盖原文更友好
- 失败容错:单条 TG / DB 失败不阻塞其他;返结构化 result + partial_reasons
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from telegram import Bot
from telegram.error import TelegramError

from meta_ops.common.db import get_local_db
from meta_ops.facilitator import state_machine as sm
from meta_ops.facilitator.bots import ROLE_DISPLAY, ROLE_EMOJI

log = logging.getLogger(__name__)

DEFAULT_STALE_HOURS = 12

# 与 state_machine._TRANSITIONS 中 archive_stale 的合法 from-state 一致
STALE_STATES: tuple[str, ...] = (
    "displayed",
    "in_discussion",
    "approved_pending_rationale",
)


def _select_stale_threads(conn, hours: int) -> list[dict[str, Any]]:
    """查询当前满足"超 N 小时未终态"的 thread。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, state, tg_chat_id, tg_message_id,
                   report_week, candidate_index, created_at
              FROM ops_decision_threads
             WHERE state = ANY(%s)
               AND created_at < NOW() - (%s || ' hours')::interval
             ORDER BY created_at ASC
            """,
            (list(STALE_STATES), str(hours)),
        )
        cols = ["id", "state", "tg_chat_id", "tg_message_id",
                "report_week", "candidate_index", "created_at"]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _archive_one_in_db(conn, thread_id: str, current_state: str) -> dict[str, Any]:
    """走状态机 + UPDATE,带 state guard 防并发。"""
    result = sm.transition(current_state, "archive_stale")
    if result.error:
        return {"ok": False, "reason": result.error}
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ops_decision_threads
               SET state = %s, updated_at = NOW()
             WHERE id = %s AND state = %s
            """,
            (result.new_state, thread_id, current_state),
        )
        if cur.rowcount != 1:
            return {
                "ok": False,
                "reason": f"UPDATE rowcount={cur.rowcount} (state changed concurrently)",
            }
    return {"ok": True, "new_state": result.new_state}


async def _refresh_tg(
    bot: Bot,
    chat_id: int | None,
    message_id: int | None,
    thread_id: str,
) -> dict[str, Any]:
    """编辑 TG:移按钮 + 发归档通知 reply。

    chat_id/message_id 为 None → 跳过(从未推过 TG,理论不可能;防御)。
    单步失败不致命:返 warning 不抛。
    """
    out: dict[str, Any] = {"buttons_removed": False, "notice_sent": False}

    if chat_id is None or message_id is None:
        out["skip_reason"] = "tg_chat_id/message_id is NULL"
        return out

    try:
        await bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=None,
        )
        out["buttons_removed"] = True
    except TelegramError as e:
        out["edit_warning"] = str(e)
        log.warning("archiver: edit_reply_markup failed for thread %s: %s", thread_id, e)

    notice = (
        f"{ROLE_EMOJI['facilitator']} {ROLE_DISPLAY['facilitator']}: "
        f"决策 #{thread_id[:8]} ⏰ 已自动归档为 deferred(超 12h 无回应)。"
    )
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=notice,
            reply_to_message_id=message_id,
        )
        out["notice_sent"] = True
    except TelegramError as e:
        out["notice_warning"] = str(e)
        log.warning("archiver: send notice failed for thread %s: %s", thread_id, e)

    return out


async def _process_all(
    bot: Bot,
    conn,
    stale: list[dict[str, Any]],
    dry_run: bool,
) -> dict[str, Any]:
    archived = 0
    skipped: list[dict[str, Any]] = []
    tg_warnings: list[dict[str, Any]] = []

    for row in stale:
        thread_id = str(row["id"])
        if dry_run:
            log.info(
                "[dry-run] would archive thread %s (state=%s, msg_id=%s, week=%s, idx=%s)",
                thread_id, row["state"], row["tg_message_id"],
                row["report_week"], row["candidate_index"],
            )
            archived += 1
            continue

        db_result = _archive_one_in_db(conn, thread_id, row["state"])
        if not db_result["ok"]:
            skipped.append({"thread_id": thread_id, "reason": db_result["reason"]})
            continue

        # TG 失败不回滚 DB:archive 是事实,通知到不到次要
        tg_result = await _refresh_tg(
            bot=bot,
            chat_id=row["tg_chat_id"],
            message_id=row["tg_message_id"],
            thread_id=thread_id,
        )
        if tg_result.get("edit_warning") or tg_result.get("notice_warning") or tg_result.get("skip_reason"):
            tg_warnings.append({"thread_id": thread_id, **tg_result})
        archived += 1

    if not dry_run:
        conn.commit()

    return {
        "archived": archived,
        "skipped": skipped,
        "tg_warnings": tg_warnings,
    }


def archive_stale_threads(
    hours: int = DEFAULT_STALE_HOURS,
    dry_run: bool = False,
) -> dict[str, Any]:
    """主入口。返结构化 dict。

    status 取值:
    - "ok"        — 没有 stale,或全部 archive 成功
    - "partial"   — 部分 thread DB 转移失败(并发?)
    - "failed"    — 系统性失败(如 .env 缺 token)
    """
    result: dict[str, Any] = {
        "hours": hours,
        "dry_run": dry_run,
        "status": "ok",
        "stale_count": 0,
        "archived": 0,
    }

    conn = get_local_db()
    try:
        stale = _select_stale_threads(conn, hours)
        result["stale_count"] = len(stale)
        log.info("archiver: %d stale threads at threshold %dh", len(stale), hours)

        if not stale:
            return result

        token = os.getenv("TG_BOT_TOKEN_ANALYST")
        if not token:
            result["status"] = "failed"
            result["reason"] = "TG_BOT_TOKEN_ANALYST not set"
            log.error(result["reason"])
            return result

        bot = Bot(token=token)
        async def _runner() -> dict[str, Any]:
            async with bot:
                return await _process_all(bot, conn, stale, dry_run=dry_run)

        send_result = asyncio.run(_runner())
        result.update(send_result)

        if send_result.get("skipped"):
            result["status"] = "partial"
            result["partial_reasons"] = [
                f"[degraded] thread {s['thread_id']}: {s['reason']}"
                for s in send_result["skipped"]
            ]
    finally:
        conn.close()

    return result
