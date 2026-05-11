"""
Discord DM log handler.
Sends important events to Discord DM.
"""

import logging
import asyncio
from typing import Optional

logger = logging.getLogger(__name__)

IMPORTANT_KEYWORDS = [
    "Bootstrapping",
    "started (independent)",
    "ACS is running",
    "ACS shutdown",
    "업종 선택:",
    "승인됨:",
    "거절됨:",
    "창업 시작:",
    "회고 완료:",
    "성공 판단:",
    "실패 판단:",
    "Company spawned:",
    "브랜드명:",
    "이메일 검토 요청:",
    "이메일 자동 발송:",
    "[BUS]",
    "Stage 승급:",
    "Stage 강등:",
    "Glasswing 자율 승인:",
    "loop error",
    "루프 오류",
    "무응답 감지",
    "재무 위기",
]

IGNORE_KEYWORDS = [
    "heartbeat",
    "poll",
    "processing [",
    "보고 수신",
    "STATUS]",
    "ceo_company_001 -> ceo_company_001",
]


def _format_bus_message(msg: str) -> str:
    """Format [BUS] message for Discord."""
    try:
        core = msg.split("[BUS] ", 1)[1]
        if "->" in core and "[" in core:
            parts = core.split("[", 1)
            agents = parts[0].strip()
            rest = parts[1]
            if "]" in rest:
                label, summary = rest.split("]", 1)
                summary = summary.strip()
                return f"{agents}\n>> {label}: {summary}" if summary else f"{agents}\n>> {label}"
    except Exception:
        pass
    return msg


class DiscordDMHandler(logging.Handler):
    """
    Python logging handler.
    Sends important logs to Discord DM.
    Buffers messages before bot is ready.
    """

    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self.setLevel(logging.INFO)
        self._buffer: list[str] = []

    def flush_buffer(self):
        """Send buffered messages after bot is ready."""
        if not self._buffer:
            return
        msgs = self._buffer.copy()
        self._buffer.clear()
        for m in msgs:
            try:
                asyncio.run_coroutine_threadsafe(
                    self.bot.log_dm(m),
                    self.bot.loop,
                )
            except Exception:
                pass

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)

            if any(kw in msg for kw in IGNORE_KEYWORDS):
                return

            is_important = any(kw in msg for kw in IMPORTANT_KEYWORDS)
            is_warning = record.levelno >= logging.WARNING

            if not (is_important or is_warning):
                return

            # Format message
            if "[BUS]" in msg:
                dm_msg = _format_bus_message(msg)
            else:
                if record.levelno >= logging.ERROR:
                    prefix = "🔴"
                elif record.levelno >= logging.WARNING:
                    prefix = "🟠"
                else:
                    prefix = "🔵"
                short_msg = msg[-200:] if len(msg) > 200 else msg
                dm_msg = f"{prefix} {short_msg}"

            # Buffer if bot not ready
            if not self.bot.is_ready():
                self._buffer.append(dm_msg)
                if len(self._buffer) > 50:
                    self._buffer = self._buffer[-50:]
                return

            asyncio.run_coroutine_threadsafe(
                self.bot.log_dm(dm_msg),
                self.bot.loop,
            )

        except Exception:
            pass


def attach_discord_logger(bot):
    """Attach Discord DM handler to root logger."""
    handler = DiscordDMHandler(bot)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)

    # Flush buffered messages
    handler.flush_buffer()

    logger.info("[DiscordLogger] DM log handler attached")
    return handler
