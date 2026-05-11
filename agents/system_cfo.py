"""
System CFO 에이전트.
포트폴리오 전체 자본 관리, 회사별 예산 배분,
Meta-Learning Loop, 실패 임계값 감지 담당.
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

logger = logging.getLogger(__name__)


# 포트폴리오 상수
DEFAULT_BUDGET_RATIO = 0.2      # 회사당 전체 자본의 20% 배분
MAX_COMPANIES = 5               # 동시 운영 최대 회사 수
RESERVE_RATIO = 0.2             # 전체 자본의 20%는 항상 예비비로 유지
OPS_BUDGET_RATIO = 0.05         # 전체 자본의 5%는 ACS 운영 예산 (API 비용 등)


class SystemCFOAgent(BaseAgent):
    def __init__(self, name: str, db_path: str, llm, initial_capital: int = 0, persona_loader: PersonaLoader = None, poll_interval: float = 5.0):
        super().__init__(name, db_path, llm, poll_interval)
        self._persona_loader = persona_loader
        self._memory_loader: MemoryLoader = None
        self._identity_loader = IdentityLoader()
        self._behavior_loader = BehaviorLoader()

        # 자본 현황
        self.total_capital: int = initial_capital
        self.ops_budget: int = int(initial_capital * OPS_BUDGET_RATIO)
        self.reserve: int = int(initial_capital * RESERVE_RATIO)
        self.deployable: int = initial_capital - self.ops_budget - self.reserve

        # 포트폴리오
        self.companies: dict[str, dict] = {}   # company_id → 재무 정보
        self.sector_db: dict[str, dict] = {}   # 업종 → 성공률/회수기간 등

        # 성과 추적
        self.total_invested: int = 0
        self.total_returned: int = 0
        self.exit_count: int = 0
        self.failure_count: int = 0

        logger.info(f"[{self.name}] 초기 자본: {self.total_capital:,}원 / 운용 가능: {self.deployable:,}원")

    @property
    def system_prompt(self) -> str:
        portfolio_summary = json.dumps(
            {cid: {"budget": v["budget"], "revenue": v.get("revenue", 0), "sector": v.get("sector", "?")}
             for cid, v in self.companies.items()},
            ensure_ascii=False
        )
        identity_block = self._identity_loader.to_prompt("system_cfo")
        if not identity_block:
            identity_block = "## Identity: Portfolio Financial Controller\nCore Value: Capital preservation and disciplined allocation across the portfolio.\n"

        behavior = self._behavior_loader.load("system_cfo")
        prefix = identity_block + "\n\n" + behavior + "\n\n## Portfolio State\n"
        return prefix + f"""

자본 현황:
- 총 자본: {self.total_capital:,}원
- 운용 가능: {self.deployable:,}원
- 예비비: {self.reserve:,}원
- 운영 예산(API 등): {self.ops_budget:,}원

포트폴리오:
{portfolio_summary}

성과:
- 총 투자: {self.total_invested:,}원
- 총 회수: {self.total_returned:,}원
- Exit 횟수: {self.exit_count}
- 실패 횟수: {self.failure_count}

업종 DB (Meta-Learning):
{json.dumps(self.sector_db, ensure_ascii=False)}

역할:
- 포트폴리오 전체 자본 관리 및 예산 배분
- Company CFO 재무 보고 수신 및 집계
- 실패 임계값 감지 → Board 에스컬레이션
- Meta-Learning Loop: 업종별 성공률/회수기간 갱신
- Board에 포트폴리오 분산 기준 제공

