"""
Board 에이전트.
CEO/CFO 생성·승인·에스컬레이션 담당.
"""

import json
import logging
from typing import Optional

from agents.base_agent import BaseAgent
from core.message_bus import Message, MsgType, Urgency
from core.persona_loader import PersonaLoader
from core.memory_loader import MemoryLoader
from core.identity_loader import IdentityLoader
from core.behavior_loader import BehaviorLoader
from core.glasswing import GlasswingManager, ABSOLUTE_MANUAL

logger = logging.getLogger(__name__)


class BoardAgent(BaseAgent):
    def __init__(self, name: str, db_path: str, llm, persona_loader: PersonaLoader = None, poll_interval: float = 5.0):
        super().__init__(name, db_path, llm, poll_interval)
        self._persona_loader = persona_loader
        self._memory_loader: MemoryLoader = None
        self._identity_loader = IdentityLoader()
        self._behavior_loader = BehaviorLoader()
        self._glasswing: GlasswingManager = None  # main.py에서 주입

    @property
    def system_prompt(self) -> str:
        # 1. Identity
        identity_block = self._identity_loader.to_prompt("board")
        if not identity_block:
            identity_block = "## Identity: Investment Decision Maker\nCore Value: Protect capital, enable growth with disciplined risk management.\n"

        # 2. ACS 강제 행동 (behaviors/board.md)
        behavior = self._behavior_loader.load("board")

        # 3. 포트폴리오 현황 (L3)
        memory = self._memory_loader.load_for_board() if self._memory_loader else ""
        memory_block = f"""
## Portfolio Context
{memory if memory else "(데이터 없음)"}
"""
        return identity_block + "\n\n" + behavior + "\n\n" + memory_block

    def handle_message(self, msg: Message) -> Optional[str]:
        if msg.msg_type == MsgType.APPROVAL_REQ:
            return self._handle_approval(msg)
        elif msg.msg_type == MsgType.APPROVAL_RES:
            return self._handle_approval_res(msg)
        elif msg.msg_type == MsgType.REPORT:
            return self._handle_report(msg)
        elif msg.msg_type == MsgType.ALERT:
            return self._handle_alert(msg)
        else:
            logger.warning(f"[Board] 알 수 없는 메시지 타입: {msg.msg_type}")
            return None

    def _handle_approval(self, msg: Message) -> str:
        payload = msg.payload
        request_type = payload.get("type", "unknown")
        cost = payload.get("cost", 0)

        # Glasswing 자율 처리 체크
        if self._glasswing:
            ceo_state = self.bus.get_agent_state(msg.from_agent) or {}
            budget = ceo_state.get("initial_budget", 0) or ceo_state.get("budget", 0)
            can_auto, reason = self._glasswing.can_auto_approve(request_type, cost, budget)

            if can_auto:
                logger.info(f"[Board] Glasswing 자율 처리: {request_type} — {reason}")
                self.send(
                    to=msg.from_agent,
                    msg_type=MsgType.APPROVAL_RES,
                    payload={"approved": True, "reason": f"[Stage {self._glasswing.get_stage()}] {reason}"},
                    priority=2,
                )
                return f"Glasswing 자율 승인: {request_type}"

        # 절대 승인 항목이면 즉시 위진수 에스컬레이션
        absolute_items = {"exit", "human_ceo", "human_cfo", "large_investment", "hire_human", "system_shutdown"}
        if request_type in absolute_items:
            self.alert(
                urgency=Urgency.ABSOLUTE,
                title=f"[절대 승인 필요] {request_type}",
                body=json.dumps(payload, ensure_ascii=False, indent=2),
            )
            self.send(
                to=msg.from_agent,
                msg_type=MsgType.APPROVAL_RES,
                payload={"approved": False, "reason": "위진수 절대 승인 대기 중", "status": "escalated"},
                priority=2,
            )
            return f"절대 승인 에스컬레이션: {request_type}"

        # 일반 승인 요청 → LLM 판단
        prompt = f"""다음 승인 요청을 검토하세요:

요청자: {msg.from_agent}
요청 타입: {request_type}
내용: {json.dumps(payload, ensure_ascii=False, indent=2)}

Board 원칙에 따라 승인/거절/에스컬레이션을 결정하세요."""

        try:
            decision = self.think_json(prompt)
        except (ValueError, Exception) as e:
            logger.error(f"[Board] LLM 판단 실패: {e}")
            decision = {"decision": "escalate", "reason": "LLM 판단 실패", "action": "위진수 확인 필요", "escalate_to": "human"}

        approved = decision.get("decision") == "approve"

        # 에스컬레이션이면 위진수 알림
        if decision.get("escalate_to") == "human":
            self.alert(
                urgency=Urgency.MEDIUM,
                title=f"[Board 에스컬레이션] {request_type}",
                body=f"요청자: {msg.from_agent}\n이유: {decision.get('reason')}\n내용: {json.dumps(payload, ensure_ascii=False)}",
            )

        # 요청자에게 결과 전송
        self.send(
            to=msg.from_agent,
            msg_type=MsgType.APPROVAL_RES,
            payload={
                "approved": approved,
                "reason": decision.get("reason", ""),
                "action": decision.get("action", ""),
            },
            priority=2,
        )

        self.update_state(last_decision=decision)
        return f"승인 처리 완료: {decision.get('decision')} ({request_type})"

    def _handle_report(self, msg: Message) -> str:
        payload = msg.payload
        logger.info(f"[Board] 보고 수신 from {msg.from_agent}: {payload.get('summary', '')}")
        self.bus.log_event("board", "report_received", {
            "from": msg.from_agent,
            "summary": payload.get("summary", ""),
        })
        return f"보고 수신: {msg.from_agent}"

    def _handle_alert(self, msg: Message) -> str:
        urgency = msg.payload.get("urgency", Urgency.HIGH)
        title = msg.payload.get("title", "긴급 알림")
        body = msg.payload.get("body", "")
        self.alert(urgency=urgency, title=title, body=body)
        return f"알림 전달: {title}"

    def _handle_approval_res(self, msg: Message) -> str:
        """System CFO 등으로부터 승인 응답 수신 — 해당 CEO에게 전달."""
        company_id = msg.payload.get("company_id")
        approved = msg.payload.get("approved", False)
        logger.info(f"[Board] 승인 응답 수신 from {msg.from_agent}: company={company_id} approved={approved}")
        self.bus.log_event("board", "approval_res_received", {
            "from": msg.from_agent,
            "company_id": company_id,
            "approved": approved,
        })
        return f"승인 응답 수신: {msg.from_agent}"
