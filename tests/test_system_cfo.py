"""
SystemCFOAgent test.
Run: python test_system_cfo.py
"""

import sys
import time
import json
import yaml
sys.path.insert(0, "..")

from core.db import get_connection, get_thread_connection, init_schema
from core.message_bus import MessageBus, Message, MsgType
from core.llm import LLMClient
from agents.board import BoardAgent
from agents.system_cfo import SystemCFOAgent
from agents.company_cfo import CompanyCFOAgent

DB_PATH = "../test_acs.db"

def main():
    print("=== SystemCFOAgent Test ===\n")

    with open("../config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # Init schema on main-thread connection
    conn = get_thread_connection(DB_PATH)
    init_schema(conn)
    llm = LLMClient(config["llm"])
    bus = MessageBus(conn)

    board      = BoardAgent(name="board",      db_path=DB_PATH, llm=llm, poll_interval=2.0)
    system_cfo = SystemCFOAgent(name="system_cfo", db_path=DB_PATH, llm=llm, initial_capital=5_000_000, poll_interval=2.0)
    cfo_001    = CompanyCFOAgent(name="cfo_ceo_001", db_path=DB_PATH, llm=llm, ceo_name="ceo_001", poll_interval=2.0)

    board.start()
    system_cfo.start()
    cfo_001.start()
    print("Board + SystemCFO + CompanyCFO started")
    print("Initial capital: 5,000,000 KRW\n")

    # 1. Company creation report
    print("1) Company CFO -> System CFO: company creation report")
    bus.send(Message(
        from_agent="cfo_ceo_001", to_agent="system_cfo", msg_type=MsgType.REPORT,
        payload={"summary": "새 회사 창업 — 초기 예산 배정 완료", "company_id": "company_001",
                 "ceo": "ceo_001", "initial_budget": 1_000_000, "sector": "자동화 스크립트"},
    ))
    time.sleep(4)
    state = bus.get_agent_state("system_cfo")
    print(f"   active companies: {state.get('active_companies')} / invested: {state.get('total_invested', 0):,}\n")

    # 2. Revenue report
    print("2) Revenue report (300,000 KRW)")
    bus.send(Message(
        from_agent="cfo_ceo_001", to_agent="system_cfo", msg_type=MsgType.REPORT,
        payload={"summary": "수익 발생", "company_id": "company_001",
                 "revenue": 300_000, "total_revenue": 300_000, "current_budget": 1_100_000},
    ))
    time.sleep(4)
    state = bus.get_agent_state("system_cfo")
    print(f"   total returned: {state.get('total_returned', 0):,}\n")

    # 3. Budget allocation
    print("3) Board -> System CFO: budget allocation request")
    bus.send(Message(
        from_agent="board", to_agent="system_cfo", msg_type=MsgType.TASK,
        payload={"task": "allocate_budget", "company_id": "company_002", "requested_budget": 800_000},
    ))
    time.sleep(4)
    state = bus.get_agent_state("system_cfo")
    print(f"   remaining deployable: {state.get('deployable', 0):,}\n")

    # 4. Retrospective -> Meta-Learning
    print("4) Retrospective -> Meta-Learning")
    bus.send(Message(
        from_agent="cfo_ceo_001", to_agent="system_cfo", msg_type=MsgType.REPORT,
        payload={"summary": "재무 회고 완료: ROI 30.0%", "company_id": "company_001",
                 "retrospective": {"outcome": "success", "roi": 30.0,
                                   "lessons": ["Tier 0 works without capital", "Fast MVP is effective"]}},
    ))
    time.sleep(4)
    state = bus.get_agent_state("system_cfo")
    print(f"   exit count: {state.get('exit_count')} / sector_db: {state.get('sector_db')}\n")

    # 5. Portfolio review
    print("5) Portfolio review (20s wait)")
    bus.send(Message(
        from_agent="board", to_agent="system_cfo", msg_type=MsgType.TASK,
        payload={"task": "portfolio_review"},
    ))
    time.sleep(20)

    board_msgs = conn.execute(
        "SELECT payload FROM messages WHERE to_agent='board' AND msg_type='report' ORDER BY id DESC LIMIT 3"
    ).fetchall()
    print(f"   Reports sent to Board: {len(board_msgs)}")
    for m in board_msgs:
        p = json.loads(m["payload"])
        print(f"   -> {p.get('summary', '')}")

    board.stop(); system_cfo.stop(); cfo_001.stop()
    print("\n✅ SystemCFOAgent test complete")

if __name__ == "__main__":
    main()