재무 원칙:
- 회사당 배분: 운용 가능 자본의 {DEFAULT_BUDGET_RATIO*100:.0f}% 이하
- 예비비 {RESERVE_RATIO*100:.0f}% 항상 유지
- 추측 기반 결정 금지 (데이터 없으면 에스컬레이션)
- CEO 직접 Financial Gateway 접근 불가"""

    def handle_message(self, msg: Message) -> Optional[str]:
        if msg.msg_type == MsgType.REPORT:
            return self._handle_report(msg)
        elif msg.msg_type == MsgType.TASK:
            return self._handle_task(msg)
        elif msg.msg_type == MsgType.ALERT:
            return self._handle_alert(msg)
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
        summary = payload.get("summary", "")

        if "창업" in summary or "초기 예산" in summary:
            return self._on_company_created(payload)
        elif "수익" in summary:
            return self._on_revenue(payload)
        elif "재무 보고" in summary:
            return self._on_financial_report(payload)
        elif "회고" in summary:
            return self._on_retrospective(payload)
        elif "경고" in summary or "위기" in summary:
            return self._on_financial_warning(payload)
        else:
            logger.info(f"[{self.name}] 보고 수신: {summary}")
            return f"보고 수신: {summary}"

    def _on_company_created(self, payload: dict) -> str:
        company_id = payload.get("company_id")
        budget = payload.get("initial_budget", 0)

        self.companies[company_id] = {
            "ceo": payload.get("ceo"),
            "budget": budget,
            "revenue": 0,
            "sector": payload.get("sector", "미정"),
            "status": "active",
        }
        self.total_invested += budget
        self._sync_capital()

        logger.info(f"[{self.name}] 새 회사 등록: {company_id} ({budget:,}원)")
        return f"회사 등록: {company_id}"

    def _on_revenue(self, payload: dict) -> str:
        company_id = payload.get("company_id")
        revenue = payload.get("revenue", 0)

        if company_id in self.companies:
            self.companies[company_id]["revenue"] = payload.get("total_revenue", revenue)

        self.total_returned += revenue
        self._sync_capital()

        logger.info(f"[{self.name}] 수익 집계: {company_id} +{revenue:,}원")
        return f"수익 집계: {revenue:,}원"

    def _on_financial_report(self, payload: dict) -> str:
        company_id = payload.get("company_id")
        analysis = payload.get("analysis", {})
        risk_level = analysis.get("risk_level", "low")

        if company_id in self.companies:
            self.companies[company_id]["last_report"] = analysis

        # 위험도 높으면 Board에 에스컬레이션
        if risk_level in ("high", "critical"):
            self.send(
                to="board",
                msg_type=MsgType.REPORT,
                payload={
                    "summary": f"포트폴리오 경고: {company_id} 위험도 {risk_level}",
                    "company_id": company_id,
                    "analysis": analysis,
                },
                priority=2,
            )

        self._sync_capital()
        return f"재무 보고 집계: {company_id} (위험도: {risk_level})"

    def _on_financial_warning(self, payload: dict) -> str:
        company_id = payload.get("company_id")
        risk_level = payload.get("risk_level", "high")

        logger.warning(f"[{self.name}] 재무 경고: {company_id} ({risk_level})")

        # Board에 즉시 보고
        self.send(
            to="board",
            msg_type=MsgType.REPORT,
            payload={
                "summary": f"재무 경고: {company_id}",
                "company_id": company_id,
                "current_budget": payload.get("current_budget"),
                "initial_budget": payload.get("initial_budget"),
                "risk_level": risk_level,
            },
            priority=1,
        )
        return f"재무 경고 전달: {company_id}"

    def _on_retrospective(self, payload: dict) -> str:
        """회사 종료 후 Meta-Learning Loop 실행."""
        company_id = payload.get("company_id")
        retro = payload.get("retrospective", {})
        outcome = retro.get("outcome", "unknown")

        # 성과 집계
        if outcome == "success":
            self.exit_count += 1
        else:
            self.failure_count += 1

        # 회사 상태 업데이트
        if company_id in self.companies:
            sector = self.companies[company_id].get("sector", "unknown")
            self.companies[company_id]["status"] = "closed"

            # Meta-Learning: 업종 DB 갱신
            self._update_sector_db(sector, outcome, retro)

        self._sync_capital()
        self._run_meta_learning()

        return f"회고 수신 및 Meta-Learning 완료: {company_id} ({outcome})"

    # ──────────────────────────────────────────
    # 태스크 처리
    # ──────────────────────────────────────────

    def _handle_task(self, msg: Message) -> str:
        task = msg.payload.get("task", "")

        if task == "allocate_budget":
            return self._allocate_budget(msg.payload)
        elif task == "portfolio_review":
            return self._portfolio_review()
        elif task == "get_sector_recommendation":
            return self._get_sector_recommendation(msg.payload)
        else:
            response = self.think(
                f"재무 태스크: {task}\n내용: {json.dumps(msg.payload, ensure_ascii=False)}"
            )
            return response

    def _allocate_budget(self, payload: dict) -> str:
        """새 회사에 예산 배분."""
        company_id = payload.get("company_id")
        requested = payload.get("requested_budget", 0)

        # 배분 가능 최대액 계산
        max_allocatable = int(self.deployable * DEFAULT_BUDGET_RATIO)
        allocated = min(requested, max_allocatable)

        if allocated <= 0 or self.deployable < allocated:
            self.send(
                to="board",
                msg_type=MsgType.REPORT,
                payload={
                    "summary": f"예산 배분 불가: {company_id}",
                    "reason": "운용 가능 자본 부족",
                    "deployable": self.deployable,
                    "requested": requested,
                },
                priority=2,
            )
            return f"예산 배분 불가: 잔액 부족 ({self.deployable:,}원)"

        self.deployable -= allocated
        self._sync_capital()

        # Board에 결과 보고
        self.send(
            to="board",
            msg_type=MsgType.APPROVAL_RES,
            payload={
                "approved": True,
                "company_id": company_id,
                "allocated_budget": allocated,
                "remaining_deployable": self.deployable,
            },
            priority=2,
        )

        logger.info(f"[{self.name}] 예산 배분: {company_id} {allocated:,}원 / 잔여 운용: {self.deployable:,}원")
        return f"예산 배분: {company_id} {allocated:,}원"

    def _portfolio_review(self) -> str:
        """포트폴리오 전체 검토."""
        prompt = f"""현재 포트폴리오를 검토하고 전략적 권고를 제시하세요.

