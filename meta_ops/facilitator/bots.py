"""Phase 3 Step 2: TG Bot 框架 — 同进程多 token。

设计:
- 4 个角色:Analyst / Facilitator / Watcher / Craftsman
- 每个角色一个 python-telegram-bot Application 实例,各自独立 polling
- token 从 .env 读 TG_BOT_TOKEN_<ROLE>;任一缺失 BotRegistry 拒绝构建
- Phase 3 业务 handler 只在 Analyst/Facilitator 上注册;Watcher/Craftsman 仅 /start 占位

后续 Step:
- Step 4 周报推送器调 registry.bot('analyst').bot.send_message(...)
- Step 5 Facilitator 状态机注册 CallbackQueryHandler 到 'facilitator'
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

log = logging.getLogger(__name__)


# 角色 → emoji + 显示名(每条 TG 消息开头标这个,帮 Jonathan 切换决策模式)
ROLE_EMOJI: dict[str, str] = {
    "analyst":     "🔍",
    "facilitator": "🎯",
    "watcher":     "👁",
    "craftsman":   "🔨",
}

ROLE_DISPLAY: dict[str, str] = {
    "analyst":     "Analyst",
    "facilitator": "Facilitator",
    "watcher":     "Watcher",
    "craftsman":   "Craftsman",
}

# 4 个角色都必须有 token,缺任一 service 启动即失败(防隐性遗漏)
REQUIRED_ROLES: tuple[str, ...] = ("analyst", "facilitator", "watcher", "craftsman")


@dataclass
class BotEntry:
    role: str
    app: Application

    @property
    def emoji(self) -> str:
        return ROLE_EMOJI[self.role]

    @property
    def display(self) -> str:
        return ROLE_DISPLAY[self.role]

    @property
    def header(self) -> str:
        """每条该 Bot 发出的消息开头应加的角色标识。"""
        return f"{self.emoji} {self.display}:"


class BotRegistry:
    """构建并持有 4 个 Application;提供统一的 handler 注册与启动入口。"""

    def __init__(self) -> None:
        missing = [r for r in REQUIRED_ROLES if not os.getenv(f"TG_BOT_TOKEN_{r.upper()}")]
        if missing:
            raise RuntimeError(
                f"missing TG_BOT_TOKEN_* in .env: {missing}. "
                "All 4 tokens are required (spec v1 §5 决策 2)."
            )

        self._bots: dict[str, BotEntry] = {}
        for role in REQUIRED_ROLES:
            token = os.getenv(f"TG_BOT_TOKEN_{role.upper()}")
            assert token  # already validated above
            app = Application.builder().token(token).build()
            self._bots[role] = BotEntry(role=role, app=app)
            log.info("BotRegistry: built %s (token *****%s)", role, token[-6:])

        self._register_default_handlers()

    def bot(self, role: str) -> BotEntry:
        if role not in self._bots:
            raise KeyError(f"unknown role {role!r}; valid: {list(self._bots)}")
        return self._bots[role]

    def all(self) -> list[BotEntry]:
        return list(self._bots.values())

    def _register_default_handlers(self) -> None:
        """每个 Bot 都装 /start 报身份。Phase 3 业务 handler 后续 step 加。"""
        for entry in self._bots.values():
            entry.app.add_handler(CommandHandler("start", _make_start_handler(entry)))


def _make_start_handler(entry: BotEntry):
    """生成绑定该 Bot role 的 /start handler。

    log chat info(id / type / title)— 方便:
    1. 抽群 chat_id 给 .env 的 TG_ADMIN_CHAT_ID
    2. 后续调试群 vs 私聊 / chat 切换问题
    """

    async def _start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        if chat is not None:
            log.info(
                "/start on %s: chat_id=%s type=%s title=%r username=%r",
                entry.role, chat.id, chat.type, chat.title, chat.username,
            )

        is_placeholder = entry.role in ("watcher", "craftsman")
        if is_placeholder:
            text = (
                f"{entry.header} placeholder bot for Phase 3.\n"
                "Business handlers come in Phase 4."
            )
        else:
            text = (
                f"{entry.header} ready (Phase 3 Step 2).\n"
                "Active handlers will be registered in Step 4-5."
            )
        await update.effective_message.reply_text(text)

    return _start
