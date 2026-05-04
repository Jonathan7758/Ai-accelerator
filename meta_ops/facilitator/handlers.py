"""Phase 3 Step 5: Facilitator handlers — TG callback / message → state machine → DB / TG。

handlers 层 = 状态机的"司机":
- 监听 CallbackQueryHandler(`dec:apv|rej|dsc|full:<uuid>` / `rep:full:<week>`)
- 监听 MessageHandler(reply 到决策卡 → 写 ops_decisions)
- 调 state_machine.transition() 算 new_state + side_effects
- 翻译 side_effects 成实际 TG / DB 调用

放在 **analyst bot 的 Application** 上(因为决策卡是 analyst 发的,
按钮 callback 和 reply 都路由给 analyst bot)。
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from meta_ops.common.db import get_local_db
from meta_ops.facilitator import state_machine as sm
from meta_ops.facilitator.bots import ROLE_DISPLAY, ROLE_EMOJI

log = logging.getLogger(__name__)

REPORTS_ROOT = Path("/opt/accelerator/reports")
EXTRACTED_ROOT = Path("/opt/accelerator/knowledge/pulse/extracted")

# LLM 答疑控制
DISCUSSION_MAX_TOKENS = 800       # 200-500 字答疑(中文 ~ 1.5 token/字)
DISCUSSION_MAX_EXTRACTED_CHARS = 12000  # 限制总 prompt 体积


# ─────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────

def _fetch_thread(thread_id: str) -> dict[str, Any] | None:
    """读单条 thread。返回 dict;找不到返 None。"""
    conn = get_local_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, report_week, candidate_index,
                       decision_type, subject, rationale_proposed,
                       verification_plan, risk, evidence,
                       state, tg_chat_id, tg_message_id,
                       user_rationale, decided_by, ops_decision_id
                  FROM ops_decision_threads
                 WHERE id = %s
                """,
                (thread_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            cols = [
                "id", "report_week", "candidate_index",
                "decision_type", "subject", "rationale_proposed",
                "verification_plan", "risk", "evidence",
                "state", "tg_chat_id", "tg_message_id",
                "user_rationale", "decided_by", "ops_decision_id",
            ]
            return dict(zip(cols, row))
    finally:
        conn.close()


def _fetch_thread_by_card(chat_id: int, message_id: int) -> dict[str, Any] | None:
    conn = get_local_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, state
                  FROM ops_decision_threads
                 WHERE tg_chat_id = %s AND tg_message_id = %s
                 ORDER BY created_at DESC LIMIT 1
                """,
                (chat_id, message_id),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return {"id": str(row[0]), "state": row[1]}
    finally:
        conn.close()


def _update_thread_state(thread_id: str, new_state: str, *, decided_by: str | None = None) -> None:
    conn = get_local_db()
    try:
        with conn.cursor() as cur:
            if decided_by is not None:
                cur.execute(
                    """
                    UPDATE ops_decision_threads
                       SET state = %s,
                           decided_by = COALESCE(decided_by, %s),
                           decided_at = COALESCE(decided_at, NOW()),
                           updated_at = NOW()
                     WHERE id = %s
                    """,
                    (new_state, decided_by, thread_id),
                )
            else:
                cur.execute(
                    "UPDATE ops_decision_threads SET state = %s, updated_at = NOW() WHERE id = %s",
                    (new_state, thread_id),
                )
        conn.commit()
    finally:
        conn.close()


def _write_ops_decision_and_link(
    thread: dict[str, Any], rationale: str, actor: str
) -> str:
    """成功 ✅ 后:
    1) INSERT ops_decisions (status='active' + 6 字段) → 拿 ops_decision_id
    2) UPDATE thread state='approved' + user_rationale + decided_by + ops_decision_id
    返回 ops_decision_id (str)。
    """
    conn = get_local_db()
    try:
        with conn.cursor() as cur:
            # 1) ops_decisions
            cur.execute(
                """
                INSERT INTO ops_decisions
                    (decision_type, subject, before_state, after_state,
                     rationale, evidence, verification_plan, status, decided_at)
                VALUES (%s, %s, NULL, NULL, %s, %s, %s, 'active', NOW())
                RETURNING id
                """,
                (
                    thread["decision_type"] or "other",
                    thread["subject"] or "(unknown)",
                    rationale,  # 用户的 rationale 权威,不用 rationale_proposed
                    json.dumps(thread.get("evidence") or [], ensure_ascii=False),
                    thread["verification_plan"],
                ),
            )
            ops_decision_id = str(cur.fetchone()[0])

            # 2) thread state 收尾
            cur.execute(
                """
                UPDATE ops_decision_threads
                   SET state = 'approved',
                       user_rationale = %s,
                       decided_by = COALESCE(decided_by, %s),
                       decided_at = COALESCE(decided_at, NOW()),
                       ops_decision_id = %s,
                       updated_at = NOW()
                 WHERE id = %s
                """,
                (rationale, actor, ops_decision_id, thread["id"]),
            )
        conn.commit()
        return ops_decision_id
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────
# TG message helpers
# ─────────────────────────────────────────────────────────────────

def _decision_keyboard(thread_id: str) -> InlineKeyboardMarkup:
    """决策卡的 4 按钮(还原用)。与 pusher._build_decision_keyboard 保持一致。"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ 采用", callback_data=f"dec:apv:{thread_id}"),
            InlineKeyboardButton("❌ 否决", callback_data=f"dec:rej:{thread_id}"),
        ],
        [
            InlineKeyboardButton("💬 讨论", callback_data=f"dec:dsc:{thread_id}"),
            InlineKeyboardButton("📄 全文", callback_data=f"dec:full:{thread_id}"),
        ],
    ])