총 자본: {self.total_capital:,}원
운용 가능: {self.deployable:,}원
활성 회사: {[cid for cid, v in self.companies.items() if v.get('status') == 'active']}
업종 DB: {json.dumps(self.sector_db, ensure_ascii=False)}
총 투자: {self.total_invested:,}원
총 회수: {self.total_returned:,}원
Exit: {self.exit_count}건 / 실패: {self.failure_count}건

다음 JSON으로 응답:
{{
  "portfolio_health": "good" | "warning" | "critical",
  "summary": "포트폴리오 요약",
  "recommendations": ["권고1", "권고2"],
  "diversification_rules": {{
    "max_per_sector": 최대_동일업종_수(숫자),
    "preferred_sectors": ["선호업종1", "선호업종2"]
  }}
}}"""

        try:
            review = self.think_json(prompt)
        except ValueError as e:
            logger.error(f"[{self.name}] 포트폴리오 검토 실패: {e}")
            return "포트폴리오 검토 실패"

        # Board에 분산 규칙 전달
        self.send(
            to="board",
            msg_type=MsgType.REPORT,
            payload={
                "summary": "포트폴리오 검토 완료",
                "health": review.get("portfolio_health"),
                "recommendations": review.get("recommendations"),
                "diversification_rules": review.get("diversification_rules"),
            },
        )

        self.update_state(last_review=review)
        return f"포트폴리오 검토: {review.get('portfolio_health')} — {review.get('summary', '')}"

    def _get_sector_recommendation(self, payload: dict) -> str:
        """업종 추천 (Board 요청 시)."""
        available_capital = payload.get("available_capital", self.deployable)

        prompt = f"""새 CEO에게 업종을 추천하세요.

운용 가능 자본: {available_capital:,}원
업종 성과 DB: {json.dumps(self.sector_db, ensure_ascii=False)}
현재 활성 업종: {[v.get('sector') for v in self.companies.values() if v.get('status') == 'active']}

자본 조건과 분산 원칙을 고려해 추천 업종 목록을 제시하세요.

