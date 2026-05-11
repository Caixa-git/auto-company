"""
Glasswing Autonomy Framework.
에이전트 자율성을 단계적으로 관리.
성과에 따라 자동 승급/강등.
"""

import json
import logging
import sqlite3
from dataclasses import dataclass
from typing import Optional

from core.db import get_thread_connection

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────
# 단계 정의
# ──────────────────────────────────────────

@dataclass
class StagePolicy:
    stage: int
    name: str
    description: str
    auto_approve_cost_ratio: float   # 예산 대비 자율 승인 한도
    auto_approve_types: list[str]    # 자율 처리 가능 액션 타입
    hotl_urgency_filter: str         # 이 urgency 이상만 DM 알림 (low/medium/high/absolute)


STAGES: dict[int, StagePolicy] = {
    0: StagePolicy(
        stage=0,
        name="Full Oversight",
        description="모든 액션 위진수 승인 필요",
        auto_approve_cost_ratio=0.0,
        auto_approve_types=[],
        hotl_urgency_filter="low",
    ),
    1: StagePolicy(
        stage=1,
        name="Supervised",
        description="소액/업종 선택 자율, 나머지 승인",
        auto_approve_cost_ratio=0.05,
        auto_approve_types=["sector_selection", "internal_action", "reporting"],
        hotl_urgency_filter="medium",
    ),
    2: StagePolicy(
        stage=2,
        name="Assisted",
        description="중액/실행 계획 자율, Exit만 승인",
        auto_approve_cost_ratio=0.20,
        auto_approve_types=["sector_selection", "internal_action", "reporting", "external_action", "marketing_spend"],
        hotl_urgency_filter="high",
    ),
    3: StagePolicy(
        stage=3,
        name="Autonomous",
        description="대부분 자율, 대규모 투자/Exit만 승인",
        auto_approve_cost_ratio=0.40,
        auto_approve_types=["sector_selection", "internal_action", "reporting", "external_action",
                            "marketing_spend", "small_investment", "hiring_contractor"],
        hotl_urgency_filter="high",
    ),
    4: StagePolicy(
        stage=4,
        name="Full Autonomy",
        description="완전 자율 (Exit/시스템종료 제외)",
        auto_approve_cost_ratio=1.0,
        auto_approve_types=["sector_selection", "internal_action", "reporting", "external_action",
                            "marketing_spend", "small_investment", "hiring_contractor", "large_investment"],
        hotl_urgency_filter="absolute",
    ),
}

# 어떤 단계에서도 절대 자율 처리 불가
ABSOLUTE_MANUAL = {"exit", "human_ceo", "human_cfo", "hire_human", "system_shutdown"}

# 단계 승급 조건
PROMOTION_CRITERIA = {
    1: {"min_successes": 1, "max_consecutive_failures": 0},
    2: {"min_successes": 3, "max_consecutive_failures": 1},
    3: {"min_successes": 6, "max_consecutive_failures": 1},
    4: {"min_successes": 10, "max_consecutive_failures": 0},
}

# 단계 강등 조건
DEMOTION_CRITERIA = {
    "consecutive_failures": 2,     # 연속 실패 2회
    "budget_crisis_count": 1,      # 예산 위기 1회
}


