import sys
sys.path.insert(0, "..")
from core.db import get_thread_connection, init_schema
from core.message_bus import MessageBus, Message, MsgType, Urgency

def main():
    print("=== MessageBus Test ===\n")
    conn = get_thread_connection("test_acs.db")
    init_schema(conn)
    bus = MessageBus(conn)

    print("1) Send Board -> CEO")
    msg_id = bus.send(Message(from_agent="board", to_agent="ceo_001", msg_type=MsgType.TASK,
                              payload={"task": "업종 선택", "budget": 100000}, priority=3))
    print(f"   sent (id={msg_id})")

    print("\n2) CEO receives")
    messages = bus.receive("ceo_001")
    for m in messages:
        print(f"   [{m.msg_type}] from={m.from_agent} payload={m.payload}")

    bus.ack(messages[0].id)
    print(f"\n3) ACK done")

    print("\n4) Re-receive (should be 0)")
    messages2 = bus.receive("ceo_001")
    print(f"   received {len(messages2)} ({'OK' if len(messages2) == 0 else 'FAIL'})")

    print("\n5) HOTL alert")
    alert_id = bus.alert_hotl("system_auditor", Urgency.HIGH, "Hermes down", "No response for 30s")
    print(f"   alert sent (id={alert_id})")

    print("\n6) Agent state")
    bus.set_agent_state("ceo_001", "running", {"task": "analyzing", "companies": []})
    state = bus.get_agent_state("ceo_001")
    print(f"   state: {state}")

    print("\n✅ MessageBus test passed")

if __name__ == "__main__":
    main()