async def _apply_card_edit(
    update: Update,
    thread_id: str,
    side: dict[str, Any],
) -> None:
    """根据 side_effects['edit_card'] 修改决策卡。"""
    edit = side.get("edit_card") or {}
    append_text = edit.get("append_text") or ""
    remove_buttons = edit.get("remove_buttons", False)
    restore_buttons = edit.get("restore_buttons", False)

    msg = update.callback_query.message
    if msg is None:
        log.warning("_apply_card_edit: callback_query has no message; skip")
        return

    new_text = msg.text or ""
    if append_text:
        new_text = new_text + append_text

    if restore_buttons:
        markup: InlineKeyboardMarkup | None = _decision_keyboard(thread_id)
    elif remove_buttons:
        markup = None
    else:
        markup = msg.reply_markup

    try:
        await msg.edit_text(text=new_text, reply_markup=markup)
    except TelegramError as e:
        log.warning("edit_text failed: %s", e)


def _facilitator_header() -> str:
    return f"{ROLE_EMOJI['facilitator']} {ROLE_DISPLAY['facilitator']}:"


def _analyst_header() -> str:
    return f"{ROLE_EMOJI['analyst']} {ROLE_DISPLAY['analyst']}:"


# ─────────────────────────────────────────────────────────────────
# Callback query handlers
# ─────────────────────────────────────────────────────────────────

async def on_callback_decision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 dec:<action>:<uuid> 三种点击 + dec:full:<uuid> 推全文。"""
    cq = update.callback_query
    if cq is None or cq.data is None:
        return
    await cq.answer()  # 立即 ack,避免按钮一直转圈

    parts = cq.data.split(":")
    if len(parts) != 3 or parts[0] != "dec":
        log.warning("unknown callback_data: %r", cq.data)
        return
    _, action_short, thread_id = parts

    actor = (cq.from_user.username if cq.from_user else None) or "unknown"

    # full: 不进状态机,直接推 evidence JSON
    if action_short == "full":
        await _send_decision_full(update, context, thread_id)
        return

    action_map = {"apv": "click_approve", "rej": "click_reject", "dsc": "click_discuss"}
    action = action_map.get(action_short)
    if action is None:
        log.warning("unknown decision action: %r", action_short)
        return

    thread = _fetch_thread(thread_id)
    if thread is None:
        await cq.message.reply_text(f"{_facilitator_header()} 找不到该决策(thread_id={thread_id[:8]}…)")
        return

    result = sm.transition(thread["state"], action, {"actor_username": actor})
    if result.error:
        await cq.message.reply_text(f"{_facilitator_header()} {result.error}")
        return

    # 写 DB(state + decided_by)
    _update_thread_state(thread_id, result.new_state, decided_by=actor)

    # 应用 edit_card
    await _apply_card_edit(update, thread_id, result.side_effects)

    # discuss 分支:调 LLM 答疑,然后 transition discussion_done → 还原按钮
    if action == "click_discuss":
        await _run_discussion_followup(update, context, thread)


async def on_callback_report_full(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 rep:full:<week> — 上传 reports/<week>.md 作为 TG document(决策 B)。"""
    cq = update.callback_query
    if cq is None or cq.data is None:
        return
    await cq.answer()
    parts = cq.data.split(":")
    if len(parts) != 3 or parts[0] != "rep" or parts[1] != "full":
        return
    week = parts[2]
    report_path = REPORTS_ROOT / f"{week}.md"
    if not report_path.exists():
        await cq.message.reply_text(f"{_analyst_header()} reports/{week}.md 不存在")
        return
    try:
        with report_path.open("rb") as f:
            await context.bot.send_document(
                chat_id=cq.message.chat_id,
                document=f,
                filename=f"{week}.md",
                caption=f"{_analyst_header()} 周报 {week} 全文",
            )
    except TelegramError as e:
        log.exception("send_document failed")
        await cq.message.reply_text(f"{_analyst_header()} 推送全文失败: {e}")