JSON으로 응답:
{{
  "recommended_sectors": [
    {{"sector": "업종명", "min_capital": 최소자본, "reason": "추천 이유", "success_rate": 예상성공률}}
  ]
}}"""

        try:
            rec = self.think_json(prompt)
        except ValueError:
            rec = {"recommended_sectors": []}

        # 요청자에게 응답
        self.send(
            to=payload.get("requester", "board"),
            msg_type=MsgType.REPORT,
            payload={
                "summary": "업종 추천",
                "recommendations": rec.get("recommended_sectors", []),
            },
        )

        return f"업종 추천 완료: {len(rec.get('recommended_sectors', []))}개"

    # ──────────────────────────────────────────
    # Meta-Learning
    # ──────────────────────────────────────────

    def _update_sector_db(self, sector: str, outcome: str, retro: dict):
        """업종 DB 갱신 (Meta-Learning)."""
        if sector not in self.sector_db:
            self.sector_db[sector] = {
                "total": 0, "success": 0, "failure": 0,
                "success_rate": 0.0, "avg_roi": 0.0, "lessons": [],
            }

        db = self.sector_db[sector]
        db["total"] += 1

        if outcome == "success":
            db["success"] += 1
        else:
            db["failure"] += 1

        db["success_rate"] = db["success"] / db["total"]

        # 교훈 누적 (최근 5개)
        lessons = retro.get("lessons", [])
        db["lessons"] = (db["lessons"] + lessons)[-5:]

        # ROI 갱신
        roi = retro.get("roi", 0)
        if roi:
            db["avg_roi"] = (db["avg_roi"] * (db["total"] - 1) + roi) / db["total"]

        self.update_state(sector_db=self.sector_db)
        logger.info(f"[{self.name}] 업종 DB 갱신: {sector} (성공률: {db['success_rate']*100:.0f}%)")

    def _run_meta_learning(self):
        """Meta-Learning Loop 실행 — Board에 갱신된 분산 규칙 전달."""
        if not self.sector_db:
            return

        best_sectors = sorted(
            self.sector_db.items(),
            key=lambda x: x[1].get("success_rate", 0),
            reverse=True,
        )[:3]

        self.send(
            to="board",
            msg_type=MsgType.REPORT,
            payload={
                "summary": "Meta-Learning 업데이트",
                "best_sectors": [{"sector": s, "success_rate": d["success_rate"]} for s, d in best_sectors],
                "sector_db": self.sector_db,
            },
        )

        logger.info(f"[{self.name}] Meta-Learning 완료. 최우수 업종: {[s for s, _ in best_sectors]}")

    # ──────────────────────────────────────────
    # 알림 / 승인 처리
    # ──────────────────────────────────────────

    def _handle_alert(self, msg: Message) -> str:
        urgency = msg.payload.get("urgency", Urgency.HIGH)
        title = msg.payload.get("title", "긴급 알림")
        body = msg.payload.get("body", "")
        self.alert(urgency=urgency, title=title, body=body)
        return f"알림 전달: {title}"

    def _handle_approval_req(self, msg: Message) -> str:
        """임계값 이하 소액은 자율 승인, 초과는 위진수 에스컬레이션."""
        amount = msg.payload.get("amount", 0)
        threshold = int(self.total_capital * 0.05)

        if amount <= threshold:
            self.send(
                to=msg.from_agent,
                msg_type=MsgType.APPROVAL_RES,
                payload={"approved": True, "reason": f"System CFO 자율 승인 ({amount:,}원)"},
                priority=2,
            )
            return f"소액 자율 승인: {amount:,}원"
        else:
            self.alert(
                urgency=Urgency.ABSOLUTE,
                title=f"[대규모 투자 승인 필요] {amount:,}원",
                body=json.dumps(msg.payload, ensure_ascii=False),
            )
            return f"대규모 투자 에스컬레이션: {amount:,}원"

    # ──────────────────────────────────────────
    # 내부 유틸
    # ──────────────────────────────────────────

    def _sync_capital(self):
        """자본 현황 상태 동기화."""
        # 첫 실행 시 DB에 상태가 없을 수 있으므로 안전하게 초기화
        if not self.bus.get_agent_state(self.name):
            self.bus.set_agent_state(self.name, "running", {})
        self.update_state(
            total_capital=self.total_capital,
            deployable=self.deployable,
            ops_budget=self.ops_budget,
            reserve=self.reserve,
            total_invested=self.total_invested,
            total_returned=self.total_returned,
            exit_count=self.exit_count,
            failure_count=self.failure_count,
            active_companies=len([v for v in self.companies.values() if v.get("status") == "active"]),
        )
