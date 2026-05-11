# Auto-Company-System (ACS)

> **"능력은 있는데 시작을 못 하는 문제를 해결하는 시스템"**
>
> 외부 소통, 걱정, 맥락 손실 — 세 가지 실행 장벽을 AI 에이전트가 대신 넘어줍니다.
> 위진수(Human-on-the-Loop)는 Discord DM에서 버튼만 누르면 됩니다.

---

## 개요

ACS는 AI 에이전트들이 Tier 0 디지털 사업을 자율적으로 운영하는 시스템입니다.  
CEO 에이전트가 업종을 선택하고 사업을 실행하며, Board가 감독하고, CFO가 재무를 관리합니다.  
사람(위진수)은 절대 승인 사항에만 개입합니다.

```
위진수 (Human-on-the-Loop)
    ↕ Discord DM (승인 버튼)
Board ──── System CFO
  │              │
CEO + CFO    Meta-Learning
  │
실행 (이메일 발송 / API)
```

---

## 핵심 특징

### Glasswing 자율성 프레임워크
성과에 따라 에이전트 자율성이 자동으로 높아집니다.

| Stage | 이름 | 자율 한도 | 알림 필터 |
|-------|------|-----------|-----------|
| 0 | Full Oversight | 0% | 전부 알림 |
| 1 | Supervised | 예산 5% | MEDIUM 이상 |
| 2 | Assisted | 예산 20% | HIGH 이상 |
| 3 | Autonomous | 예산 40% | HIGH 이상 |
| 4 | Full Autonomy | 무제한 | ABSOLUTE만 |

성공 3회 → Stage 2 자동 승급 / 연속 실패 2회 → 자동 강등

### 3계층 메모리 구조
```
L1. Working Memory  — 현재 세션 대화 (휘발)
L2. Episodic Memory — 과거 회고 기록 (DB 영속)
L3. Semantic Memory — 업종별 학습 데이터 (Meta-Learning)
```

재시작해도 L2/L3는 유지 — 에이전트가 경험을 축적합니다.

### CEO 성향 시스템
창업마다 성향이 랜덤 배정되며, agency-agents 검증 페르소나 기반입니다.

| 성향 | 기반 페르소나 | 특징 |
|------|---------------|------|
| 해커형 | Rapid Prototyper | 속도 우선, 빠른 MVP |
| 장인형 | Senior Developer | 품질 우선, 안정적 |
| 분석가형 | Product Manager | 데이터 기반, 검증 중심 |

---

## 아키텍처

```
acs/
├── main.py                  # 시스템 부트스트랩
├── discord_bot.py           # Discord DM 인터페이스
├── config.yaml              # API 키 및 설정
│
├── core/
│   ├── llm.py               # DeepSeek API 직접 호출
│   ├── db.py                # SQLite + 스레드 안전
│   ├── message_bus.py       # 에이전트 간 메시지 큐
│   ├── glasswing.py         # 자율성 단계 관리
│   ├── identity_loader.py   # 에이전트 Identity 로드
│   ├── behavior_loader.py   # 에이전트 Behavior 로드
│   ├── memory_loader.py     # L2/L3 메모리 압축 로드
│   ├── persona_loader.py    # agency-agents 원본 로드
│   └── discord_logger.py    # Discord DM 로그 핸들러
│
├── agents/
│   ├── base_agent.py        # 공통 루프 + 메모리
│   ├── board.py             # 승인/에스컬레이션
│   ├── ceo.py               # 창업/실행/회고
│   ├── company_cfo.py       # 예산/지출 관리
│   ├── system_cfo.py        # 포트폴리오 + Meta-Learning
│   ├── system_auditor.py    # 장애 감지 (독립 실행)
│   ├── identities/          # 에이전트 Identity JSON
│   └── behaviors/           # 에이전트 Behavior MD
│
└── tests/                   # 단위 테스트
```

---

## 시작하기

### 요구사항
- Python 3.11+
- DeepSeek API 키
- Discord Bot 토큰

### 설치

```bash
git clone https://github.com/your-repo/auto-company-system
cd auto-company-system
pip install pyyaml discord.py
```

### 설정

`config.yaml` 편집:

```yaml
llm:
  api_key: "YOUR_DEEPSEEK_API_KEY"
  model: "deepseek-chat"

system:
  initial_capital: 1000000   # 초기 자본 (원)
  db_path: "acs.db"

personas:
  agency_agents_path: "C:/path/to/agency-agents"  # 선택사항

hotl:
  discord:
    token: "YOUR_BOT_TOKEN"
    owner_id: 0  # 본인 Discord 유저 ID
```

### 실행

```bash
# Windows
start.bat --reset    # DB 초기화 후 시작
start.bat            # 그냥 시작
stop.bat             # 종료

# 직접 실행
python main.py
python discord_bot.py
```

### Discord 명령어 (DM)

| 명령어 | 설명 |
|--------|------|
| `status` | 에이전트 상태 + 포트폴리오 + Glasswing 단계 |
| `alerts` | 미처리 알림 목록 |
| `stage 0~4` | Glasswing 자율성 단계 수동 설정 |
| `help` | 명령어 목록 |

---

## 작동 흐름

```
1. ACS 시작
   └── System CFO, Board, Auditor 초기화

2. 회사 창업
   └── CEO (랜덤 성향) + Company CFO 생성
   └── CEO → 업종 선택 → Board 승인 요청
   └── Board → Glasswing 판단 → 자율 승인 or 위진수 DM

3. 사업 실행
   └── CEO → 실행 계획 수립
   └── 외부 액션 → 이메일 초안 작성 → 위진수 Discord 승인
   └── 승인 → 발송 → CFO 수익 기록

4. 성공/실패 판단
   └── 예산 15% 이하 → 실패
   └── 수익 200% 달성 → 성공
   └── 회고 작성 → L2 메모리 저장 → 다음 창업에 반영

5. Meta-Learning
   └── System CFO → 업종 성공률/ROI 누적
   └── Glasswing 자동 승급/강등
```

---

## 절대 승인 항목

어떤 Glasswing 단계에서도 위진수 직접 승인 필요:

- Exit (회사 매각)
- Human CEO/CFO 전환
- Human Manager 고용/해고
- 시스템 전체 종료

---

## 외부 의존성

| 패키지 | 용도 |
|--------|------|
| `pyyaml` | 설정 파일 파싱 |
| `discord.py` | Discord 봇 |

LLM 프레임워크(LangChain, CrewAI 등) **미사용**.  
모든 에이전트 통신은 SQLite 기반 메시지 버스로 처리.

---

## 관련 프로젝트

- [agency-agents](https://github.com/msitarzewski/agency-agents) — 에이전트 페르소나 소스

---

## 라이선스

MIT