async def _send_decision_full(
    update: Update, context: ContextTypes.DEFAULT_TYPE, thread_id: str
) -> None:
    """决策卡的 📄 按钮:推该决策的 evidence 数组 JSON(决策 B)。"""
    thread = _fetch_thread(thread_id)
    if thread is None:
        await update.callback_query.message.reply_text(
            f"{_facilitator_header()} 找不到该决策(thread_id={thread_id[:8]}…)"
        )
        return
    evidence = thread.get("evidence") or []
    pretty = json.dumps(evidence, ensure_ascii=False, indent=2)
    text = (
        f"{_analyst_header()} 决策 #{thread_id[:8]} evidence ({len(evidence)} 条)\n"
        f"```json\n{pretty}\n```"
    )
    if len(text) > 3800:
        text = text[:3700] + "\n…(已截断)```"
    await update.callback_query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ─────────────────────────────────────────────────────────────────
# Discussion (💬) — 单轮 LLM (策略 C2)
# ─────────────────────────────────────────────────────────────────

def _load_extracted_topics_referenced(evidence: list[dict[str, Any]]) -> dict[str, str]:
    """从 evidence 数组里抽 type=='extracted' 的 topic,加载对应文件。

    返回 {topic_name: file_text},按顺序去重。
    若文件读失败或不存在,跳过(不抛)。
    """
    topics: list[str] = []
    seen: set[str] = set()
    for e in evidence or []:
        if not isinstance(e, dict):
            continue
        if e.get("type") == "extracted":
            t = e.get("topic")
            if isinstance(t, str) and t not in seen:
                seen.add(t)
                topics.append(t)

    out: dict[str, str] = {}
    total = 0
    for topic in topics:
        path = EXTRACTED_ROOT / f"{topic}.md"
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        # 防爆体积:整体超 12K 截断
        remaining = DISCUSSION_MAX_EXTRACTED_CHARS - total
        if remaining <= 0:
            break
        if len(text) > remaining:
            text = text[:remaining] + "\n…(截断)"
        out[topic] = text
        total += len(text)
    return out


def _build_discussion_prompt(thread: dict[str, Any]) -> tuple[str, str]:
    """组 system + user。返回 (system, user)。"""
    evidence = thread.get("evidence") or []
    topics = _load_extracted_topics_referenced(evidence)

    system_parts = [
        "你是 Accelerator 系统中的 Facilitator,职责是回答 Jonathan 关于某条候选决策的提问。",
        "约束:",
        "- 仅基于下方提供的 evidence 与 extracted/ 主题文件回答,不得编造数据",
        "- 答 200-500 字中文,不写客套话",
        "- 不替 Jonathan 拍板,只澄清事实、补充上下文、列出权衡",
        "- 若 evidence 不足以回答,直说 '数据不足' 并指出缺什么",
    ]
    if topics:
        system_parts.append("\n## 引用的 extracted/ 主题文件\n")
        for name, content in topics.items():
            system_parts.append(f"\n### {name}\n{content}\n")
    system = "\n".join(system_parts)

    user = (
        f"# 候选决策 #{str(thread['id'])[:8]}\n"
        f"- decision_type: {thread['decision_type']}\n"
        f"- subject: {thread['subject']}\n"
        f"- rationale (Analyst 写的): {thread['rationale_proposed']}\n"
        f"- verification_plan: {thread['verification_plan']}\n"
        f"- risk: {thread.get('risk') or '(未填)'}\n\n"
        f"## evidence ({len(evidence)} 条)\n"
        f"```json\n{json.dumps(evidence, ensure_ascii=False, indent=2)}\n```\n\n"
        "## 我的提问\n"
        "为什么这么建议?有没有更稳的替代方案?这条决策最大的盲点是什么?"
    )
    return system, user


