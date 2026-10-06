"""
CLI entry point for starting the interactive Telegram Bot service.
Usage: python -m src.bot
"""

import asyncio
import logging
import signal
import sys

from src.bot.listener import BotListener
from src.database.ddl import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("src.bot")


async def main() -> None:
    logger.info("Initializing database schemas...")
    await init_db()

    listener = BotListener()

    # Graceful shutdown handling
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, listener.stop)
        except NotImplementedError:
            pass

    logger.info("Starting Audi A6 C5 Telegram Bot Daemon...")
    await listener.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot process exited.")
