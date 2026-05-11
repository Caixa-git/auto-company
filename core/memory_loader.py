"""
MemoryLoader - L2/L3 기억 압축 로더.
DB에서 회고와 sector_db를 읽어서 압축된 형태로 반환.
system_prompt에 주입용.
"""

import json
import logging
import sqlite3
from typing import Optional

from core.db import get_thread_connection

logger = logging.getLogger(__name__)

# 압축 목표 토큰 (대략 문자 수 / 3)
MAX_L2_CHARS = 500   # 회고 압축 목표
MAX_L3_CHARS = 300   # sector_db 압축 목표


class MemoryLoader:
    def __init__(self, db_path: str):
        self.db_path = db_path

    @property
    def _conn(self) -> sqlite3.Connection:
        return get_thread_connection(self.db_path)

    # ──────────────────────────────────────────
    # L2: Episodic Memory (과거 회고)
    # ──────────────────────────────────────────

    def load_l2(self, agent_name: str, max_entries: int = 5) -> str:
        """
        에이전트의 과거 회고를 압축해서 반환.
        없으면 빈 문자열.
        """
        try:
            rows = self._conn.execute(
                """
                SELECT detail FROM event_log
                WHERE agent_name = ? AND event_type = 'retrospective'
                ORDER BY id DESC LIMIT ?
                """,
                (agent_name, max_entries)
            ).fetchall()

            if not rows:
                return ""

            entries = []
            for row in rows:
                try:
                    detail = json.loads(row["detail"])
                    retro = detail.get("retrospective", {})
                    outcome = retro.get("outcome", "unknown")
                    sector = detail.get("sector", "?")
                    roi = retro.get("roi", 0)
                    lessons = retro.get("lessons", [])
                    lesson_str = " / ".join(lessons[:2])  # 최대 2개 교훈만
                    entries.append(f"[{outcome}] {sector} ROI {roi}% - {lesson_str}")
                except Exception:
                    continue

            if not entries:
                return ""

            result = "과거 창업 경험:\n" + "\n".join(entries)

            # 길이 초과 시 뒤에서 자르기
            if len(result) > MAX_L2_CHARS:
                result = result[:MAX_L2_CHARS] + "..."

            return result

        except Exception as e:
            logger.error(f"[Memory] L2 로드 실패 ({agent_name}): {e}")
            return ""

    # ──────────────────────────────────────────
    # L3: Semantic Memory (업종 학습 데이터)
    # ──────────────────────────────────────────

    def load_l3(self, max_sectors: int = 5) -> str:
        """
        System CFO의 sector_db를 압축해서 반환.
        성공률 높은 순으로 정렬.
        """
        try:
            row = self._conn.execute(
                "SELECT state_json FROM agent_states WHERE agent_name = 'system_cfo'"
            ).fetchone()

            if not row:
                return ""

            state = json.loads(row["state_json"])
            sector_db = state.get("sector_db", {})

            if not sector_db:
                return ""

            # 성공률 높은 순 정렬
            sorted_sectors = sorted(
                sector_db.items(),
                key=lambda x: x[1].get("success_rate", 0),
                reverse=True,
            )[:max_sectors]

            lines = []
            for sector, data in sorted_sectors:
                sr = int(data.get("success_rate", 0) * 100)
                roi = data.get("avg_roi", 0)
                total = data.get("total", 0)
                lines.append(f"{sector}: 성공률 {sr}% avg ROI {roi:.0f}% ({total}건)")

            result = "업종 학습 데이터:\n" + "\n".join(lines)

            if len(result) > MAX_L3_CHARS:
                result = result[:MAX_L3_CHARS] + "..."

            return result

        except Exception as e:
            logger.error(f"[Memory] L3 로드 실패: {e}")
            return ""

    # ──────────────────────────────────────────
    # 통합 로드 (system_prompt 주입용)
    # ──────────────────────────────────────────

    def load_for_ceo(self, agent_name: str) -> str:
        """
        CEO system_prompt에 주입할 기억 블록 반환.
        L2 + L3 합쳐서 최대 ~800자.
        """
        l2 = self.load_l2(agent_name)
        l3 = self.load_l3()

        parts = []
        if l2:
            parts.append(l2)
        if l3:
            parts.append(l3)

        if not parts:
            return ""

        return "\n\n".join(parts)

    def load_for_board(self) -> str:
        """Board system_prompt에 주입할 포트폴리오 현황."""
        try:
            row = self._conn.execute(
                "SELECT state_json FROM agent_states WHERE agent_name = 'system_cfo'"
            ).fetchone()

            if not row:
                return ""

            state = json.loads(row["state_json"])
            lines = [
                f"총 자본: {state.get('total_capital', 0):,}원",
                f"운용 가능: {state.get('deployable', 0):,}원",
                f"활성 회사: {state.get('active_companies', 0)}개",
                f"Exit: {state.get('exit_count', 0)}건 / 실패: {state.get('failure_count', 0)}건",
            ]

            l3 = self.load_l3(max_sectors=3)
            if l3:
                lines.append("")
                lines.append(l3)

            return "포트폴리오 현황:\n" + "\n".join(lines)

        except Exception as e:
            logger.error(f"[Memory] Board 컨텍스트 로드 실패: {e}")
            return ""

    def load_for_system_cfo(self) -> str:
        """System CFO system_prompt에 주입할 포트폴리오 요약."""
        return self.load_l3(max_sectors=5)
