"""
페르소나 적용 확인 테스트.
CEO에게 직접 자기소개 요청 -> 시스템 프롬프트 반영 여부 확인.
Run: python test_persona.py
"""

import sys
import yaml
sys.path.insert(0, "..")

from core.llm import LLMClient
from core.persona_loader import PersonaLoader

def main():
    print("=== Persona 적용 확인 ===\n")

    with open("../config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    llm = LLMClient(config["llm"])
    agency_path = config.get("personas", {}).get("agency_agents_path", "")
    loader = PersonaLoader(agency_path)

    available = loader.available()
    print(f"로드 가능한 페르소나: {available}\n")

    # 각 페르소나별로 간단한 질문
    tests = [
        ("ceo_hacker",    "해커형 CEO", "당신은 누구이고 어떤 방식으로 사업 결정을 내리나요? 2-3문장으로 답하세요."),
        ("ceo_craftsman", "장인형 CEO", "당신은 누구이고 어떤 방식으로 사업 결정을 내리나요? 2-3문장으로 답하세요."),
        ("ceo_analyst",   "분석가형 CEO", "당신은 누구이고 어떤 방식으로 사업 결정을 내리나요? 2-3문장으로 답하세요."),
        ("board",         "Board",  "새 CEO가 '자동화 스크립트' 업종을 선택했습니다. 승인하시겠습니까? JSON으로 답하세요."),
    ]

    for key, label, question in tests:
        persona = loader.load(key)
        if not persona:
            print(f"[{label}] 페르소나 파일 없음 - 건너뜀\n")
            continue

        print(f"[{label}] 페르소나 길이: {len(persona)} chars")
        print(f"질문: {question}")

        try:
            response = llm.chat(
                system_prompt=persona,
                messages=[{"role": "user", "content": question}],
                max_tokens=300,
            )
            print(f"응답: {response[:300]}")
        except Exception as e:
            print(f"오류: {e}")
        print()

if __name__ == "__main__":
    main()