async def _run_discussion_followup(
    update: Update, context: ContextTypes.DEFAULT_TYPE, thread: dict[str, Any]
) -> None:
    """调 LLM 答疑 + 还原按钮(transition discussion_done)。"""
    cq = update.callback_query

    # 调 LLM(同步函数,放线程池跑避免阻塞 polling)
    import asyncio
    from meta_ops.librarian import llm_client

    system, user = _build_discussion_prompt(thread)

    def _call() -> dict[str, Any]:
        return llm_client.call_claude(
            kind="facilitator",
            target_path=f"thread/{thread['id']}",
            prompt_template="phase3_discussion_v0",
            system=system,
            user=user,
            max_tokens=DISCUSSION_MAX_TOKENS,
        )

    try:
        result = await asyncio.to_thread(_call)
    except Exception:
        log.exception("LLM discussion call crashed")
        result = {"ok": False, "error": "LLM call crashed", "response_text": None}

    answer_text = (result.get("response_text") or "").strip() or "(LLM 无回应)"
    if not result.get("ok"):
        answer_text = f"⚠️ LLM 调用失败: {result.get('error')}"

    cost = result.get("cost_usd") or 0.0
    footer = f"\n\n— LLM 答疑(约 ${cost:.4f},单轮)"

    chat_id = cq.message.chat_id
    full_msg = f"{_facilitator_header()} 关于决策 #{str(thread['id'])[:8]}\n\n{answer_text}{footer}"
    if len(full_msg) > 3800:
        full_msg = full_msg[:3700] + "\n…(截断)"

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=full_msg,
            reply_to_message_id=cq.message.message_id,
        )
    except TelegramError:
        log.exception("LLM answer send failed")

    # transition: in_discussion → displayed,还原按钮
    after = sm.transition("in_discussion", "discussion_done")
    _update_thread_state(thread["id"], after.new_state)
    await _apply_card_edit(update, thread["id"], after.side_effects)


# ─────────────────────────────────────────────────────────────────
# Reply MessageHandler — 收 rationale (策略 A1)
# ─────────────────────────────────────────────────────────────────

async def on_reply_rationale(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """用户 reply 决策卡 → 写 rationale → ops_decisions。

    匹配条件:
    1) 是 reply(reply_to_message 非空)
    2) reply 的目标 message_id 对应某个 thread.tg_message_id
    3) 该 thread 当前 state == 'approved_pending_rationale'
    其他情况忽略(可能是其它消息的 reply,不该误触)。
    """
    msg = update.message
    if msg is None or msg.reply_to_message is None:
        return
    if msg.text is None:
        return  # 只处理文字 rationale;图片/语音忽略

    chat_id = msg.chat_id
    target_msg_id = msg.reply_to_message.message_id

    found = _fetch_thread_by_card(chat_id, target_msg_id)
    if found is None:
        # reply 到的不是已知决策卡,忽略
        return

    thread = _fetch_thread(found["id"])
    if thread is None:
        return

    actor = (msg.from_user.username if msg.from_user else None) or "unknown"
    rationale = msg.text.strip()

    result = sm.transition(
        thread["state"],
        "rationale_received",
        {"actor_username": actor, "rationale": rationale},
    )
    if result.error:
        # 用户 reply 到一张已 deferred / 已 reject / 已 approved 的卡 → 友好提示
        await msg.reply_text(f"{_facilitator_header()} {result.error}")
        return

    try:
        ops_decision_id = _write_ops_decision_and_link(thread, rationale, actor)
    except Exception:
        log.exception("write_ops_decision_and_link failed")
        await msg.reply_text(f"{_facilitator_header()} ⚠️ 写 ops_decisions 失败,详见服务器日志。")
        return

    # 更新决策卡(append "已采纳" + 移按钮)— 用模拟的 update 走 _apply_card_edit 不方便,
    # 直接调 bot.edit_message_text。
    edit = result.side_effects.get("edit_card") or {}
    append_text = edit.get("append_text") or ""
    try:
        orig = msg.reply_to_message
        new_text = (orig.text or "") + append_text
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=target_msg_id,
            text=new_text,
            reply_markup=None,
        )
    except TelegramError:
        log.warning("edit decision card on rationale_received failed", exc_info=True)

    await msg.reply_text(
        f"{_facilitator_header()} 决策 #{ops_decision_id[:8]} 已记录(status=active)。"
        f"\n等待 verification_plan 在 {thread['verification_plan'][:80]}… 时间窗回填。"
    )


# ─────────────────────────────────────────────────────────────────
# Registration
# ─────────────────────────────────────────────────────────────────

def register_handlers(app: Application) -> None:
    """把 Step 5 的 handlers 装到 analyst bot 的 Application 上。

    调用方:run.py 在 BotRegistry 构建后调一次。
    """
    app.add_handler(CallbackQueryHandler(on_callback_decision, pattern=r"^dec:"))
    app.add_handler(CallbackQueryHandler(on_callback_report_full, pattern=r"^rep:full:"))
    # 收 reply 文字消息;过滤掉 command + 非文字
    app.add_handler(
        MessageHandler(filters.REPLY & filters.TEXT & ~filters.COMMAND, on_reply_rationale)
    )
    log.info("Step 5 handlers registered on analyst bot")
