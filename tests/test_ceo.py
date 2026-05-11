import sys, time, yaml
sys.path.insert(0, "..")
from core.db import get_thread_connection, init_schema
from core.message_bus import MessageBus, Message, MsgType
from core.llm import LLMClient
from agents.board import BoardAgent
from agents.ceo import CEOAgent

DB_PATH = "../test_acs.db"

def main():
    print("=== CEOAgent Test ===\n")
    with open("../config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    conn = get_thread_connection(DB_PATH)
    init_schema(conn)
    llm = LLMClient(config["llm"])
    bus = MessageBus(conn)

    board = BoardAgent(name="board", db_path=DB_PATH, llm=llm, poll_interval=2.0)
    ceo   = CEOAgent(name="ceo_001", db_path=DB_PATH, llm=llm, poll_interval=2.0)
    board.start(); ceo.start()
    print(f"Board + CEO({ceo.personality_name}) started\n")

    print("1) Board -> CEO: start company")
    bus.send(Message(from_agent="board", to_agent="ceo_001", msg_type=MsgType.TASK,
                     payload={"task": "start_company", "company_id": "company_001", "budget": 500000,
                              "available_sectors": ["프롬프트 판매", "AI 콘텐츠 생성", "자동화 스크립트", "정보 중개"]}))
    print("   processing... (30s)\n")
    time.sleep(30)

    print("2) Results")
    ceo_state = bus.get_agent_state("ceo_001")
    print(f"   CEO state: sector={ceo_state.get('sector')} stage={ceo_state.get('stage')}")
    alerts = conn.execute("SELECT * FROM hotl_alerts").fetchall()
    if alerts:
        print(f"   HOTL alerts {len(alerts)}:")
        for a in alerts:
            print(f"   [{a['urgency'].upper()}] {a['title']}")

    board.stop(); ceo.stop()
    print("\n✅ CEOAgent test complete")

if __name__ == "__main__":
    main()
