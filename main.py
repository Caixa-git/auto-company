"""
Auto-Company-System (ACS) - Entry Point
Run: python main.py
"""

import sys
import time
import signal
import logging
import yaml

from core.db import get_thread_connection, init_schema
from core.message_bus import MessageBus
from core.llm import LLMClient
from agents.board import BoardAgent
from agents.ceo import CEOAgent
from agents.company_cfo import CompanyCFOAgent
from agents.system_cfo import SystemCFOAgent
from agents.system_auditor import SystemAuditor
from core.persona_loader import PersonaLoader
from core.memory_loader import MemoryLoader
from core.glasswing import GlasswingManager
from core.email_action import EmailAction

# ──────────────────────────────────────────
# Logging
# ──────────────────────────────────────────

import io
import os
os.makedirs("logs", exist_ok=True)

_handlers = [logging.FileHandler("logs/acs.log", encoding="utf-8")]
try:
    _handlers.insert(0, logging.StreamHandler(io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")))
except AttributeError:
    pass  # pythonw has no stdout

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=_handlers,
)
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────
# ACS Runtime
# ──────────────────────────────────────────

class ACS:
    def __init__(self, config: dict):
        self.config = config
        self.db_path = config["system"]["db_path"]
        self.poll_interval = config["system"]["poll_interval_seconds"]

        self.conn = get_thread_connection(self.db_path)
        init_schema(self.conn)
        self.bus = MessageBus(self.conn)
        self.llm = LLMClient(config["llm"])

        self.agents: list = []
        self.auditor: SystemAuditor = None

        # PersonaLoader 초기화
        agency_path = config.get("personas", {}).get("agency_agents_path", "")
        self.persona_loader = PersonaLoader(agency_path) if agency_path else None
        if self.persona_loader:
            available = self.persona_loader.available()
            logger.info(f"Personas loaded: {available}")

        self.memory_loader = MemoryLoader(self.db_path)
        self.glasswing = GlasswingManager(self.db_path)
        self.email_action = EmailAction(config)
        self._running = False

    def bootstrap(self):
        """Initialize all agents and start the system."""
        # System Auditor 가장 먼저 시작 (독립 실행)
        self.auditor = SystemAuditor(db_path=self.db_path)
        self.auditor.start()
        logger.info("  OK system_auditor started (independent)")

        logger.info("=" * 50)
        logger.info("  Auto-Company-System Bootstrapping...")
        logger.info("=" * 50)

        initial_capital = self.config["system"].get("initial_capital", 1_000_000)

        # 1. System CFO (재무 두뇌)
        system_cfo = SystemCFOAgent(
            name="system_cfo",
            db_path=self.db_path,
            llm=self.llm,
            initial_capital=initial_capital,
            persona_loader=self.persona_loader,
            poll_interval=self.poll_interval,
        )
        system_cfo._memory_loader = self.memory_loader

        # 2. Board (의사결정)
        board = BoardAgent(
            name="board",
            db_path=self.db_path,
            llm=self.llm,
            persona_loader=self.persona_loader,
            poll_interval=self.poll_interval,
        )
        board._memory_loader = self.memory_loader
        board._glasswing = self.glasswing

        self.agents = [system_cfo, board]

        # Start all agents
        for agent in self.agents:
            agent.start()
            logger.info(f"  OK {agent.name} started")

        logger.info("=" * 50)
        logger.info(f"  Initial capital : {initial_capital:,} KRW")
        logger.info(f"  DB              : {self.db_path}")
        logger.info(f"  Poll interval   : {self.poll_interval}s")
        logger.info("=" * 50)
        logger.info("ACS is running. Press Ctrl+C to stop.")

        self._running = True

    def spawn_company(self, company_id: str, budget: int, available_sectors: list[str]):
        """Spawn a new CEO + CFO pair for a company."""
        ceo_name = f"ceo_{company_id}"
        cfo_name = f"cfo_{company_id}"

        ceo = CEOAgent(
            name=ceo_name,
            db_path=self.db_path,
            llm=self.llm,
            cfo_name=cfo_name,
            persona_loader=self.persona_loader,
            poll_interval=self.poll_interval,
        )
        ceo._memory_loader = self.memory_loader
        ceo._email_action = self.email_action
        ceo._glasswing = self.glasswing
        cfo = CompanyCFOAgent(
            name=cfo_name,
            db_path=self.db_path,
            llm=self.llm,
            ceo_name=ceo_name,
            poll_interval=self.poll_interval,
        )

        ceo.start()
        cfo.start()
        self.agents += [ceo, cfo]

        # Board -> CEO: start company
        from core.message_bus import Message, MsgType
        self.bus.send(Message(
            from_agent="board",
            to_agent=ceo_name,
            msg_type=MsgType.TASK,
            payload={
                "task": "start_company",
                "company_id": company_id,
                "budget": budget,
                "available_sectors": available_sectors,
            },
        ))

        logger.info(f"Company spawned: {company_id} (CEO={ceo_name}, budget={budget:,})")
        logger.info(f"CEO personality: {ceo.personality_name}")
        return ceo, cfo

    def status(self) -> dict:
        """Return current system status."""
        result = {}
        for agent in self.agents:
            state = self.bus.get_agent_state(agent.name) or {}
            result[agent.name] = {
                "alive": agent.is_alive(),
                "status": state.get("status", "unknown"),
            }
        pending_alerts = self.conn.execute(
            "SELECT COUNT(*) FROM hotl_alerts WHERE status='pending'"
        ).fetchone()[0]
        result["_pending_alerts"] = pending_alerts
        return result

    def shutdown(self):
        """Graceful shutdown."""
        logger.info("Shutting down ACS...")
        for agent in reversed(self.agents):
            agent.stop()
            logger.info(f"  OK {agent.name} stopped")
        self._running = False
        if self.auditor:
            self.auditor.stop()
            logger.info("  OK system_auditor stopped")
        logger.info("ACS shutdown complete.")

    def run(self):
        """Main loop - keeps the process alive and prints status."""
        self.bootstrap()

        # Graceful shutdown on Ctrl+C
        def _handler(sig, frame):
            print()
            self.shutdown()
            sys.exit(0)
        signal.signal(signal.SIGINT, _handler)

        # Spawn first company (Tier 0)
        tier0_sectors = self.config["system"].get("tier0_sectors", [
            "프롬프트 판매",
            "AI 콘텐츠 생성",
            "자동화 스크립트",
            "정보 중개",
        ])
        initial_capital = self.config["system"].get("initial_capital", 1_000_000)
        first_budget = int(initial_capital * 0.2)  # 20% of capital

        self.spawn_company(
            company_id="company_001",
            budget=first_budget,
            available_sectors=tier0_sectors,
        )

        # Status loop
        tick = 0
        while self._running:
            time.sleep(30)
            tick += 1
            if tick % 2 == 0:  # every 60s
                status = self.status()
                alive = sum(1 for v in status.values() if isinstance(v, dict) and v.get("alive"))
                alerts = status.get("_pending_alerts", 0)
                logger.info(f"[STATUS] agents alive={alive} | pending_alerts={alerts}")
                if alerts > 0:
                    logger.warning(f"  ⚠ {alerts} HOTL alert(s) pending - check Discord bot")


# ──────────────────────────────────────────
# Entry
# ──────────────────────────────────────────

def main():
    with open("config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    acs = ACS(config)
    acs.run()


if __name__ == "__main__":
    main()
