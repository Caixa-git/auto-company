"""
승인 플로우 테스트.
DB에 직접 ABSOLUTE 알림을 꽂아서 Discord DM 버튼 확인.
Run: python test_approval_flow.py (discord_bot.py 실행 중인 상태에서)
"""

import sys
import time
import json
import yaml
sys.path.insert(0, "..")

from core.db import get_thread_connection, init_schema
from core.message_bus import MessageBus, Urgency

DB_PATH = "acs.db"

def main():
    print("=== 승인 플로우 테스트 ===\n")

    conn = get_thread_connection(DB_PATH)
    bus = MessageBus(conn)

    # 1. 일반 알림 (LOW)
    print("1) LOW 알림 전송")
    bus.alert_hotl(
        from_agent="test",
        urgency=Urgency.LOW,
        title="테스트 알림 (LOW)",
        body="이건 일반 알림이에요. 버튼 없음.",
    )
    time.sleep(2)

    # 2. 높은 위급도 알림 (HIGH)
    print("2) HIGH 알림 전송")
    bus.alert_hotl(
        from_agent="system_auditor",
        urgency=Urgency.HIGH,
        title="에이전트 장애 감지",
        body="board 에이전트가 30초간 응답 없음. 확인 필요.",
    )
    time.sleep(2)

    # 3. 절대 승인 — Exit 요청 (버튼 UI)
    print("3) ABSOLUTE 승인 요청 전송 (Exit)")
    bus.alert_hotl(
        from_agent="ceo_company_001",
        urgency=Urgency.ABSOLUTE,
        title="[절대 승인 필요] exit",
        body=json.dumps({
            "type": "exit",
            "company_id": "company_001",
            "sector": "AI 콘텐츠 생성",
            "valuation": 800000,
            "reason": "3개월 목표 달성, 매각 적정 시점 판단",
        }, ensure_ascii=False, indent=2),
    )
    time.sleep(2)

    # 4. 절대 승인 — Human CEO 전환 요청
    print("4) ABSOLUTE 승인 요청 전송 (Human CEO 전환)")
    bus.alert_hotl(
        from_agent="board",
        urgency=Urgency.ABSOLUTE,
        title="[절대 승인 필요] human_ceo",
        body=json.dumps({
            "type": "human_ceo",
            "company_id": "company_001",
            "reason": "성장 단계 진입, Human CEO 전환 권고",
        }, ensure_ascii=False, indent=2),
    )

    print("\nDiscord DM 확인해봐요!")
    print("- LOW/HIGH → 버튼 없는 알림")
    print("- ABSOLUTE → ✅ 승인 / ❌ 거절 버튼")
    print("\n승인 버튼 누른 후 아래 명령으로 Board 수신 확인:")
    print("  python test_approval_flow.py --check")

def check():
    """Board가 승인 응답을 수신했는지 확인."""
    print("=== Board 수신 확인 ===\n")
    conn = get_thread_connection(DB_PATH)

    # Board로 온 approval_res 메시지 확인
    msgs = conn.execute(
        "SELECT * FROM messages WHERE to_agent='board' AND msg_type='approval_res' ORDER BY id DESC LIMIT 5"
    ).fetchall()

    if msgs:
        print(f"Board 수신된 승인 응답 {len(msgs)}건:")
        for m in msgs:
            p = json.loads(m["payload"])
            status = "✅ 승인" if p.get("approved") else "❌ 거절"
            print(f"  [{status}] type={p.get('type')} reason={p.get('reason')}")
    else:
        print("아직 Board에 응답 없음 (버튼 아직 안 눌렀거나 전달 중)")

    # 알림 상태 확인
    alerts = conn.execute(
        "SELECT id, urgency, title, status FROM hotl_alerts ORDER BY id DESC LIMIT 5"
    ).fetchall()
    print(f"\n최근 알림 {len(alerts)}건:")
    for a in alerts:
        print(f"  [id={a['id']}] [{a['urgency'].upper()}] {a['title'][:40]} → {a['status']}")

if __name__ == "__main__":
    if "--check" in sys.argv:
        check()
    else:
        main()
