"""
System Auditor agent.
Independently monitors all agents.
Sends HOTL alerts on failure detection.
"""

import time
import logging
import threading
from typing import Optional
import sqlite3

from core.db import get_thread_connection
from core.message_bus import MessageBus, Urgency

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 120         # CEO 등 LLM 호출 포함 에이전트
CRITICAL_TIMEOUT = 90         # Board / System CFO
AUDITOR_CHECK_INTERVAL = 10

CRITICAL_AGENTS = {"board", "system_cfo"}


class SystemAuditor:
    """
    Fully independent - does not inherit BaseAgent.
    Runs regardless of other agent failures.
    """

    def __init__(self, db_path: str, poll_interval: float = AUDITOR_CHECK_INTERVAL):
        self.db_path = db_path
        self.poll_interval = poll_interval
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._alerted: dict[str, bool] = {}

    @property
    def _conn(self) -> sqlite3.Connection:
        return get_thread_connection(self.db_path)

    @property
    def _bus(self) -> MessageBus:
        return MessageBus(self._conn)

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="system_auditor", daemon=True)
        self._thread.start()
        logger.info("[Auditor] started (independent)")

    def stop(self):
        self._running = False
        logger.info("[Auditor] stopped")

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self):
        while self._running:
            try:
                self._check_all_agents()
                self._check_pending_alerts()
            except Exception as e:
                logger.error(f"[Auditor] loop error: {e}")
            time.sleep(self.poll_interval)

    def _elapsed_seconds(self, updated_at_str: str) -> int:
        """SQLite UTC 기준으로 경과 시간 계산 (로컬 시간대 무관)."""
        try:
            row = self._conn.execute(
                "SELECT CAST((julianday('now') - julianday(?)) * 86400 AS INTEGER)",
                (updated_at_str,)
            ).fetchone()
            return row[0] if row and row[0] is not None else 9999
        except Exception:
            return 9999

    def _check_all_agents(self):
        rows = self._conn.execute(
            "SELECT agent_name, status, updated_at FROM agent_states"
        ).fetchall()

        for row in rows:
            name = row["agent_name"]
            status = row["status"]
            updated_at_str = row["updated_at"]

            if status == "stopped":
                self._alerted.pop(name, None)
                continue

            elapsed = self._elapsed_seconds(updated_at_str)
            timeout = CRITICAL_TIMEOUT if name in CRITICAL_AGENTS else DEFAULT_TIMEOUT

            if elapsed > timeout:
                if not self._alerted.get(name):
                    urgency = Urgency.ABSOLUTE if name in CRITICAL_AGENTS else Urgency.HIGH
                    self._send_alert(
                        urgency=urgency,
                        title=f"[에이전트 무응답] {name}",
                        body=(
                            f"agent '{name}' no response for {elapsed}s.\n"
                            f"last status: {status}\n"
                            f"last update: {updated_at_str}\n"
                            f"timeout: {timeout}s"
                        ),
                    )
                    self._alerted[name] = True
                    logger.warning(f"[Auditor] {name} unresponsive ({elapsed}s)")
            else:
                if self._alerted.get(name):
                    self._send_alert(
                        urgency=Urgency.LOW,
                        title=f"[복구] {name}",
                        body=f"agent '{name}' recovered.",
                    )
                    self._alerted[name] = False
                    logger.info(f"[Auditor] {name} recovered")

        # error 상태 감지
        error_rows = self._conn.execute(
            "SELECT agent_name FROM agent_states WHERE status='error'"
        ).fetchall()
        for row in error_rows:
            name = row["agent_name"]
            key = f"error_{name}"
            if not self._alerted.get(key):
                urgency = Urgency.ABSOLUTE if name in CRITICAL_AGENTS else Urgency.HIGH
                self._send_alert(
                    urgency=urgency,
                    title=f"[에이전트 오류] {name}",
                    body=f"agent '{name}' is in error state.",
                )
                self._alerted[key] = True

    def _check_pending_alerts(self):
        """ABSOLUTE 알림 10분 이상 미처리 시 재알림."""
        rows = self._conn.execute(
            """
            SELECT id, title FROM hotl_alerts
            WHERE status='sent' AND urgency='absolute'
            AND CAST((julianday('now') - julianday(created_at)) * 86400 AS INTEGER) > 600
            """
        ).fetchall()
        for row in rows:
            key = f"renotify_{row['id']}"
            if not self._alerted.get(key):
                self._send_alert(
                    urgency=Urgency.ABSOLUTE,
                    title=f"[미처리 재알림] {row['title'][:40]}",
                    body=f"Absolute approval pending >10min. alert_id={row['id']}",
                )
                self._alerted[key] = True

    def _send_alert(self, urgency: str, title: str, body: str):
        try:
            self._bus.alert_hotl(from_agent="system_auditor", urgency=urgency, title=title, body=body)
        except Exception as e:
            logger.error(f"[Auditor] alert send failed: {e}")

    def summary(self) -> dict:
        rows = self._conn.execute(
            "SELECT agent_name, status, updated_at FROM agent_states"
        ).fetchall()
        result = {}
        for row in rows:
            elapsed = self._elapsed_seconds(row["updated_at"])
            timeout = CRITICAL_TIMEOUT if row["agent_name"] in CRITICAL_AGENTS else DEFAULT_TIMEOUT
            result[row["agent_name"]] = {
                "status": row["status"],
                "elapsed_sec": elapsed,
                "healthy": elapsed < timeout,
            }
        return result
