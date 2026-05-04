"""Phase 3 Step 4: 周报推送器。

调用方:
- systemd `acc-facilitator-pusher.timer`(Sun 20:05 SGT,自动)
- `acc facilitator push --week YYYY-WW`(手动 / 测试 / 补推)

流程:
1. 读 reports/_meta/index.json 验最新条目 week == 目标 + status=='ok'
2. 读 reports/<week>.md + parse_report → list[CandidateDecision]
3. 用 @acc_ana_bot 推 1 条 summary(带 [📄 全文] 按钮)
4. 每条决策推 1 条详情(带 [✅采用][❌否决][💬讨论] 按钮)
5. 每推一条:先 INSERT ops_decision_threads (state='proposed') → send → UPDATE
   tg_chat_id/tg_message_id + state='displayed'

失败处理:
- index.json 找不到目标 week / status!=ok → 不推,返 deferred(供 l2_run_log 记录)
- reports/<week>.md 不存在 → 推 1 条"周报缺失"通知
- parse 出 0 条 → 推 summary 但说明"本周无候选决策"
- 单条 send 失败 → 该行保留 state='proposed',继续下一条,整体标 partial
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError

from meta_ops.common.db import get_local_db
from meta_ops.facilitator.bots import ROLE_DISPLAY, ROLE_EMOJI
from meta_ops.facilitator.report_parser import CandidateDecision, parse_report

log = logging.getLogger(__name__)

REPORTS_ROOT = Path("/opt/accelerator/reports")
INDEX_JSON = REPORTS_ROOT / "_meta" / "index.json"

# TG 单条消息硬上限 4096;给 markdown/格式留余地
_MSG_HARD_LIMIT = 3800


def _validate_index(week_iso: str) -> tuple[bool, str]:
    """检查 index.json 最新条目是否就绪。
    返回 (ok, reason);ok=False 时 reason 描述跳过原因。
    """
    if not INDEX_JSON.exists():
        return False, f"[deferred] index.json missing at {INDEX_JSON}"
    try:
        data = json.loads(INDEX_JSON.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return False, f"[deferred] index.json invalid JSON: {e}"

    reports = data.get("reports", [])
    if not reports:
        return False, "[deferred] index.json has 0 reports"

    latest = reports[-1]
    if latest.get("week") != week_iso:
        return False, (
            f"[deferred] latest report in index is {latest.get('week')!r}, "
            f"not target {week_iso!r}"
        )
    if latest.get("status") != "ok":
        return False, (
            f"[deferred] latest report status={latest.get('status')!r} (need 'ok')"
        )
    return True, "ok"


def _truncate(text: str, limit: int = _MSG_HARD_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 100] + "\n\n…(已截断,见 reports/<week>.md 全文)"


def _build_summary_text(week_iso: str, n_decisions: int, parse_warnings: int) -> str:
    header = f"{ROLE_EMOJI['analyst']} {ROLE_DISPLAY['analyst']} — 周报 {week_iso}"
    if n_decisions == 0:
        body = "本周无候选决策(数据不足或 §4 解析为空)。"
    else:
        body = (
            f"候选决策数: {n_decisions}\n\n"
            f"接下来 {n_decisions} 条消息是各候选决策详情。\n"
            f"点 [✅ 采用] / [❌ 否决] / [💬 讨论] 推进。\n"
            "未响应 12 小时自动归档为 deferred。"
        )
    if parse_warnings:
        body += f"\n\n⚠️ 解析告警 {parse_warnings} 条(见 ops_decision_threads.parse_warnings)"
    return f"{header}\n\n{body}"


def _build_decision_text(idx: int, total: int, d: CandidateDecision) -> str:
    header = (
        f"{ROLE_EMOJI['analyst']} {ROLE_DISPLAY['analyst']} — "
        f"决策 {idx}/{total}: {d.title or '(无标题)'}"
    )
    parts = [header, ""]
    if d.decision_type:
        parts.append(f"类型: {d.decision_type}")
    if d.subject:
        parts.append(f"对象: {d.subject}")
    parts.append("")
    if d.rationale:
        parts.append(f"理由: {d.rationale}")
        parts.append("")
    if d.verification_plan:
        parts.append(f"验证: {d.verification_plan}")
        parts.append("")
    if d.risk:
        parts.append(f"风险: {d.risk}")
    if d.evidence:
        parts.append(f"\n📚 evidence: {len(d.evidence)} 条引用")
    if d.parse_warnings:
        parts.append(
            f"\n⚠️ 解析告警: {'; '.join(d.parse_warnings[:2])}"
        )
    return _truncate("\n".join(parts))


def _build_summary_keyboard(week_iso: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📄 全文", callback_data=f"rep:full:{week_iso}")],
    ])


def _build_decision_keyboard(thread_id: str) -> InlineKeyboardMarkup:
    """Step 5 状态机会注册这些 callback_data 的 handler。

    callback_data 格式: dec:<action>:<thread_uuid>
    actions: apv (approve) / rej (reject) / dsc (discuss) / full (show full)
    """
    short = thread_id  # UUID 36 字符,加前缀 12 字符 < 64 上限
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ 采用", callback_data=f"dec:apv:{short}"),
            InlineKeyboardButton("❌ 否决", callback_data=f"dec:rej:{short}"),
        ],
        [
            InlineKeyboardButton("💬 讨论", callback_data=f"dec:dsc:{short}"),
            InlineKeyboardButton("📄 全文", callback_data=f"dec:full:{short}"),
        ],
    ])


def _insert_thread_row(conn, week: str, idx: int, d: CandidateDecision) -> str:
    """INSERT proposed 行,返回 thread_id (UUID str)。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ops_decision_threads
                (report_week, candidate_index, decision_type, subject,
                 rationale_proposed, verification_plan, risk, evidence, state)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'proposed')
            RETURNING id
            """,
            (
                week,
                idx,
                d.decision_type or "",
                d.subject or "",
                d.rationale or "",
                d.verification_plan or "",
                d.risk,
                json.dumps(d.evidence, ensure_ascii=False),
            ),
        )
        return str(cur.fetchone()[0])


def _mark_displayed(conn, thread_id: str, chat_id: int, message_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ops_decision_threads
               SET tg_chat_id = %s,
                   tg_message_id = %s,
                   state = 'displayed',
                   updated_at = NOW()
             WHERE id = %s
            """,
            (chat_id, message_id, thread_id),
        )


