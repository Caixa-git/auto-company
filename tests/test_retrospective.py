"""
실패/성공 판단 + 회고 루프 테스트.
CFO 상태를 직접 조작해서 판단 트리거 확인.
Run: python test_retrospective.py (tests/ 안에서)
"""

import sys
import time
import json
import yaml
sys.path.insert(0, "..")

from core.db import get_thread_connection, init_schema
from core.message_bus import MessageBus, Message, MsgType
from core.llm import LLMClient
from core.memory_loader import MemoryLoader
from agents.board import BoardAgent
from agents.ceo import CEOAgent, BUDGET_FAILURE_THRESHOLD

DB_PATH = "../test_retro.db"

def main():
    print("=== 실패/성공 판단 + 회고 루프 테스트 ===\n")

    with open("../config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    conn = get_thread_connection(DB_PATH)
    init_schema(conn)
    llm = LLMClient(config["llm"])
    bus = MessageBus(conn)
    ml = MemoryLoader(DB_PATH)

    board = BoardAgent(name="board", db_path=DB_PATH, llm=llm, poll_interval=2.0)
    ceo = CEOAgent(name="ceo_001", db_path=DB_PATH, llm=llm,
                   cfo_name="cfo_001", poll_interval=2.0)
    ceo._memory_loader = ml

    board.start()
    ceo.start()
    print(f"Board + CEO({ceo.personality_name}) 시작\n")

    # 창업 지시
    print("1) 창업 시작")
    bus.send(Message(
        from_agent="board", to_agent="ceo_001", msg_type=MsgType.TASK,
        payload={
            "task": "start_company",
            "company_id": "company_001",
            "budget": 200_000,
            "available_sectors": ["자동화 스크립트", "프롬프트 판매", "AI 콘텐츠 생성"],
        }
    ))
    time.sleep(25)  # 업종 선택 + Board 승인 대기

    print(f"   CEO 단계: {bus.get_agent_state('ceo_001', ).get('stage')}")

    # CFO 상태 직접 조작 (예산 15% 이하로 → 실패 트리거)
    print("\n2) 예산 소진 시뮬레이션 (실패 트리거)")
    remaining = int(200_000 * (BUDGET_FAILURE_THRESHOLD - 0.01))  # 14%
    conn.execute(
        """
        INSERT INTO agent_states (agent_name, status, state_json)
        VALUES ('cfo_001', 'running', ?)
        ON CONFLICT(agent_name) DO UPDATE SET state_json=excluded.state_json
        """,
        (json.dumps({
            "company_id": "company_001",
            "initial_budget": 200_000,
            "current_budget": remaining,
            "total_revenue": 0,
        }, ensure_ascii=False),)
    )
    conn.commit()
    print(f"   CFO 잔액 강제 설정: {remaining:,}원 ({remaining/200_000*100:.0f}%)")

    # CEO가 평가 루프 실행할 때까지 대기
    print("   CEO 판단 대기 중 (30초)...")
    time.sleep(30)

    # 결과 확인
    print("\n3) 결과 확인")
    ceo_state = bus.get_agent_state("ceo_001") or {}
    print(f"   CEO 단계: {ceo_state.get('stage', '?')}")

    retros = conn.execute(
        "SELECT detail FROM event_log WHERE agent_name='ceo_001' AND event_type='retrospective'"
    ).fetchall()
    print(f"   회고 기록: {len(retros)}건")
    for r in retros:
        d = json.loads(r["detail"])
        retro = d.get("retrospective", {})
        print(f"   outcome={retro.get('outcome')} lessons={retro.get('lessons', [])[:1]}")

    # L2 메모리 확인
    print("\n4) L2 메모리 로드 확인")
    l2 = ml.load_l2("ceo_001")
    print(f"   L2 내용: {l2 if l2 else '(없음)'}")

    board.stop()
    ceo.stop()

    import os
    conn.close()
    time.sleep(0.5)
    for p in [DB_PATH, DB_PATH+"-shm", DB_PATH+"-wal"]:
        try:
            if os.path.exists(p): os.remove(p)
        except Exception:
            pass

    print("\n✅ 회고 루프 테스트 완료")

if __name__ == "__main__":
    main()
