import sys, time, yaml
sys.path.insert(0, "..")
from core.db import get_thread_connection, init_schema
from core.message_bus import MessageBus, Message, MsgType
from core.llm import LLMClient
from agents.board import BoardAgent

DB_PATH = "../test_acs.db"

def main():
    print("=== BoardAgent Test ===\n")
    with open("../config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    conn = get_thread_connection(DB_PATH)
    init_schema(conn)
    llm = LLMClient(config["llm"])
    bus = MessageBus(conn)

    board = BoardAgent(name="board", db_path=DB_PATH, llm=llm, poll_interval=1.0)
    board.start()
    print("Board started\n")

    print("1) Normal approval request")
    bus.send(Message(from_agent="ceo_001", to_agent="board", msg_type=MsgType.APPROVAL_REQ,
                     payload={"type": "marketing_spend", "amount": 50000, "description": "SNS ads"}))
    time.sleep(15)
    responses = bus.receive("ceo_001")
    print(f"   Board response: {responses[0].payload if responses else 'no response'}")

    print("\n2) Absolute approval (Exit)")
    bus.send(Message(from_agent="ceo_002", to_agent="board", msg_type=MsgType.APPROVAL_REQ,
                     payload={"type": "exit", "company": "company_001", "valuation": 5000000}))
    time.sleep(5)
    alerts = conn.execute("SELECT * FROM hotl_alerts ORDER BY id DESC LIMIT 3").fetchall()
    print(f"   HOTL alerts {len(alerts)}:")
    for a in alerts:
        print(f"   [{a['urgency'].upper()}] {a['title']}")

    board.stop()
    print("\n✅ BoardAgent test complete")

if __name__ == "__main__":
    main()
