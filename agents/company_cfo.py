"""
Company CFO 에이전트.
예산 관리, 비용 추적, System CFO 보고 담당.
CEO와 독립적으로 재무 판단.
"""

import json
import logging
from typing import Optional

from agents.base_agent import BaseAgent
from core.message_bus import Message, MsgType, Urgency

logger = logging.getLogger(__name__)


# 실패 임계값
FAILURE_THRESHOLD = 0.3   # 예산의 30% 이하 남으면 위험 신호
CRITICAL_THRESHOLD = 0.1  # 예산의 10% 이하 남으면 즉시 에스컬레이션


class CompanyCFOAgent(BaseAgent):
    def __init__(self, name: str, db_path: str, llm, ceo_name: str, poll_interval: float = 5.0):
        super().__init__(name, db_path, llm, poll_interval)

        self.ceo_name = ceo_name          # 담당 CEO
        self.company_id: Optional[str] = None
        self.initial_budget: int = 0
        self.current_budget: int = 0
        self.total_revenue: int = 0
        self.expense_log: list[dict] = [] # 지출 기록
        self.revenue_log: list[dict] = [] # 수익 기록

    @property
    def system_prompt(self) -> str:
        budget_ratio = (self.current_budget / self.initial_budget) if self.initial_budget > 0 else 1.0
        return f"""당신은 Auto-Company-System의 Company CFO입니다.

담당 CEO: {self.ceo_name}
회사 ID: {self.company_id or '미정'}
초기 예산: {self.initial_budget:,}원
현재 잔액: {self.current_budget:,}원
총 수익: {self.total_revenue:,}원
예산 소진율: {(1 - budget_ratio) * 100:.1f}%

역할:
- 예산 관리 및 지출 승인 (소액)
- 비용 추적 및 재무 보고
- System CFO에게 정기 보고
- 재무 위기 감지 및 에스컬레이션

재무 원칙:
- CEO의 사업 결정은 존중하되 재무 건전성 독립 판단
- 예산 30% 이하: System CFO 경고 보고
- 예산 10% 이하: 즉시 위진수 에스컬레이션
- 모든 지출은 근거 기록 필수
- 추측 기반 재무 결정 금지 (데이터 없으면 에스컬레이션)

응답은 상황에 따라 자유롭게. 재무 분석 시:
{{
  "assessment": "재무 상태 평가",
  "risk_level": "low" | "medium" | "high" | "critical",
  "recommendation": "권고 사항",
  "action_needed": true/false
}}"""

    def handle_message(self, msg: Message) -> Optional[str]:
        if msg.msg_type == MsgType.REPORT:
            return self._handle_report(msg)
        elif msg.msg_type == MsgType.TASK:
            return self._handle_task(msg)
        elif msg.msg_type == MsgType.APPROVAL_REQ:
            return self._handle_approval_req(msg)
        else:
            logger.warning(f"[{self.name}] 알 수 없는 메시지 타입: {msg.msg_type}")
            return None

    # ──────────────────────────────────────────
    # 보고 처리
    # ──────────────────────────────────────────

    def _handle_report(self, msg: Message) -> str:
        payload = msg.payload
        report_type = payload.get("summary", "")

        # CEO로부터 창업 시작 보고
        if "창업 시작" in report_type or "초기 예산" in report_type:
            return self._init_budget(payload)

        # 수익 보고
        elif "revenue" in payload or "수익" in report_type:
            return self._record_revenue(payload)

        # 지출 보고
        elif "expense" in payload or "지출" in report_type:
            return self._record_expense(payload)

        else:
            logger.info(f"[{self.name}] 보고 수신: {report_type}")
            return f"보고 수신: {report_type}"

    def _init_budget(self, payload: dict) -> str:
        """초기 예산 설정."""
        self.company_id = payload.get("company_id")
        self.initial_budget = payload.get("budget", 0)
        self.current_budget = self.initial_budget
        self.update_state(
            company_id=self.company_id,
            initial_budget=self.initial_budget,
            current_budget=self.current_budget,
        )

        # System CFO에게 창업 보고
        self.send(
            to="system_cfo",
            msg_type=MsgType.REPORT,
            payload={
                "summary": "새 회사 창업 — 초기 예산 배정 완료",
                "company_id": self.company_id,
                "ceo": self.ceo_name,
                "initial_budget": self.initial_budget,
            },
        )

        logger.info(f"[{self.name}] 초기 예산 설정: {self.initial_budget:,}원")
        return f"초기 예산 설정 완료: {self.initial_budget:,}원"

    def _record_expense(self, payload: dict) -> str:
        """지출 기록 및 예산 차감."""
        amount = payload.get("amount", 0)
        description = payload.get("description", "")

        if amount <= 0:
            return "유효하지 않은 지출 금액"

        self.current_budget -= amount
        self.expense_log.append({
            "amount": amount,
            "description": description,
            "remaining": self.current_budget,
        })
        self.update_state(
            current_budget=self.current_budget,
            expense_log=self.expense_log[-10:],  # 최근 10건만
        )

        logger.info(f"[{self.name}] 지출 기록: {amount:,}원 ({description}) — 잔액: {self.current_budget:,}원")

        # 임계값 체크
        self._check_thresholds()

        return f"지출 기록: {amount:,}원, 잔액: {self.current_budget:,}원"

    def _record_revenue(self, payload: dict) -> str:
        """수익 기록."""
        amount = payload.get("amount", 0)
        description = payload.get("description", "")

        self.total_revenue += amount
        self.current_budget += amount
        self.revenue_log.append({
            "amount": amount,
            "description": description,
            "total": self.total_revenue,
        })
        self.update_state(
            current_budget=self.current_budget,
            total_revenue=self.total_revenue,
            revenue_log=self.revenue_log[-10:],
        )

        logger.info(f"[{self.name}] 수익 기록: {amount:,}원 ({description}) — 총수익: {self.total_revenue:,}원")

        # System CFO에게 수익 보고
        self.send(
            to="system_cfo",
            msg_type=MsgType.REPORT,
            payload={
                "summary": "수익 발생",
                "company_id": self.company_id,
                "revenue": amount,
                "total_revenue": self.total_revenue,
                "current_budget": self.current_budget,
            },
        )

        return f"수익 기록: {amount:,}원, 총수익: {self.total_revenue:,}원"

    # ──────────────────────────────────────────
    # 태스크 처리
    # ──────────────────────────────────────────

    def _handle_task(self, msg: Message) -> str:
        task = msg.payload.get("task", "")

        if task == "financial_report":
            return self._generate_report()
        elif task == "write_retrospective":
            return self._write_retrospective(msg.payload)
        else:
            response = self.think(
                f"재무 태스크: {task}\n내용: {json.dumps(msg.payload, ensure_ascii=False)}"
            )
            return response

    def _generate_report(self) -> str:
        """재무 보고서 생성 → System CFO에게 전송."""
        if self.initial_budget == 0:
            return "예산 정보 없음"

        budget_ratio = self.current_budget / self.initial_budget
        total_expense = self.initial_budget - self.current_budget + self.total_revenue

        prompt = f"""현재 재무 상태를 분석하고 보고서를 작성하세요.

회사: {self.company_id}
초기 예산: {self.initial_budget:,}원
현재 잔액: {self.current_budget:,}원
총 수익: {self.total_revenue:,}원
총 지출: {total_expense:,}원
예산 소진율: {(1 - budget_ratio) * 100:.1f}%
최근 지출: {json.dumps(self.expense_log[-5:], ensure_ascii=False)}
최근 수익: {json.dumps(self.revenue_log[-5:], ensure_ascii=False)}

재무 상태를 평가하고 권고 사항을 제시하세요."""

        try:
            analysis = self.think_json(prompt)
        except ValueError:
            analysis = {
                "assessment": "분석 실패",
                "risk_level": "medium",
                "recommendation": "수동 확인 필요",
                "action_needed": True,
            }

        # System CFO에게 보고
        self.send(
            to="system_cfo",
            msg_type=MsgType.REPORT,
            payload={
                "summary": "정기 재무 보고",
                "company_id": self.company_id,
                "initial_budget": self.initial_budget,
                "current_budget": self.current_budget,
                "total_revenue": self.total_revenue,
                "analysis": analysis,
            },
        )

        return f"재무 보고 완료: {analysis.get('assessment', '')}"

    def _write_retrospective(self, payload: dict) -> str:
        """재무 회고 작성."""
        outcome = payload.get("outcome", "unknown")
        roi = ((self.total_revenue - self.initial_budget) / self.initial_budget * 100) if self.initial_budget > 0 else 0

        prompt = f"""이번 사업의 재무 회고를 작성하세요.

회사: {self.company_id}
결과: {outcome}
초기 예산: {self.initial_budget:,}원
최종 잔액: {self.current_budget:,}원
총 수익: {self.total_revenue:,}원
ROI: {roi:.1f}%
총 지출 내역: {json.dumps(self.expense_log, ensure_ascii=False)}

재무 관점에서 솔직한 회고를 작성하세요.

JSON으로 응답:
{{
  "roi": {roi:.1f},
  "financial_summary": "재무 요약",
  "cost_efficiency": "비용 효율성 평가",
  "lessons": ["재무 교훈1", "재무 교훈2"],
  "next_budget_recommendation": 다음_예산_권고액(숫자)
}}"""

        try:
            retro = self.think_json(prompt)
        except ValueError as e:
            logger.error(f"[{self.name}] 재무 회고 실패: {e}")
            return "재무 회고 실패"

        # Board에 회고 보고
        self.send(
            to="board",
            msg_type=MsgType.REPORT,
            payload={
                "summary": f"재무 회고 완료: ROI {roi:.1f}%",
                "company_id": self.company_id,
                "retrospective": retro,
            },
        )

        logger.info(f"[{self.name}] 재무 회고 완료. ROI: {roi:.1f}%")
        return f"재무 회고 완료: ROI {roi:.1f}%"

    # ──────────────────────────────────────────
    # 소액 승인 처리
    # ──────────────────────────────────────────

    def _handle_approval_req(self, msg: Message) -> str:
        """CEO로부터 소액 지출 승인 요청."""
        amount = msg.payload.get("amount", 0)
        description = msg.payload.get("description", "")

        # 소액 (예산의 5% 이하) → CFO 자율 승인
        threshold = self.initial_budget * 0.05
        if amount <= threshold and self.current_budget >= amount:
            self._record_expense({"amount": amount, "description": description})
            self.send(
                to=msg.from_agent,
                msg_type=MsgType.APPROVAL_RES,
                payload={"approved": True, "reason": f"소액 자율 승인 ({amount:,}원)"},
                priority=3,
            )
            return f"소액 승인: {amount:,}원"
        else:
            # 임계값 초과 → Board로 에스컬레이션
            self.send(
                to="board",
                msg_type=MsgType.APPROVAL_REQ,
                payload={
                    "type": "large_expense",
                    "company_id": self.company_id,
                    "amount": amount,
                    "description": description,
                    "current_budget": self.current_budget,
                },
                priority=2,
            )
            return f"Board 에스컬레이션: {amount:,}원 지출 요청"

    # ──────────────────────────────────────────
    # 임계값 체크
    # ──────────────────────────────────────────

    def _check_thresholds(self):
        if self.initial_budget == 0:
            return

        ratio = self.current_budget / self.initial_budget

        if ratio <= CRITICAL_THRESHOLD:
            self.alert(
                urgency=Urgency.ABSOLUTE,
                title=f"[재무 위기] {self.company_id} 예산 {ratio*100:.1f}% 남음",
                body=f"잔액: {self.current_budget:,}원 / 초기: {self.initial_budget:,}원\n즉시 조치 필요.",
            )
            # System CFO에게도 긴급 보고
            self.send(
                to="system_cfo",
                msg_type=MsgType.ALERT,
                payload={
                    "urgency": Urgency.ABSOLUTE,
                    "title": f"{self.company_id} 재무 위기",
                    "body": f"잔액 {ratio*100:.1f}% — 즉시 조치 필요",
                },
                priority=1,
            )

        elif ratio <= FAILURE_THRESHOLD:
            self.send(
                to="system_cfo",
                msg_type=MsgType.REPORT,
                payload={
                    "summary": f"예산 경고: {ratio*100:.1f}% 남음",
                    "company_id": self.company_id,
                    "current_budget": self.current_budget,
                    "initial_budget": self.initial_budget,
                    "risk_level": "high",
                },
                priority=2,
            )
            logger.warning(f"[{self.name}] 예산 경고: {ratio*100:.1f}% 남음")