async def _send_all(
    bot: Bot,
    chat_id: int,
    week_iso: str,
    decisions: list[CandidateDecision],
    conn,
    dry_run: bool,
) -> dict[str, Any]:
    summary_text = _build_summary_text(
        week_iso=week_iso,
        n_decisions=len(decisions),
        parse_warnings=sum(1 for d in decisions if d.parse_warnings),
    )

    sent = 0
    failed: list[dict[str, Any]] = []
    thread_ids: list[str] = []

    if dry_run:
        log.info("[dry-run] summary message preview:\n%s\n%s", "-" * 40, summary_text)
        for i, d in enumerate(decisions, start=1):
            log.info(
                "[dry-run] decision %d preview:\n%s\n%s",
                i, "-" * 40, _build_decision_text(i, len(decisions), d),
            )
        return {
            "dry_run": True,
            "summary_preview": summary_text,
            "n_decisions": len(decisions),
        }

    # Real send
    async with bot:
        # 1. summary
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=summary_text,
                reply_markup=_build_summary_keyboard(week_iso),
            )
            sent += 1
        except TelegramError as e:
            failed.append({"kind": "summary", "error": str(e)})
            log.exception("summary send failed")

        # 2. decisions
        for i, d in enumerate(decisions, start=1):
            thread_id = _insert_thread_row(conn, week_iso, i, d)
            thread_ids.append(thread_id)
            try:
                msg = await bot.send_message(
                    chat_id=chat_id,
                    text=_build_decision_text(i, len(decisions), d),
                    reply_markup=_build_decision_keyboard(thread_id),
                )
                _mark_displayed(conn, thread_id, chat_id, msg.message_id)
                sent += 1
            except TelegramError as e:
                failed.append(
                    {"kind": "decision", "candidate_index": i, "thread_id": thread_id, "error": str(e)}
                )
                log.exception("decision %d send failed (thread_id=%s)", i, thread_id)
        conn.commit()

    return {
        "dry_run": False,
        "sent": sent,
        "failed": failed,
        "thread_ids": thread_ids,
        "n_decisions": len(decisions),
    }


def push_weekly_report(week_iso: str, dry_run: bool = False) -> dict[str, Any]:
    """主入口。返回结构化结果(供 CLI / l2_run_log 用)。"""
    result: dict[str, Any] = {
        "week": week_iso,
        "dry_run": dry_run,
        "status": "ok",
    }

    # 1. index.json 守门
    ok, reason = _validate_index(week_iso)
    if not ok:
        log.warning("pusher: skipped — %s", reason)
        result["status"] = "deferred"
        result["reason"] = reason
        return result

    # 2. 读 + parse
    report_path = REPORTS_ROOT / f"{week_iso}.md"
    if not report_path.exists():
        log.error("pusher: report file missing %s", report_path)
        result["status"] = "deferred"
        result["reason"] = f"[deferred] report file missing: {report_path}"
        return result

    md = report_path.read_text(encoding="utf-8")
    decisions = parse_report(md)
    log.info("pusher: parsed %d candidate decisions from %s", len(decisions), report_path.name)

    # 3. chat_id + token from env
    chat_id_raw = os.getenv("TG_ADMIN_CHAT_ID")
    if not chat_id_raw:
        result["status"] = "failed"
        result["reason"] = "TG_ADMIN_CHAT_ID not set in env"
        log.error(result["reason"])
        return result
    try:
        chat_id = int(chat_id_raw)
    except ValueError:
        result["status"] = "failed"
        result["reason"] = f"TG_ADMIN_CHAT_ID not int: {chat_id_raw!r}"
        log.error(result["reason"])
        return result

    token = os.getenv("TG_BOT_TOKEN_ANALYST")
    if not token:
        result["status"] = "failed"
        result["reason"] = "TG_BOT_TOKEN_ANALYST not set"
        log.error(result["reason"])
        return result

    bot = Bot(token=token)
    conn = get_local_db()
    try:
        send_result = asyncio.run(
            _send_all(bot, chat_id, week_iso, decisions, conn, dry_run=dry_run)
        )
    finally:
        conn.close()

    result.update(send_result)
    if not dry_run and send_result.get("failed"):
        result["status"] = "partial"
        result["partial_reasons"] = [
            f"[degraded] {f['kind']} #{f.get('candidate_index', '-')} send failed: {f['error']}"
            for f in send_result["failed"]
        ]
    return result
