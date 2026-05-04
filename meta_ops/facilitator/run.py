"""Phase 3 Step 2: Facilitator 长进程入口。

systemd `acc-facilitator.service` ExecStart 指向 `python -m meta_ops.facilitator.run`。
启动 4 个 Bot 的 polling,任一 Bot 崩溃 → asyncio 抛出 → systemd Restart=always 整体拉起。
"""
from __future__ import annotations

import asyncio
import logging
import signal
from pathlib import Path

from dotenv import load_dotenv

from meta_ops.common.logging_config import setup_logging
from meta_ops.facilitator.bots import BotRegistry

log = logging.getLogger(__name__)

ENV_PATH = Path("/opt/accelerator/.env")


async def _run() -> None:
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH)
        log.info("loaded .env from %s", ENV_PATH)
    else:
        log.warning(".env not found at %s; relying on process env", ENV_PATH)

    registry = BotRegistry()
    log.info("facilitator: %d bots built", len(registry.all()))

    # 启动每个 Bot 的 application + polling
    for entry in registry.all():
        await entry.app.initialize()
        await entry.app.start()
        await entry.app.updater.start_polling(drop_pending_updates=True)
        log.info("started polling: %s", entry.role)

    log.info("facilitator: all bots polling; awaiting SIGTERM/SIGINT")

    stop_event = asyncio.Event()

    def _stop(*_: object) -> None:
        log.info("signal received; shutting down")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            # Windows fallback (we don't run there in prod, but keep safe)
            signal.signal(sig, _stop)

    await stop_event.wait()

    log.info("shutting down all bots...")
    for entry in registry.all():
        try:
            await entry.app.updater.stop()
            await entry.app.stop()
            await entry.app.shutdown()
            log.info("stopped: %s", entry.role)
        except Exception:
            log.exception("error stopping %s", entry.role)


def main() -> None:
    setup_logging("facilitator")
    asyncio.run(_run())


if __name__ == "__main__":
    main()
