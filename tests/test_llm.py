"""
llm.py 동작 확인용 테스트.
실행: python test_llm.py
"""

import sys
import yaml
sys.path.insert(0, "..")

from core.llm import LLMClient, LLMError

def main():
    with open("../config.yaml") as f:
        config = yaml.safe_load(f)

    client = LLMClient(config["llm"])

    print("=== LLM 연결 테스트 ===")
    try:
        response = client.chat(
            system_prompt="You are a helpful assistant. Reply in Korean.",
            messages=[{"role": "user", "content": "안녕! 지금 작동하면 '연결 성공'이라고만 답해줘."}],
        )
        print(f"응답: {response}")
        print("\n✅ LLM 연결 성공")
    except LLMError as e:
        print(f"\n❌ LLM 오류: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
