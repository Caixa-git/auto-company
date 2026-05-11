"""
BehaviorLoader.
agents/behaviors/*.md 에서 ACS 강제 행동 규칙 로드.
system_prompt 중간에 주입용.
"""

import logging
from pathlib import Path
from functools import lru_cache

logger = logging.getLogger(__name__)

BEHAVIORS_DIR = Path(__file__).parent.parent / "agents" / "behaviors"


class BehaviorLoader:
    def __init__(self, behaviors_dir: Path = None):
        self.dir = behaviors_dir or BEHAVIORS_DIR

    @lru_cache(maxsize=20)
    def load(self, agent_type: str, include_common: bool = True) -> str:
        """
        행동 규칙 로드.
        common.md + {agent_type}.md 합쳐서 반환.
        """
        parts = []

        if include_common:
            common = self._read("common")
            if common:
                parts.append(common)

        specific = self._read(agent_type)
        if specific:
            parts.append(specific)

        if not parts:
            logger.warning(f"[Behavior] 파일 없음: {agent_type} (behaviors/ 폴더 확인)")
            return ""

        return "\n\n".join(parts)

    def _read(self, name: str) -> str:
        path = self.dir / f"{name}.md"
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8").strip()
        except Exception as e:
            logger.error(f"[Behavior] 읽기 실패 ({name}): {e}")
            return ""

    def available(self) -> list[str]:
        if not self.dir.exists():
            return []
        return [p.stem for p in self.dir.glob("*.md") if p.stem != "common"]
