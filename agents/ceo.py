"""
CEO 에이전트.
성향(장인형/해커형/분석가형) 랜덤 부여, 업종 선택, 사업 실행 담당.
"""

import json
import random
import logging
from typing import Optional

from agents.base_agent import BaseAgent
from core.message_bus import Message, MsgType, Urgency
from core.persona_loader import PersonaLoader
from core.memory_loader import MemoryLoader
from core.identity_loader import IdentityLoader
from core.behavior_loader import BehaviorLoader
from core.email_action import EmailAction, EmailDraft
from core.glasswing import GlasswingManager

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────
# CEO 성향 정의
# ──────────────────────────────────────────

PERSONALITIES = {
    "장인형": {
        "description": "품질과 완성도를 최우선. 느리지만 안정적. 리스크 회피 성향.",
        "risk_tolerance": 0.2,       # 낮음
        "decision_speed": 0.3,       # 느림
        "exit_threshold": 0.8,       # 높은 가치에서만 Exit
        "preferred_sectors": ["콘텐츠 제작", "전문 서비스", "SaaS"],
    },
    "해커형": {
        "description": "빠른 실행과 피벗. 높은 리스크 허용. 속도 우선.",
        "risk_tolerance": 0.8,       # 높음
        "decision_speed": 0.9,       # 빠름
        "exit_threshold": 0.4,       # 빠른 Exit 선호
        "preferred_sectors": ["자동화 스크립트", "정보 중개", "AI 콘텐츠"],
    },
    "분석가형": {
        "description": "데이터 기반 의사결정. 균형 잡힌 리스크. 철저한 검증.",
        "risk_tolerance": 0.5,       # 중간
        "decision_speed": 0.5,       # 중간
        "exit_threshold": 0.6,       # 적정 가치에서 Exit
        "preferred_sectors": ["프롬프트 판매", "정보 중개", "자동화 스크립트"],
    },
}


# ──────────────────────────────────────────
# 판단 임계값
# ──────────────────────────────────────────

BUDGET_FAILURE_THRESHOLD = 0.15   # 예산 15% 이하 → 실패 판단
BUDGET_SUCCESS_THRESHOLD = 2.0    # 초기 예산 대비 200% 수익 → 성공 판단
MAX_EXECUTION_TICKS = 60          # 최대 실행 루프 횟수 (poll_interval * 60)
EVALUATION_INTERVAL = 5           # 매 5 루프마다 진행 평가


