"""
Persona loader.
Reads .md files from agency-agents repo and injects into agent system prompts.
"""

import os
import logging
from pathlib import Path
from functools import lru_cache

logger = logging.getLogger(__name__)

# agency-agents 파일 → ACS 에이전트 매핑
PERSONA_MAP = {
    "ceo_hacker":    "engineering/engineering-rapid-prototyper.md",
    "ceo_craftsman": "engineering/engineering-senior-developer.md",
    "ceo_analyst":   "product/product-manager.md",
    "board":         "finance/finance-investment-researcher.md",
    "system_cfo":    "finance/finance-fpa-analyst.md",
}


class PersonaLoader:
    def __init__(self, agency_agents_path: str):
        """
        agency_agents_path: agency-agents 레포 루트 경로
        예) C:/Projects/agency-agents
        """
        self.base_path = Path(agency_agents_path)
        if not self.base_path.exists():
            logger.warning(f"[Persona] agency-agents path not found: {agency_agents_path}")

    @lru_cache(maxsize=20)
    def load(self, persona_key: str) -> str:
        """
        페르소나 키로 .md 파일 읽기.
        파일 없으면 빈 문자열 반환 (graceful fallback).
        """
        rel_path = PERSONA_MAP.get(persona_key)
        if not rel_path:
            logger.warning(f"[Persona] unknown key: {persona_key}")
            return ""

        full_path = self.base_path / rel_path
        if not full_path.exists():
            logger.warning(f"[Persona] file not found: {full_path}")
            return ""

        try:
            content = full_path.read_text(encoding="utf-8")
            logger.info(f"[Persona] loaded: {persona_key} ({len(content)} chars)")
            return content
        except Exception as e:
            logger.error(f"[Persona] read error: {e}")
            return ""

    def available(self) -> list[str]:
        """로드 가능한 페르소나 목록."""
        result = []
        for key, rel_path in PERSONA_MAP.items():
            if (self.base_path / rel_path).exists():
                result.append(key)
        return result
