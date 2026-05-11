import sys, time, json, yaml
sys.path.insert(0, "..")
from core.db import get_thread_connection, init_schema
from core.message_bus import MessageBus, Message, MsgType
from core.llm import LLMClient
from agents.board import BoardAgent
from agents.ceo import CEOAgent
from agents.company_cfo import CompanyCFOAgent

DB_PATH = "../test_acs.db"

def main():
    print("=== CompanyCFOAgent Test ===\n")
    with open("../config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    conn = get_thread_connection(DB_PATH)
    init_schema(conn)
    llm = LLMClient(config["llm"])
    bus = MessageBus(conn)

    board = BoardAgent(name="board",       db_path=DB_PATH, llm=llm, poll_interval=2.0)
    ceo   = CEOAgent(name="ceo_001",       db_path=DB_PATH, llm=llm, poll_interval=2.0)
    cfo   = CompanyCFOAgent(name="cfo_ceo_001", db_path=DB_PATH, llm=llm, ceo_name="ceo_001", poll_interval=2.0)
    board.start(); ceo.start(); cfo.start()
    print(f"Board + CEO({ceo.personality_name}) + CFO started\n")

    print("1) CEO -> CFO: company start")
    bus.send(Message(from_agent="ceo_001", to_agent="cfo_ceo_001", msg_type=MsgType.REPORT,
                     payload={"summary": "창업 시작 — 초기 예산 배정", "company_id": "company_001",
                              "budget": 500000, "sector": "자동화 스크립트"}))
    time.sleep(5)
    state = bus.get_agent_state("cfo_ceo_001")
    print(f"   budget: {state.get('current_budget', '?'):,}\n")

    print("2) Expense 100,000")
    bus.send(Message(from_agent="ceo_001", to_agent="cfo_ceo_001", msg_type=MsgType.REPORT,
                     payload={"summary": "지출", "amount": 100000, "description": "SNS ads"}))
    time.sleep(3)
    state = bus.get_agent_state("cfo_ceo_001")
    print(f"   balance: {state.get('current_budget', '?'):,}\n")

    print("3) Revenue 300,000")
    bus.send(Message(from_agent="ceo_001", to_agent="cfo_ceo_001", msg_type=MsgType.REPORT,
                     payload={"summary": "수익", "amount": 300000, "description": "script sales"}))
    time.sleep(3)
    state = bus.get_agent_state("cfo_ceo_001")
    print(f"   balance: {state.get('current_budget', '?'):,} / revenue: {state.get('total_revenue', '?'):,}\n")

    print("4) Crisis test (large expense)")
    bus.send(Message(from_agent="ceo_001", to_agent="cfo_ceo_001", msg_type=MsgType.REPORT,
                     payload={"summary": "지출", "amount": 740000, "description": "server + outsourcing"}))
    time.sleep(5)
    alerts = conn.execute("SELECT * FROM hotl_alerts ORDER BY id DESC LIMIT 3").fetchall()
    print(f"   HOTL alerts {len(alerts)}:")
    for a in alerts:
        print(f"   [{a['urgency'].upper()}] {a['title']}")

    print("\n5) Financial report (15s)")
    bus.send(Message(from_agent="board", to_agent="cfo_ceo_001", msg_type=MsgType.TASK,
                     payload={"task": "financial_report"}))
    time.sleep(15)
    msgs = conn.execute("SELECT payload FROM messages WHERE to_agent='system_cfo' ORDER BY id DESC LIMIT 3").fetchall()
    print(f"   Reports to system_cfo: {len(msgs)}")
    for m in msgs:
        p = json.loads(m["payload"])
        print(f"   -> {p.get('summary', '')}")

    board.stop(); ceo.stop(); cfo.stop()
    print("\n✅ CompanyCFOAgent test complete")

if __name__ == "__main__":
    main()
