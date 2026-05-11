"""
SystemAuditor 테스트.
에이전트를 강제로 멈춰서 무응답 감지 확인.
Run: python test_auditor.py
"""

import sys
import time
import yaml
sys.path.insert(0, "..")

from core.db import get_thread_connection, init_schema
from core.message_bus import MessageBus
from core.llm import LLMClient
from agents.board import BoardAgent
from agents.system_auditor import SystemAuditor

DB_PATH = "../test_auditor.db"

def main():
    print("=== SystemAuditor 테스트 ===\n")

    with open("../config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    conn = get_thread_connection(DB_PATH)
    init_schema(conn)
    llm = LLMClient(config["llm"])
    bus = MessageBus(conn)

    # Auditor 먼저 시작
    auditor = SystemAuditor(db_path=DB_PATH, poll_interval=5.0)
    auditor.start()
    print("Auditor 시작됨\n")

    # Board 시작
    board = BoardAgent(name="board", db_path=DB_PATH, llm=llm, poll_interval=2.0)
    board.start()
    print("Board 시작됨")
    time.sleep(3)

    # 1. 정상 상태 확인
    print("\n1) 정상 상태 확인")
    summary = auditor.summary()
    for name, info in summary.items():
        emoji = "🟢" if info["healthy"] else "🔴"
        print(f"   {emoji} {name}: {info['status']} ({info['elapsed_sec']}s)")

    # 2. Board DB 상태를 running으로 놔두고 스레드만 kill → 무응답 유발
    print("\n2) Board 스레드 강제 종료 (running 상태 유지, 무응답 유발)")
    board._running = False  # 루프 중단 but DB status는 'running' 유지
    print("   Board 루프 중단됨. 35초 대기 (임계값 30초)...")
    time.sleep(35)

    # 3. 장애 감지 확인
    print("\n3) 장애 감지 확인")
    summary = auditor.summary()
    for name, info in summary.items():
        emoji = "🟢" if info["healthy"] else "🔴"
        print(f"   {emoji} {name}: {info['status']} ({info['elapsed_sec']}s)")

    alerts = conn.execute(
        "SELECT urgency, title, status FROM hotl_alerts ORDER BY id DESC LIMIT 5"
    ).fetchall()
    print(f"\n   HOTL 알림 {len(alerts)}건:")
    for a in alerts:
        print(f"   [{a['urgency'].upper()}] {a['title']} → {a['status']}")

    auditor.stop()
    # Windows에서는 DB 연결이 열려있어 바로 삭제 불가 — 수동으로 삭제하세요
    # import os; os.remove(DB_PATH)
    print("\n✅ SystemAuditor 테스트 완료")

if __name__ == "__main__":
    main()
