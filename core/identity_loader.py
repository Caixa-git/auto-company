"""
IdentityLoader.
agents/identities/*.json 에서 표준화된 Identity를 로드.
system_prompt 상단에 주입용.
"""

import json
import logging
from pathlib import Path
from functools import lru_cache

logger = logging.getLogger(__name__)

IDENTITIES_DIR = Path(__file__).parent.parent / "agents" / "identities"


class IdentityLoader:
    def __init__(self, identities_dir: Path = None):
        self.dir = identities_dir or IDENTITIES_DIR

    @lru_cache(maxsize=20)
    def load(self, persona_key: str) -> dict:
        """Identity JSON 로드. 없으면 빈 dict."""
        path = self.dir / f"{persona_key}.json"
        if not path.exists():
            logger.warning(f"[Identity] 파일 없음: {path} (extract_identities.py 먼저 실행)")
            return {}
        try:
            identity = json.loads(path.read_text(encoding="utf-8"))
            logger.debug(f"[Identity] loaded: {persona_key}")
            return identity
        except Exception as e:
            logger.error(f"[Identity] 로드 실패 ({persona_key}): {e}")
            return {}

    def to_prompt(self, persona_key: str) -> str:
        """
        Identity → system_prompt 상단 주입용 문자열.
        없으면 빈 문자열 (graceful fallback).
        """
        identity = self.load(persona_key)
        if not identity:
            return ""

        criteria = "\n".join(f"  - {c}" for c in identity.get("decision_criteria", []))
        strengths = "\n".join(f"  - {s}" for s in identity.get("strengths", []))
        blind_spots = "\n".join(f"  - {b}" for b in identity.get("blind_spots", []))

        return f"""## Identity: {identity.get('name', persona_key)}

Core Value     : {identity.get('core_value', '')}
Thinking Style : {identity.get('thinking_style', '')}
Risk Stance    : {identity.get('risk_stance', '')}
Communication  : {identity.get('communication_style', '')}

Decision Criteria:
{criteria}

Strengths:
{strengths}

Blind Spots (be aware):
{blind_spots}
"""

    def available(self) -> list[str]:
        """로드 가능한 identity 목록."""
        if not self.dir.exists():
            return []
        return [p.stem for p in self.dir.glob("*.json")]
