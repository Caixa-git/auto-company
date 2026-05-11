"""
MemoryLoader 테스트.
DB에 가짜 회고/sector_db 데이터를 심고 압축 결과 확인.
Run: python test_memory.py (tests/ 폴더 안에서)
"""

import sys
import json
sys.path.insert(0, "..")

from core.db import get_thread_connection, init_schema
from core.message_bus import MessageBus
from core.memory_loader import MemoryLoader

DB_PATH = "../test_memory.db"

def seed_retrospectives(conn, agent_name: str):
    """가짜 회고 데이터 삽입."""
    retros = [
        {
            "sector": "AI 콘텐츠 생성",
            "retrospective": {
                "outcome": "failure",
                "roi": -40.0,
                "lessons": ["마케팅 비용 과다", "경쟁 포화 업종"],
            }
        },
        {
            "sector": "자동화 스크립트",
            "retrospective": {
                "outcome": "success",
                "roi": 30.0,
                "lessons": ["Tier 0 초기 자본 불필요", "빠른 MVP 효과적"],
            }
        },
        {
            "sector": "정보 중개",
            "retrospective": {
                "outcome": "failure",
                "roi": -15.0,
                "lessons": ["신뢰 구축 시간 과소평가"],
            }
        },
    ]
    for r in retros:
        conn.execute(
            "INSERT INTO event_log (agent_name, event_type, detail) VALUES (?, ?, ?)",
            (agent_name, "retrospective", json.dumps(r, ensure_ascii=False))
        )
    conn.commit()
    print(f"  회고 {len(retros)}건 삽입 완료")

def seed_sector_db(conn):
    """가짜 sector_db 삽입 (system_cfo 상태)."""
    state = {
        "total_capital": 1_000_000,
        "deployable": 600_000,
        "active_companies": 1,
        "exit_count": 1,
        "failure_count": 2,
        "sector_db": {
            "자동화 스크립트": {"total": 1, "success": 1, "failure": 0, "success_rate": 1.0, "avg_roi": 30.0},
            "AI 콘텐츠 생성": {"total": 1, "success": 0, "failure": 1, "success_rate": 0.0, "avg_roi": -40.0},
            "정보 중개":      {"total": 1, "success": 0, "failure": 1, "success_rate": 0.0, "avg_roi": -15.0},
        }
    }
    conn.execute(
        """
        INSERT INTO agent_states (agent_name, status, state_json)
        VALUES ('system_cfo', 'running', ?)
        ON CONFLICT(agent_name) DO UPDATE SET state_json=excluded.state_json
        """,
        (json.dumps(state, ensure_ascii=False),)
    )
    conn.commit()
    print("  sector_db 삽입 완료")

def main():
    print("=== MemoryLoader 테스트 ===\n")

    import os
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = get_thread_connection(DB_PATH)
    init_schema(conn)
    ml = MemoryLoader(DB_PATH)

    # 1. 빈 상태
    print("1) 빈 상태 (첫 창업)")
    print(f"  L2: {repr(ml.load_l2('ceo_001'))}")
    print(f"  L3: {repr(ml.load_l3())}")
    print(f"  CEO 블록: {repr(ml.load_for_ceo('ceo_001'))}\n")

    # 2. 데이터 심기
    print("2) 회고 + sector_db 데이터 삽입")
    seed_retrospectives(conn, "ceo_001")
    seed_sector_db(conn)
    print()

    # 3. 로드 결과 확인
    print("3) L2 회고 압축 결과:")
    l2 = ml.load_l2("ceo_001")
    print(f"  길이: {len(l2)}자 (목표: 500자 이하)")
    print(f"  내용:\n{l2}\n")

    print("4) L3 sector_db 압축 결과:")
    l3 = ml.load_l3()
    print(f"  길이: {len(l3)}자 (목표: 300자 이하)")
    print(f"  내용:\n{l3}\n")

    print("5) CEO 전체 메모리 블록:")
    ceo_mem = ml.load_for_ceo("ceo_001")
    print(f"  길이: {len(ceo_mem)}자")
    print(f"  내용:\n{ceo_mem}\n")

    print("6) Board 포트폴리오 블록:")
    board_mem = ml.load_for_board()
    print(f"  길이: {len(board_mem)}자")
    print(f"  내용:\n{board_mem}\n")

    # 7. 길이 검증
    print("7) 길이 검증")
    assert len(l2) <= 500, f"L2 길이 초과: {len(l2)}"
    assert len(l3) <= 300, f"L3 길이 초과: {len(l3)}"
    print("  L2 <= 500자 OK")
    print("  L3 <= 300자 OK")

    # Windows: 연결 명시적으로 닫고 삭제
    conn.close()
    import os, time
    time.sleep(0.5)
    for path in [DB_PATH, DB_PATH+"-shm", DB_PATH+"-wal"]:
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass  # 삭제 실패해도 테스트에 영향 없음

    print("\n✅ MemoryLoader 테스트 완료")

if __name__ == "__main__":
    main()