class CEOAgent(BaseAgent):
    def __init__(self, name: str, db_path: str, llm, cfo_name: str = None, personality: Optional[str] = None, persona_loader: PersonaLoader = None, poll_interval: float = 5.0):
        super().__init__(name, db_path, llm, poll_interval)
        self._persona_loader = persona_loader
        self._memory_loader: MemoryLoader = None  # main.py에서 주입
        self._identity_loader = IdentityLoader()
        self._behavior_loader = BehaviorLoader()
        self._email_action: EmailAction = None   # main.py에서 주입
        self._glasswing: GlasswingManager = None  # main.py에서 주입

        # 성향 랜덤 부여 (또는 지정)
        self.personality_name = personality or random.choice(list(PERSONALITIES.keys()))
        self.personality = PERSONALITIES[self.personality_name]

        # agency-agents 페르소나 키 매핑
        self._persona_key_map = {
            "해커형": "ceo_hacker",
            "장인형": "ceo_craftsman",
            "분석가형": "ceo_analyst",
        }

        # CEO 상태
        self.company_id: Optional[str] = None
        self.sector: Optional[str] = None
        self.stage: str = "idle"           # idle | planning | executing | reporting
        self.budget: int = 0
        self.cfo_name: str = cfo_name  # 담당 CFO 이름
        self.retrospectives: list[dict] = []  # 과거 회고 목록
        self._execution_ticks: int = 0             # 실행 루프 카운터
        self._total_revenue: int = 0               # 수익 추적 (CFO 보고 기반)
        self.brand_name: str = ""                  # 브랜드명 (업종 선택 시 결정)
        self.target: dict = {}                     # 타겟 고객 설정

        logger.info(f"[{self.name}] 성향: {self.personality_name} - {self.personality['description']}")

    @property
    def system_prompt(self) -> str:
        persona_key = self._persona_key_map.get(self.personality_name, "ceo_analyst")

        # 1. Identity (agency-agents에서 추출된 표준 JSON)
        identity_block = self._identity_loader.to_prompt(persona_key)
        if not identity_block:
            # fallback: identity 없으면 성향 텍스트로 대체
            identity_block = f"""## Identity: {self.personality_name}
Core Value     : {self.personality['description']}
Risk Stance    : 리스크 허용도 {self.personality['risk_tolerance']} (0=낮음, 1=높음)
"""

        # 2. ACS 강제 행동 (behaviors/ceo.md)
        behavior = self._behavior_loader.load("ceo")

        # 3. 현재 상태 (동적)
        acs_rules = f"""
## Your Role in ACS
이름: {self.name} | 성향: {self.personality_name}
선호 업종: {', '.join(self.personality['preferred_sectors'])}
담당 CFO: {self.cfo_name or 'cfo_' + self.name}

## Current State
회사: {self.company_id or '없음'} | 업종: {self.sector or '미정'}
단계: {self.stage} | 예산: {self.budget:,}원
"""

        # 3. 기억 (L2 + L3, 압축)
        memory = ""
        if self._memory_loader:
            memory = self._memory_loader.load_for_ceo(self.name)
        memory_block = f"""
## Learned Memory
{memory if memory else "(첫 창업 - 기억 없음)"}
"""

        return identity_block + "\n\n" + behavior + "\n\n" + acs_rules + memory_block

    def handle_message(self, msg: Message) -> Optional[str]:
        if msg.msg_type == MsgType.TASK:
            return self._handle_task(msg)
        elif msg.msg_type == MsgType.APPROVAL_RES:
            return self._handle_approval_response(msg)
        elif msg.msg_type == MsgType.REPORT:
            return self._handle_report(msg)
        else:
            logger.warning(f"[{self.name}] 알 수 없는 메시지 타입: {msg.msg_type}")
            return None

    # ──────────────────────────────────────────
    # 태스크 처리
    # ──────────────────────────────────────────

    def _handle_task(self, msg: Message) -> str:
        task = msg.payload.get("task", "")
        logger.info(f"[{self.name}] 태스크 수신: {task}")

        if task == "start_company":
            return self._start_company(msg.payload)
        elif task == "execute_plan":
            return self._execute_plan(msg.payload)
        elif task == "write_retrospective":
            return self._write_retrospective(msg.payload)
        elif task == "evaluate_progress":
            return self._evaluate_progress()
        else:
            # 일반 태스크 → LLM 판단
            response = self.think(f"다음 태스크를 수행하세요: {task}\n\n추가 정보: {json.dumps(msg.payload, ensure_ascii=False)}")
            return response

    def _start_company(self, payload: dict) -> str:
        """회사 창업 시작."""
        self.company_id = payload.get("company_id", f"company_{self.name}")
        self.budget = payload.get("budget", 0)
        self.target = payload.get("target", {})
        available_sectors = payload.get("available_sectors", self.personality["preferred_sectors"])
        self.stage = "planning"
        self.update_state(
            company_id=self.company_id,
            stage=self.stage,
            budget=self.budget,
        )

        prompt = f"""회사 창업을 시작합니다.

회사 ID: {self.company_id}
초기 예산: {self.budget:,}원
가용 업종 목록: {json.dumps(available_sectors, ensure_ascii=False)}

타겟 고객: {self.target.get('region', '한국')} / {self.target.get('customer_type', '국내 사업자')}
연락 수단: {self.target.get('contact_channels', '이메일')}

당신의 성향({self.personality_name})에 맞는 업종을 선택하고 초기 사업 계획을 수립하세요.

다음 JSON으로 응답하세요:
{{
  "selected_sector": "선택한 업종",
  "brand_name": "브랜드명 (한국어 또는 영어, 2-4음절, 기억하기 쉽고 자연스럽게)",
  "reason": "선택 이유 (성향 반영)",
  "plan": {{
    "goal": "3개월 목표",
    "first_actions": ["첫 번째 액션", "두 번째 액션", "세 번째 액션"],
    "expected_revenue": 예상수익(숫자),
    "risks": ["리스크1", "리스크2"]
  }}
}}"""

        try:
            decision = self.think_json(prompt)
        except ValueError as e:
            logger.error(f"[{self.name}] 업종 선택 실패: {e}")
            self.alert(Urgency.HIGH, f"{self.name} 업종 선택 실패", str(e))
            return "업종 선택 실패"

        self.sector = decision.get("selected_sector")
        self.brand_name = decision.get("brand_name", "")
        self._pending_plan = decision.get("plan")  # 승인 응답 시 사용
        self.update_state(sector=self.sector, brand_name=self.brand_name, plan=self._pending_plan)
        logger.info(f"[{self.name}] 브랜드명: {self.brand_name}")

        # Board에 업종 승인 요청
        self.send(
            to="board",
            msg_type=MsgType.APPROVAL_REQ,
            payload={
                "type": "sector_selection",
                "company_id": self.company_id,
                "selected_sector": self.sector,
                "reason": decision.get("reason"),
                "plan": decision.get("plan"),
            },
            priority=3,
        )

        # Company CFO에게 예산 통보
        self.send(
            to=self.cfo_name or f"cfo_{self.name}",
            msg_type=MsgType.REPORT,
            payload={
                "summary": "창업 시작 - 초기 예산 배정",
                "company_id": self.company_id,
                "budget": self.budget,
                "sector": self.sector,
            },
        )

        logger.info(f"[{self.name}] 업종 선택: {self.sector} - Board 승인 요청 완료")
        return f"창업 시작: {self.sector} 업종 선택, Board 승인 대기 중"

    def _execute_plan(self, payload: dict) -> str:
        """Board 승인 후 실행 단계."""
        self.stage = "executing"
        self.update_state(stage=self.stage)

        # 이메일 작성 전용 페르소나 로드
        email_identity = self._identity_loader.load("email_writer") if self._identity_loader else {}
        email_rules = email_identity.get("email_rules", {})

        prompt = f"""업종 '{self.sector}' 사업이 승인되었습니다. 예산 {self.budget:,}원으로 실행을 시작합니다.

현재 사업 계획: {json.dumps(payload.get('plan', {}), ensure_ascii=False)}
브랜드명: {self.brand_name}

첫 번째 외부 액션으로 이메일을 작성하세요.
이 이메일은 실제로 발송됩니다.

이메일 작성 규칙 (반드시 따를 것):
제목: {email_rules.get("subject_line", "3-5단어, 소문자, 내부 이메일처럼 보일 것")}
오프닝 금지: {email_rules.get("opening_bad", ["안녕하세요로 시작", "저희 서비스를 소개합니다"])}
오프닝 방식: {email_rules.get("opening_good", "상대방 상황을 구체적으로 언급")}
본문: {email_rules.get("body", "한 문장으로 핵심 가치 전달")}
CTA 금지: {email_rules.get("cta_bad", "30분 미팅 요청")}
CTA 방식: {email_rules.get("cta_good", "15분 대화 제안")}
톤: {email_identity.get("tone", "직접적이고 인간적으로. 마케팅 문구 금지.")}

추가 원칙:
- 수신자: {self.target.get('region', '한국')} / {self.target.get('customer_type', '국내 사업자')}
- 언어: {self.target.get('language', '한국어')}로 작성
- 연락 수단: 본문 또는 서명에 {self.target.get('contact_channels', '이메일')} 언급
- 톤: {self.target.get('tone', '정중하되 간결하게')}
- AI가 작성했다는 티 절대 금지
- 당신의 성향({self.personality_name})이 문체에 자연스럽게 반영될 것
- 이모지, 과도한 포맷 금지

다음 JSON으로 응답하세요:
{{
  "actions": [
    {{
      "description": "액션 설명",
      "action_type": "outreach | listing | post | newsletter",
      "requires_approval": true,
      "estimated_cost": 비용(숫자),
      "estimated_revenue": 예상수익(숫자),
      "email_draft": {{
        "to": "수신자 이메일 (실제 대상이 없으면 owner)",
        "subject": "이메일 제목 (자연스럽게, 스팸 느낌 없이)",
        "body": "이메일 본문 (실제 발송될 완성된 내용)"
      }}
    }}
  ],
  "status_update": "현재 상태 요약"
}}"""

        try:
            execution = self.think_json(prompt)
        except ValueError as e:
            logger.error(f"[{self.name}] 실행 계획 수립 실패: {e}")
            return "실행 계획 수립 실패"

        # 액션 처리
        for action in execution.get("actions", []):
            email_draft_data = action.get("email_draft", {})

            # 이메일 초안이 있으면 HOTL 알림 + 이메일로 위진수에게 전달
            if email_draft_data and self._email_action:
                draft = EmailDraft(
                    subject=email_draft_data.get("subject", ""),
                    body=email_draft_data.get("body", ""),
                    to=email_draft_data.get("to", ""),
                    action_type=action.get("action_type", "outreach"),
                    company_id=self.company_id,
                    ceo_name=self.name,
                    estimated_revenue=action.get("estimated_revenue", 0),
                    brand_name=self.brand_name,
                )

                # Glasswing Stage에 따라 처리 방식 결정
                stage = self._glasswing.get_stage() if self._glasswing else 1

                if stage >= 3:
                    # Stage 3+: 자동 발송
                    success = self._email_action.send(draft)
                    logger.info(f"[{self.name}] 이메일 자동 발송: {draft.subject} ({'성공' if success else '실패'})")
                else:
                    # Stage 1~2: Gmail 검토 요청 + Discord 승인 버튼
                    self._email_action.send_to_owner(draft)
                    logger.info(f"[{self.name}] 이메일 검토 요청: {draft.subject}")

                    # Discord HOTL 승인 알림
                    import json as _json
                    self.alert(
                        urgency=Urgency.HIGH,
                        title=f"[이메일 승인 요청] {draft.subject[:40]}",
                        body=_json.dumps({
                            "type": "email_approval",
                            "company_id": self.company_id,
                            "subject": draft.subject,
                            "to": draft.to,
                            "estimated_revenue": draft.estimated_revenue,
                            "preview": draft.body[:200],
                        }, ensure_ascii=False),
                    )

                # Board에 승인 요청 (이메일 내용 포함)
                self.send(
                    to="board",
                    msg_type=MsgType.APPROVAL_REQ,
                    payload={
                        "type": action.get("action_type", "external_action"),
                        "company_id": self.company_id,
                        "action": action["description"],
                        "cost": action.get("estimated_cost", 0),
                        "estimated_revenue": action.get("estimated_revenue", 0),
                        "email_subject": draft.subject,
                        "stage": stage,
                    },
                    priority=4,
                )

            elif action.get("requires_approval") and action.get("estimated_cost", 0) > 0:
                # 이메일 없는 일반 액션
                self.send(
                    to="board",
                    msg_type=MsgType.APPROVAL_REQ,
                    payload={
                        "type": "external_action",
                        "company_id": self.company_id,
                        "action": action["description"],
                        "cost": action.get("estimated_cost", 0),
                        "expected_outcome": action.get("expected_outcome"),
                    },
                    priority=4,
                )

        # Board에 진행 보고
        self.send(
            to="board",
            msg_type=MsgType.REPORT,
            payload={
                "summary": execution.get("status_update", "실행 중"),
                "company_id": self.company_id,
                "sector": self.sector,
                "stage": self.stage,
            },
        )

        return f"실행 시작: {execution.get('status_update', '')}"

    # ──────────────────────────────────────────
    # 실행 루프 오버라이드 (진행 평가 추가)
    # ──────────────────────────────────────────

    def _loop(self):
        while self._running:
            import time
            try:
                messages = self.bus.receive(self.name)
                for msg in messages:
                    self._process(msg)

                # Heartbeat
                self.bus.conn.execute(
                    "UPDATE agent_states SET updated_at=datetime('now') WHERE agent_name=?",
                    (self.name,)
                )
                self.bus.conn.commit()

                # 실행 중일 때만 주기적 평가
                if self.stage == "executing":
                    self._execution_ticks += 1
                    if self._execution_ticks % EVALUATION_INTERVAL == 0:
                        self._evaluate_progress()

            except Exception as e:
                logger.error(f"[{self.name}] loop error: {e}")
            time.sleep(self.poll_interval)

    def _evaluate_progress(self) -> str:
        """현재 진행 상황 평가 → 성공/실패 판단."""
        if self.stage != "executing" or not self.company_id:
            return "평가 스킵 (실행 중 아님)"

        cfo_state = self.bus.get_agent_state(self.cfo_name or f"cfo_{self.name}") or {}
        current_budget = cfo_state.get("current_budget", self.budget)
        initial_budget = cfo_state.get("initial_budget", self.budget) or self.budget
        total_revenue = cfo_state.get("total_revenue", self._total_revenue)

        if initial_budget <= 0:
            return "평가 스킵 (예산 정보 없음)"

        budget_ratio = current_budget / initial_budget
        revenue_ratio = total_revenue / initial_budget if initial_budget > 0 else 0

        logger.info(
            f"[{self.name}] 진행 평가: 예산 {budget_ratio*100:.0f}% 잔여 / "
            f"수익 {revenue_ratio*100:.0f}% / 틱 {self._execution_ticks}"
        )

        # 성공 판단
        if revenue_ratio >= BUDGET_SUCCESS_THRESHOLD:
            logger.info(f"[{self.name}] 성공 조건 달성! ROI {(revenue_ratio-1)*100:.0f}%")
            self._trigger_retrospective("success", current_budget, total_revenue)
            return f"성공 판단: ROI {(revenue_ratio-1)*100:.0f}%"

        # 실패 판단 1: 예산 소진
        if budget_ratio <= BUDGET_FAILURE_THRESHOLD:
            logger.warning(f"[{self.name}] 실패 판단: 예산 {budget_ratio*100:.0f}% 남음")
            self._trigger_retrospective("failure_budget", current_budget, total_revenue)
            return f"실패 판단: 예산 소진 ({budget_ratio*100:.0f}% 남음)"

        # 실패 판단 2: 최대 실행 시간 초과 + 수익 없음
        if self._execution_ticks >= MAX_EXECUTION_TICKS and total_revenue == 0:
            logger.warning(f"[{self.name}] 실패 판단: {self._execution_ticks}틱 경과, 수익 없음")
            self._trigger_retrospective("failure_timeout", current_budget, total_revenue)
            return f"실패 판단: 타임아웃 ({self._execution_ticks}틱)"

        return f"진행 중: 예산 {budget_ratio*100:.0f}% / 수익 {total_revenue:,}원"

    def _trigger_retrospective(self, outcome: str, current_budget: int, total_revenue: int):
        """회고 태스크 자신에게 전송."""
        self.stage = "reporting"
        self.update_state(stage=self.stage)

        roi = ((total_revenue - (self.budget - current_budget)) / self.budget * 100) if self.budget > 0 else 0

        self.send(
            to=self.name,
            msg_type=MsgType.TASK,
            payload={
                "task": "write_retrospective",
                "outcome": "success" if "success" in outcome else "failure",
                "outcome_detail": outcome,
                "company_id": self.company_id,
                "sector": self.sector,
                "initial_budget": self.budget,
                "final_budget": current_budget,
                "total_revenue": total_revenue,
                "roi": round(roi, 1),
                "execution_ticks": self._execution_ticks,
            },
            priority=2,
        )
        logger.info(f"[{self.name}] 회고 트리거: {outcome} / ROI {roi:.1f}%")

    def _handle_approval_response(self, msg: Message) -> str:
        """Board로부터 승인 응답 수신."""
        approved = msg.payload.get("approved", False)
        reason = msg.payload.get("reason", "")
        action = msg.payload.get("action", "")

        if approved:
            logger.info(f"[{self.name}] 승인됨: {reason}")
            # 업종 승인이면 실행 단계로
            if self.stage == "planning":
                plan = getattr(self, '_pending_plan', {}) or {}
                self.send(
                    to=self.name,
                    msg_type=MsgType.TASK,
                    payload={"task": "execute_plan", "plan": plan},
                )
            return f"승인 수신: {reason}"
        else:
            logger.warning(f"[{self.name}] 거절됨: {reason}")
            # 거절 시 재계획
            if "escalated" not in reason:
                self.stage = "planning"
                self.update_state(stage=self.stage)
            return f"거절 수신: {reason}"

    def _write_retrospective(self, payload: dict) -> str:
        """회고 작성 (성공/실패 후)."""
        outcome = payload.get("outcome", "unknown")

        prompt = f"""이번 사업을 회고합니다.

회사: {self.company_id}
업종: {self.sector}
결과: {outcome}
세부 내용: {json.dumps(payload, ensure_ascii=False)}

당신의 성향({self.personality_name}) 관점에서 솔직한 회고를 작성하세요.

다음 JSON으로 응답하세요:
{{
  "outcome": "{outcome}",
  "what_worked": ["잘 된 점1", "잘 된 점2"],
  "what_failed": ["실패 원인1", "실패 원인2"],
  "lessons": ["교훈1", "교훈2"],
  "next_strategy": "다음 창업 전략"
}}"""

        try:
            retro = self.think_json(prompt)
        except ValueError as e:
            logger.error(f"[{self.name}] 회고 작성 실패: {e}")
            return "회고 작성 실패"

        self.retrospectives.append(retro)
        self.update_state(retrospectives=self.retrospectives)

        # Board에 회고 보고
        self.send(
            to="board",
            msg_type=MsgType.REPORT,
            payload={
                "summary": f"회고 완료: {outcome}",
                "company_id": self.company_id,
                "retrospective": retro,
            },
        )

        # 상태 초기화 (다음 창업 준비)
        self.company_id = None
        self.sector = None
        self.stage = "idle"
        self.budget = 0
        self.update_state(company_id=None, sector=None, stage="idle", budget=0)

        logger.info(f"[{self.name}] 회고 완료. 다음 창업 대기 중.")
        return f"회고 완료: {retro.get('next_strategy', '')}"

    def _handle_report(self, msg: Message) -> str:
        """CFO 등으로부터 보고 수신."""
        summary = msg.payload.get("summary", "")
        logger.info(f"[{self.name}] 보고 수신 from {msg.from_agent}: {summary}")

        # 수익 보고 수신 시 추적
        if "수익" in summary or "revenue" in summary.lower():
            revenue = msg.payload.get("amount", 0)
            self._total_revenue += revenue
            self.update_state(total_revenue=self._total_revenue)
            logger.info(f"[{self.name}] 누적 수익: {self._total_revenue:,}원")

        return f"보고 수신: {msg.from_agent}"
