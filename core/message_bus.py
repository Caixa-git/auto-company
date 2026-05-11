"""
에이전트 간 메시지 버스.
SQLite 기반으로 동작하며 외부 의존성 없음.
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional
import sqlite3

from core.db import transaction

logger = logging.getLogger(__name__)

# 에이전트 이모지 맵
AGENT_EMOJI = {
    "board":       "🏛",
    "system_cfo":  "💰",
    "system_auditor": "🔍",
}

def _agent_emoji(name: str) -> str:
    if name in AGENT_EMOJI:
        return AGENT_EMOJI[name]
    if name.startswith("ceo_"):
        return "🚀"
    if name.startswith("cfo_"):
        return "📊"
    if name == "hotl_human":
        return "👤"
    return "🤖"

# 메시지 타입 한글 요약
MSG_TYPE_LABEL = {
    "task":         "태스크",
    "report":       "보고",
    "approval_req": "승인요청",
    "approval_res": "승인응답",
    "alert":        "알림",
}


# 메시지 타입 상수
class MsgType:
    TASK         = "task"          # 작업 지시
    REPORT       = "report"        # 보고
    APPROVAL_REQ = "approval_req"  # 승인 요청
    APPROVAL_RES = "approval_res"  # 승인 응답
    ALERT        = "alert"         # 긴급 알림


# HOTL 위급도 상수
class Urgency:
    LOW      = "low"
    MEDIUM   = "medium"
    HIGH     = "high"
    ABSOLUTE = "absolute"  # 항상 위진수 직접


@dataclass
class Message:
    from_agent: str
    to_agent:   str
    msg_type:   str
    payload:    dict
    priority:   int = 5
    id:         Optional[int] = None
    status:     str = "pending"
    created_at: Optional[str] = None


class MessageBus:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ──────────────────────────────────────────
    # 송신
    # ──────────────────────────────────────────

    def send(self, msg: Message) -> int:
        """메시지 전송. 삽입된 row id 반환."""
        with transaction(self.conn):
            cur = self.conn.execute(
                """
                INSERT INTO messages (from_agent, to_agent, msg_type, priority, payload)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    msg.from_agent,
                    msg.to_agent,
                    msg.msg_type,
                    msg.priority,
                    json.dumps(msg.payload, ensure_ascii=False),
                ),
            )
            msg_id = cur.lastrowid

        label = MSG_TYPE_LABEL.get(msg.msg_type, msg.msg_type)
        from_emoji = _agent_emoji(msg.from_agent)
        to_emoji = _agent_emoji(msg.to_agent)

        # 페이로드 요약
        summary = ""
        if msg.msg_type == "task":
            summary = msg.payload.get("task", "")
        elif msg.msg_type == "report":
            summary = msg.payload.get("summary", "")[:50]
        elif msg.msg_type == "approval_req":
            summary = msg.payload.get("type", "")
        elif msg.msg_type == "approval_res":
            approved = msg.payload.get("approved", False)
            summary = "승인" if approved else "거절"
        elif msg.msg_type == "alert":
            summary = msg.payload.get("title", "")[:40]

        log_msg = (
            f"{from_emoji} {msg.from_agent} → {to_emoji} {msg.to_agent} "
            f"[{label}] {summary}"
        )
        logger.info(f"[BUS] {log_msg}")
        return msg_id

    def alert_hotl(
        self,
        from_agent: str,
        urgency: str,
        title: str,
        body: str,
    ) -> int:
        """위진수에게 HOTL 알림 전송."""
        with transaction(self.conn):
            cur = self.conn.execute(
                """
                INSERT INTO hotl_alerts (from_agent, urgency, title, body)
                VALUES (?, ?, ?, ?)
                """,
                (from_agent, urgency, title, body),
            )
            alert_id = cur.lastrowid

        logger.warning(f"[HOTL:{urgency.upper()}] {from_agent}: {title}")
        return alert_id

    # ──────────────────────────────────────────
    # 수신
    # ──────────────────────────────────────────

    def receive(self, agent_name: str, limit: int = 10) -> list[Message]:
        """
        수신 대기 중인 메시지 가져오기.
        우선순위(낮은 숫자) 순, 오래된 것 순.
        가져오는 동시에 status를 'processing'으로 변경.
        """
        rows = self.conn.execute(
            """
            SELECT id, from_agent, to_agent, msg_type, priority, payload, status, created_at
            FROM messages
            WHERE to_agent = ? AND status = 'pending'
            ORDER BY priority ASC, id ASC
            LIMIT ?
            """,
            (agent_name, limit),
        ).fetchall()

        if not rows:
            return []

        ids = [r["id"] for r in rows]
        with transaction(self.conn):
            self.conn.execute(
                f"UPDATE messages SET status='processing', updated_at=datetime('now') "
                f"WHERE id IN ({','.join('?' * len(ids))})",
                ids,
            )

        messages = [
            Message(
                id=r["id"],
                from_agent=r["from_agent"],
                to_agent=r["to_agent"],
                msg_type=r["msg_type"],
                priority=r["priority"],
                payload=json.loads(r["payload"]),
                status="processing",
                created_at=r["created_at"],
            )
            for r in rows
        ]

        logger.debug(f"[BUS] {agent_name} 수신 {len(messages)}건")
        return messages

    def ack(self, msg_id: int):
        """메시지 처리 완료 표시."""
        with transaction(self.conn):
            self.conn.execute(
                "UPDATE messages SET status='done', updated_at=datetime('now') WHERE id=?",
                (msg_id,),
            )

    # ──────────────────────────────────────────
    # 에이전트 상태
    # ──────────────────────────────────────────

    def set_agent_state(self, agent_name: str, status: str, state: dict):
        """에이전트 상태 업서트."""
        with transaction(self.conn):
            self.conn.execute(
                """
                INSERT INTO agent_states (agent_name, status, state_json, updated_at)
                VALUES (?, ?, ?, datetime('now'))
                ON CONFLICT(agent_name) DO UPDATE SET
                    status     = excluded.status,
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                """,
                (agent_name, status, json.dumps(state, ensure_ascii=False)),
            )

    def get_agent_state(self, agent_name: str) -> Optional[dict]:
        """에이전트 상태 조회."""
        row = self.conn.execute(
            "SELECT status, state_json FROM agent_states WHERE agent_name=?",
            (agent_name,),
        ).fetchone()

        if not row:
            return None
        return {"status": row["status"], **json.loads(row["state_json"])}

    # ──────────────────────────────────────────
    # 이벤트 로그
    # ──────────────────────────────────────────

    def log_event(self, agent_name: str, event_type: str, detail: dict = {}):
        with transaction(self.conn):
            self.conn.execute(
                "INSERT INTO event_log (agent_name, event_type, detail) VALUES (?, ?, ?)",
                (agent_name, event_type, json.dumps(detail, ensure_ascii=False)),
            )