class GlasswingManager:
    def __init__(self, db_path: str):
        self.db_path = db_path

    @property
    def _conn(self) -> sqlite3.Connection:
        return get_thread_connection(self.db_path)

    # ──────────────────────────────────────────
    # 현재 단계 조회
    # ──────────────────────────────────────────

    def get_stage(self) -> int:
        """현재 자율성 단계 조회. 없으면 1 반환."""
        try:
            row = self._conn.execute(
                "SELECT state_json FROM agent_states WHERE agent_name='glasswing'"
            ).fetchone()
            if not row:
                return 1
            state = json.loads(row["state_json"])
            return state.get("stage", 1)
        except Exception:
            return 1

    def get_policy(self) -> StagePolicy:
        return STAGES[self.get_stage()]

    # ──────────────────────────────────────────
    # 승인 판단
    # ──────────────────────────────────────────

    def can_auto_approve(self, action_type: str, cost: int, budget: int) -> tuple[bool, str]:
        """
        자율 처리 가능 여부 판단.
        Returns: (가능 여부, 이유)
        """
        # 절대 수동 항목
        if action_type in ABSOLUTE_MANUAL:
            return False, f"{action_type}은 항상 위진수 직접 승인 필요"

        policy = self.get_policy()

        # 액션 타입 체크
        if action_type not in policy.auto_approve_types:
            return False, f"Stage {policy.stage}에서 {action_type}은 Board 승인 필요"

        # 비용 체크
        if budget > 0 and cost > 0:
            ratio = cost / budget
            if ratio > policy.auto_approve_cost_ratio:
                return False, (
                    f"비용 {cost:,}원이 자율 한도 초과 "
                    f"({ratio*100:.0f}% > {policy.auto_approve_cost_ratio*100:.0f}%)"
                )

        return True, f"Stage {policy.stage} 자율 처리 승인"

    def should_notify_hotl(self, urgency: str) -> bool:
        """이 urgency를 DM으로 알릴지 여부."""
        policy = self.get_policy()
        order = ["low", "medium", "high", "absolute"]
        filter_idx = order.index(policy.hotl_urgency_filter)
        urgency_idx = order.index(urgency) if urgency in order else 0
        return urgency_idx >= filter_idx

    # ──────────────────────────────────────────
    # 단계 자동 조정
    # ──────────────────────────────────────────

    def record_outcome(self, outcome: str, had_budget_crisis: bool = False):
        """
        회고 결과 기록 → 자동 승급/강등 평가.
        outcome: 'success' | 'failure'
        """
        state = self._load_state()
        current_stage = state.get("stage", 1)

        if outcome == "success":
            state["total_successes"] = state.get("total_successes", 0) + 1
            state["consecutive_failures"] = 0
        else:
            state["total_failures"] = state.get("total_failures", 0) + 1
            state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1

        if had_budget_crisis:
            state["budget_crisis_count"] = state.get("budget_crisis_count", 0) + 1

        # 강등 체크
        new_stage = self._evaluate_demotion(current_stage, state)
        if new_stage < current_stage:
            state["stage"] = new_stage
            logger.warning(f"[Glasswing] Stage 강등: {current_stage} → {new_stage}")
            self._save_state(state)
            return new_stage, "demotion"

        # 승급 체크
        new_stage = self._evaluate_promotion(current_stage, state)
        if new_stage > current_stage:
            state["stage"] = new_stage
            logger.info(f"[Glasswing] Stage 승급: {current_stage} → {new_stage}")
            self._save_state(state)
            return new_stage, "promotion"

        self._save_state(state)
        return current_stage, "unchanged"

    def set_stage(self, stage: int, reason: str = "manual"):
        """위진수가 수동으로 단계 설정."""
        stage = max(0, min(4, stage))
        state = self._load_state()
        old_stage = state.get("stage", 1)
        state["stage"] = stage
        state["last_manual_override"] = reason
        self._save_state(state)
        logger.info(f"[Glasswing] 수동 설정: Stage {old_stage} → {stage} ({reason})")
        return stage

    def _evaluate_promotion(self, current_stage: int, state: dict) -> int:
        if current_stage >= 4:
            return current_stage

        next_stage = current_stage + 1
        criteria = PROMOTION_CRITERIA.get(next_stage, {})

        if (state.get("total_successes", 0) >= criteria.get("min_successes", 999) and
                state.get("consecutive_failures", 0) <= criteria.get("max_consecutive_failures", 0)):
            return next_stage
        return current_stage

    def _evaluate_demotion(self, current_stage: int, state: dict) -> int:
        if current_stage <= 0:
            return current_stage

        if (state.get("consecutive_failures", 0) >= DEMOTION_CRITERIA["consecutive_failures"] or
                state.get("budget_crisis_count", 0) >= DEMOTION_CRITERIA["budget_crisis_count"]):
            return max(0, current_stage - 1)
        return current_stage

    def _load_state(self) -> dict:
        try:
            row = self._conn.execute(
                "SELECT state_json FROM agent_states WHERE agent_name='glasswing'"
            ).fetchone()
            if row:
                return json.loads(row["state_json"])
        except Exception:
            pass
        return {"stage": 1, "total_successes": 0, "total_failures": 0,
                "consecutive_failures": 0, "budget_crisis_count": 0}

    def _save_state(self, state: dict):
        try:
            self._conn.execute(
                """
                INSERT INTO agent_states (agent_name, status, state_json, updated_at)
                VALUES ('glasswing', 'running', ?, datetime('now'))
                ON CONFLICT(agent_name) DO UPDATE SET
                    state_json=excluded.state_json,
                    updated_at=excluded.updated_at
                """,
                (json.dumps(state, ensure_ascii=False),)
            )
            self._conn.commit()
        except Exception as e:
            logger.error(f"[Glasswing] 상태 저장 실패: {e}")

    # ──────────────────────────────────────────
    # 상태 요약
    # ──────────────────────────────────────────

    def summary(self) -> dict:
        state = self._load_state()
        policy = self.get_policy()
        return {
            "stage": policy.stage,
            "name": policy.name,
            "description": policy.description,
            "auto_approve_limit": f"{policy.auto_approve_cost_ratio*100:.0f}%",
            "hotl_filter": policy.hotl_urgency_filter,
            "total_successes": state.get("total_successes", 0),
            "total_failures": state.get("total_failures", 0),
            "consecutive_failures": state.get("consecutive_failures", 0),
            "next_promotion_at": PROMOTION_CRITERIA.get(policy.stage + 1, {}).get("min_successes", "max"),
        }
