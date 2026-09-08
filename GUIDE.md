# Voice AI Agent 구축 가이드

**"음성 상담 에이전트를 안전하게 짓는 방법"**을 템플릿 코드와 함께 정리한 문서입니다.

- **뼈대(메커니즘)** — `template/s0_* ~ s6_*` … 재사용. 노트북의 셀 순서와 거의 1:1 대응.
- **도메인(데이터)** — `template/s1_domain.py` … **당신이 채워야 할 유일한 곳**. 아래 §3-도메인 지도를 보면서 채우세요.
- **앱** — `app/server.py` (FastAPI + 브라우저 콘솔) … 도메인을 채우면 즉시 실행되는 참조 구현.
- **완성 예제** — `example-project/` … 템플릿을 **택배/배송 고객센터 도메인으로 직접 채운 참고 구현**. §4를 따라 만들어진 결과물이라, "내가 만들 프로젝트"의 비교 대상으로 씁니다.

---

## 1. 방법론 — 이 템플릿이 지키는 6가지 설계 원칙

### 원칙 ① 파이프라인은 ‘내용’이 아니라 ‘계약’으로 연결한다
- 오디오 계약: **16kHz · mono · float32** + `{audio, sr, duration_ms, engine}`. 어느 ASR/TTS 가 와도 입구에서 `to_16k_mono()`로 규격화, `validate_audio_contract()`로 강제.
- ASR 계약: 어떤 엔진이든 `{engine, text}` 로 접는다.
- 도구 계약: OpenAI 함수 스키마 → `required ⊆ properties`, `additionalProperties=False`를 스키마 자체가 검증.
- JSON 계약: `TICKET_SCHEMA`(enum·추가 필드 금지).
> 교훈: "계약이 깨지면 즉시 실패하라" — 조용히 넘기는 것은 언젠가 '조용히 틀린 결과'를 낳는다.

### 원칙 ② 모의(mock) 우선, 실패는 숨기지 말고 보여라
- GPU/모델/API 없이도 오디오 계약용 **결정적 mock 합성음**, 규칙 분류기, **행동 시뮬레이터 mock LLM**으로 끝까지 돈다.
- 실패를 **assert/게이트로 소리 내며** 멈춘다. "변형 6개 중 5개를 놓쳤다"를 숨기지 않고 출력한다.
- 단계가 끝날 때마다 **회귀 게이트**로 종료 상태를 눌러둔다 (다음 단계 코드를 고쳐도 이전 기능이 깨지지 않았는지 재확인).

### 원칙 ③ 관대한 mock 은 뒤 단계를 거짓 통과시킨다 (mock fidelity)
- mock LLM 은 규칙 기반이라 실제 LLM 과 출력 '모양·제약'이 같아야 한다. 관대하게 넘겨주는 mock = 거짓 합격.
- mock 백엔드는 **실패·지연도 시뮬레이션**한다 ("항상 성공하는 mock 은 거짓말").
- 4단계 서버용 모의 ASR 도 '데모 캔드 스크립트'임을 명시.

### 원칙 ④ LLM 을 믿지 말고 검증하라
- 티켓 JSON 초안은 **형식 → 스키마 → 논리 3중 관문**(`triple_gate`)을 통과해야 한다.
- 논리 관문이 강제하는 것: 우선순위는 설계 고정값보다 **느슨해질 수 없다(내릴 수 없음)** · 최상위 인텐트(보안 등)는 **P1+escalate 강제** · `escalate=true` 면 반드시 최상위.
- "통과를 봐서는 검증이 아니다. **막아야 할 것을 막는지**를 본다" — 결함 주입 실험.

### 원칙 ⑤ 신원·상태는 LLM 이 아니라 코드·세션이 든다
- 도구 인자에 `identity`/`customer_id` 가 없다. 인증 결과는 **서버 세션**이 들고 있다 (최소 권한).
- 실패: 남의 케이스는 '권한 없음'이 아니라 **'없음'**(enumeration 방지). 인증 실패 사유도 구분해서 말하지 않는다.
- 상태 전이는 'if 남발'이 아니라 **전이표**(표에 없는 이동은 애초에 불가능).
- 쓰기(open/cancel)는 **BEGIN IMMEDIATE 원자적 트랜잭션**으로 슬롯 경합을 막는다.
- **실패도 감사 로그에 남긴다.**

### 원칙 ⑥ 지표 = 출시 계약
- ① 인텐트 정확도(골드 기준) ② 자가해결률(진단 트리) ③ 고위험(P1) 재현율.
- **재현율은 계약이다: 1.0 이 아니면 출시 금지.** 규칙 분류기는 변형 발화를 놓쳐 1.0 을 못 지키므로, 그 몫을 실물 LLM + 3중 관문으로 채운다.
- 마지막에 출시 점검표(12항)로 "진짜 다 지켰나"를 한 번에 확인.

---

## 2. 시작 (5분)

> 요구사항: **Python 3.10+** (타입 애너테이션 `X | None` 사용). 템플릿의 뼈대는 이미 만들어져 있고, **도메인만 채우면** 전부 돈다. 완성본을 먼저 보고 시작하고 싶다면 `example-project/`(택배/배송 도메인)를 참고하세요.

```bash
# 1) 복사 (실제 템플릿 디렉토리명은 template/)
cp -r template my-voice-agent && cd my-voice-agent

# 2) 의존성 (mock 최소: numpy, jsonschema)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3) 도메인 채우기: template/s1_domain.py 편집 (§3 지도와 슬롯 주석 참고)
# 4) 스모크 — 미채움 항목이 안내된다
python tests/test_smoke.py

# 5) 서버 (4단계) — 도메인 채운 뒤
cd app && python server.py    # → http://localhost:8000
```

도메인을 채우지 않아도 템플릿 코드는 import·자체검증이 모두 돈다(스모크 1부). 채운 뒤엔
회귀 게이트·예약 여정·지표가 실제 도는지 스모크 2부(메모리 채움 도메인)로 확인된다.

> 💡 템플릿을 복사하지 않고 이 저장소에서 바로 스모크를 돌려보려면:
> `python tests/test_smoke.py` (루트 템플릿은 도메인이 빈 기본 상태라 '미채움 안내' 1부 + 메모리 주입 2부가 돈다)

---

## 3. 6단계 워크플로

각 단계 = **목표 → 채울 도메인 → 만들 메커니즘 → 종료 게이트 → (선택) REAL 전환 → 근거 셀**.
노트북을 통째로 다시 읽지 않아도 되도록 파일-셀 매핑은 §부록 A.

### [0단계] 환경 점검
- 목표: 라이브러리·프로그램 준비도 확인. 최소 요건은 **numpy** 하나.
- 채울 도메인: 없음 (순수 환경 검사)
- 메커니즘: `s0_environment.py`(AVAIL/OLLAMA/FFMPEG 깃발), `s0_audio_contract.py`(오디오 계약·wav io·mock 합성음)
- 종료 게이트: `python template/s0_audio_contract.py` → "계약 방어 확인 ✅"
- 근거 셀: 노트북 셀 1~2 (셀 3~6 은 [REAL] 스모크)

### [1단계] 듣기 — 무엇을 알아들어야 하는가
- 목표: **골드 데이터 6종(Gold set)** 을 정하고, 규칙 분류 + 우선순위 + 콜흐름 + ASR 어댑터를 만든다.
- 채울 도메인 (`s1_domain.py`): `BASE_PRIORITY` → `INTENT_RULES` → `UTTERANCES` → `VARIANT_UTTERANCES` → `URGENT_MARKERS` → `CALL_FLOW` → `DIAG_TREES`/`NO_DIAG_INTENTS` → `DOMAIN_LEXICON`
- 메커니즘:
  - `s1_classifier.py` — 키워드 규칙(먼저 검사할 인텐트를 앞에) + 우선순위 정책(급해=한 단계만 승격, 내리기 금지)
  - `s1_callflow.py` — 상태 기계(앞으로만 전이) + 진단 질문 트리(진단 금지 인텐트는 즉시 접수)
  - `s1_asr.py` — ASREngine 계약 + MockWhisper + 환각 필터 + 후처리 사전
- 종료 게이트:
  - 골드 6/n 전부 `rule_intent == gold` (assert)
  - 우선순위: "급해요 + 일반(P3)" → P2 (한 단계만), "급해요 + 최상위(P1)" → P1 (승격 없음)
    (example-project 는 "급해요 + track" → P2, "택배 사기 신고" → P1)
  - 변형 문의로 규칙의 한계를 **눈으로 확인** (5/6 놓침이 정상)
- 근거 셀: 7~13, 15

### [2단계] 판단 — 3중 관문
- 목표: LLM 이 티켓 JSON 을 내되, **형식→스키마→논리**를 코드가 검증한다.
- 채울 도메인: `MUST_TOP_PRIORITY`(최상위 강제 인텐트) + 위 1단계 값들(enum 근거)
- 메커니즘:
  - `s2_prompts.py` — 프롬프트 계약(**문자열 연결로만** 조립, format 치환 금지) + `LLMClient`(+ MockTicketLLM, OllamaLLM 예시 주석)
  - `s2_gates.py` — `triple_gate`(JSON 파싱 → jsonschema → 논리) + 결함 주입 실험
- 종료 게이트: 결함 주입 전부 🛑(형식 깨짐·필드 누락·논리 위반), 골드 문의 전부 ✅
- 근거 셀: 16~19 (셀 20 은 [REAL] — 실제 LLM 관문 통과율 11+)

### [3단계] 실행 — DB·세션·도구·에이전트
- 목표: 접수·예약을 '안전한 코드'로 수행한다. 도구 6종 + 실행기 방화벽 + 왕복 루프.
- 채울 도메인: `SEED_CUSTOMERS` / `SEED_SERVICES` / `SEED_SLOT_OFFSET_DAYS` / `SEED_SLOT_TIMES` (+ `s3_policy`의 마감·상한 상수 조정)
- 메커니즘:
  - `s3_db.py` — 스키마 + `reset_db_state()`(몇 번 돌려도 같은 상태)
  - `s3_policy.py` — 전이표 + `policy_can_cancel`/`policy_can_open`(clock 주입)
  - `s3_session.py` — 세션·audit·`load_owned_case`(소유권='없음')
  - `s3_tools.py` — 도구 6종 + `execute_tool` 방화벽 + 스키마 무결성
  - `s3_agent.py` — `chat()` 왕복 루프(MAX_TOOL_TURNS) + `MockAgentLLM` 행동 시뮬레이터
  - `s3_regression_gates.py` — G1~G10 회귀 게이트
- 종료 게이트: **회귀 게이트 전부 통과** (`python -m template.s3_regression_gates`)
- 근거 셀: 21~34

### [4단계] 서빙 — FastAPI + 브라우저 콘솔
- 목표: 전화 상담을 HTTP API 로. `app/server.py` 단일 파일 참조 구현.
- 채울 도메인: `MockASR.transcribe` 의 데모 캔드 스크립트(시드와 맞추기) — 비워 둬도 서버는 뜬다.
  (example-project 에서는 이 캔드 스크립트를 **시드에 맞게 활성화**해 두었다 — [5단계]와 함께 원샷 데모가 된다.)
- 메커니즘: TTL 세션 저장소 · sess-인자 도구 · DB_LOCK 동시성 · `/api/session`·`/api/text-turn`·`/api/audio-turn`·`/api/health`
- 종료 게이트 (계약 검증):
  - 없는 세션 → **404** (조용히 새 세션 만들기 금지)
  - 빈 텍스트 → **400** (돈 나가는 LLM 호출 방어)
  - 정상 턴에서 `open_case` 도구가 실제로 돈다
  - **세션 격리** — B 세션에 A 케이스가 안 보인다
  - 짧은 오디오 → 400
  - uvicorn 기동 후 `/api/health` 실제 폴링 (떴겠지 대신)
- 근거 셀: server.py/index.html 생성 셀 + 계약 검증 셀들

### [5단계] 견고함 — 탄력성·PHI·지표
- 목표: 실전에서 망가지지 않게. 타임아웃 예산·서킷브레이커·멱등성·로그 마스킹·지표.
- 채울 도메인: `HIGH_RISK`(놓치면 안 되는 문의 — s5_metrics), PHI 패턴(전화번호 외 계좌 등)
- 메커니즘: `s5_resilience.py`(TimeoutBudget/CircuitBreaker/retryable_call/멱등키/MockHIS), `s5_phi_mask.py`, `s5_metrics.py`
- 종료 게이트:
  - 서킷브레이커: 열린 동안엔 **안 부른다**(calls 불변) + 쿨다운 후 회복
  - **재현율 1.0 = 계약** — [REAL] 로 확인, 아니면 출시 금지
- 근거 셀: 5단계 셀들 + [REAL] 고위험 관문

### [6단계] 출시 — 점검표 + 최종 회귀
- `template/s6_checklist.py` — 12항 점검표 + 메커니즘 재확인 + (도메인 채우면) 회귀 전체.
- 종료 기준: 점검표 전부 ✅ · 최종 회귀 assert 통과.

---

## 4. 프로젝트 만들기 — 시작해보기

### 4.0 프로젝트 초기화
```bash
# 작업공간 루트에서 (실제 템플릿 디렉토리명은 template/)
cp -r template my-voice-agent
cd my-voice-agent
python3 -m venv .venv && source .venv/bin/activate     # Python 3.10+
pip install -r requirements.txt
# 도메인 채우기 전 스모크 (모든 메커니즘 import/자체검증이 돈다)
python tests/test_smoke.py
```

> **완성본이 궁금하다면?** `example-project/` 가 템플릿을 그대로 복사해 **택배/배송 고객센터** 도메인으로 채운 결과물입니다. 아래 4.1~4.5를 이 저장소 안에서 따라하면서 `example-project/`와 diff 를 떠 비교하면 "내가 어떻게 채웠는가"가 바로 보입니다.

example-project 도메인 위에서 전부 돈다는 확인:
```bash
cd example-project
source .venv/bin/activate
python tests/test_smoke.py            # 택배 도메인 end-to-end
python -m template.s3_regression_gates
python -m template.s6_checklist
```

### 4.1 도메인 정의 — s1_domain.py 채우기
`template/s1_domain.py` 를 편집. **채우는 순서**가 결과에 영향을 준다:

1. **BASE_PRIORITY** — "어떤 인텐트를 알아들을지"의 목록 겸 기본 우선순위. 여기가 곧 분류·스키마·enum 의 근거.
   - 보안/고위험 최상위는 반드시 최상위 등급.
2. **INTENT_RULES** — 인텐트별 키워드표. 겹치는 단어가 있어도 **먼저 검사되는 인텐트**로 기운다.
   - "보안은 맨 먼저" — 반드시 리스트 앞쪽(또는 검사 우선순위)에.
3. **UTTERANCES + VARIANT_UTTERANCES** — 골드(반드시 잡아야 하는 것) + 변형(다른 표현).
   - uid 는 모의 ASR 의 wav 파일 stem 이 된다. **변형은 규칙이 놓치도록** 두어 1단계 한계·5단계 지표 실험을 살린다.
4. **URGENT_MARKERS / MUST_TOP_PRIORITY / NO_DIAG_INTENTS** — 승격 신호·최상위 강제·진단 금지.
5. **CALL_FLOW, DIAG_TREES** — 콜 여정과 자가해결 트리. `resolve_on` 응답이면 그 자리에서 해결.
6. **SEED_*** — DB 시드. 동명이인(같은 이름+다른 생년월일)을 넣으면 "이름+생년월일=신원" 방어를 회귀 게이트(G10)로 검증할 수 있다.
7. **DOMAIN_LEXICON** — ASR 오인식 → 표준 표기.

채우는 동안 `python tests/test_smoke.py` 를 반복 실행해 **남은 빈칸이 없어지는 것**을 확인한다.

> **⚠️ 도메인을 바꿀 때 s1_domain.py 밖에서 반드시 함께 손봐야 할 곳** (자주 놓치므로 여기에 모아 둔다):
> 1. `template/s3_agent.py` 의 `BOOKING_SERVICE` — 행동 시뮬레이터가 가정하는 **booking 서비스명**. `SEED_SERVICES`의 booking 이름과 일치시킬 것 (example-project 는 "기사 방문").
> 2. `app/server.py` 의 `MockAgent.BOOKING_SERVICE` — 자동으로 `SEED_SERVICES`에서 뽑지만, booking 이 없으면 `""`가 되므로 booking 서비스는 반드시 시드에 둘 것.
> 3. `app/server.py` 의 `MockASR.transcribe` — 4단계 데모용 '캔드 스크립트'(시드의 고객·서비스명과 맞춰야 한다).
> 4. `app/server.py` 의 `MockAgent.agent_step` 내 '내일/모레' 상대 날짜·'해주세요' 확정 문구·booking 서비스명 — 도메인에 맞게 조절.
> 5. `app/server.py` `_open` 의 활성 상한(`open_cnt >= 3`) — `template/s3_policy.MAX_OPEN_PER_CUSTOMER` 와 맞춘다.
> 6. `template/s5_metrics.py` 의 `HIGH_RISK` — "놓치면 안 되는" 문의(최상위 인텐트 원형 + 규칙이 놓칠 변형)를 넣어 재현율 계약을 살린다.
>
> 도메인 특정 단어는 `s1_domain.py` **한 곳**에만 모여 있어야 한다. `template/` 메커니즘 파일에 도메인 이름이 하드코딩돼 있으면(위 1·3·4 같은 곳) 그것들이 전부 "바꿔야 할 지점"이다.

### 4.2 벽돌 크기의 스텝 — 1단계 먼저
가장 작은 도메인(인텐트 2~3개)으로 시작해 `s1_classifier`, `s1_callflow` 의 selfcheck 를 통과시킨 뒤
확장한다. 모든 도메인 의존 게이트가 실패하지 않도록 **최소 시드를 먼저** 만들고,
스모크 2부처럼 그 위에서 회귀 게이트가 도는지 보면서 키운다.

### 4.3 회귀 체크
```bash
# 도메인 채운 뒤 회귀 게이트:G1~G10
python -m template.s3_regression_gates
# 전체 점검표
python -m template.s6_checklist
```

### 4.4 웹 콘솔로 수동 테스트
```bash
cd app && python server.py   # http://localhost:8000
```
마이크 녹음(MediaRecorder → `/api/audio-turn`)과 텍스트 fallback(`/api/text-turn`)을 모두 제공한다.
`/api/text-turn` 을 `curl` 로 테스트할 땐 한글은 반드시 `--data-urlencode` 로 보낸다.

example-project(택배) 데모 시나리오 — `MockASR` 캔드 스크립트가 시드와 맞으므로
"김하나 1985-05-12 이고요 내일 기사 방문 예약하고 싶어요" 를 한 턴만 보내면
본인확인 → 슬롯 조회 → (10시 선택) 접수까지 이어진다.

### 4.5 주의: 날짜는 오늘 기준
시드는 **'오늘' 기준으로 계산**(D0/D1/D2)되며 날짜 하드코딩을 금지한다. `s3_db.rel_dates(today)` 처럼
시계를 주입받는 구조를 유지하면 언제 실행해도 회귀·시연이 재현된다.

---

## 5. mock → REAL 전환 절차

mock 을 **완성하고 회귀 게이트가 전부 통과한 뒤에만** 실물로 갈아끼운다. 순서: ① ASR ② TTS ③ LLM 에이전트.
(엔진 실물을 먼저 연결하면 고장 위치를 구분할 수 없다.)

### 5.1 준비·환경변수
```bash
pip install mlx-whisper sherpa-onnx openai      # ASR/TTS/LLM
brew install ollama ffmpeg && ollama pull qwen2.5:32b
# .env: AIGC_LLM_MODEL / AIGC_ASR_MODEL / AIGC_TTS_SUPERTONIC / AIGC_PORT / AIGC_SESSION_TTL
```

### 5.2 ASR — mlx-whisper(Apple Silicon Metal)
```python
import mlx_whisper
class RealASR:
    name = "mlx-whisper"
    def transcribe(self, wav_path):
        # (audio-turn 은 webm → ffmpeg 로 16k mono wav 변환 후)
        r = mlx_whisper.transcribe(str(wav_path),
                                   path_or_hf_repo=os.getenv("AIGC_ASR_MODEL", "mlx-community/whisper-large-v3-turbo"),
                                   language="ko")
        return r["text"].strip()
server.ASR = RealASR()
```
출력은 반드시 `{가 없는} 순수 전사 문자열`(ASR 계약)로 — `template/s1_asr.postprocess_ko` 후처리와 환각 필터를 통과시켜라.

### 5.3 TTS — sherpa-onnx Supertonic(ko, CPU)
```python
import sherpa_onnx, numpy as np
MODEL = "sherpa-onnx-supertonic-3-tts-int8-2026-05-11"   # 최초 1회 GitHub release 다운로드
class RealTTS:
    name = "supertonic"
    def __call__(self, text):   # (audio, sr) — 오디오 계약으로 규격화
        so = sherpa_onnx; d = Path("models") / MODEL
        st = so.OfflineTtsSupertonicModelConfig(
            duration_predictor=str(d/"duration_predictor.int8.onnx"),
            text_encoder=str(d/"text_encoder.int8.onnx"),
            vector_estimator=str(d/"vector_estimator.int8.onnx"),
            vocoder=str(d/"vocoder.int8.onnx"), tts_json=str(d/"tts.json"),
            unicode_indexer=str(d/"unicode_indexer.bin"), voice_style=str(d/"voice.bin"))
        tts = so.OfflineTts(so.OfflineTtsConfig(model=so.OfflineTtsModelConfig(supertonic=st, provider="cpu")))
        g = so.GenerationConfig(); g.sid, g.speed, g.extra["lang"] = 0, 1.0, "ko"
        out = tts.generate(text, g)
        return np.asarray(out.samples, np.float32), int(out.sample_rate)
server.TTS = RealTTS()
```

### 5.4 LLM 에이전트 — Ollama qwen (오늘 날짜 주입!)
```python
from openai import OpenAI
MODEL = os.getenv("AIGC_LLM_MODEL", "qwen2.5:32b")
TODAY = ...  # str(today) — 날짜를 알려주지 않으면 모델은 과거 학습 시점의 날짜를 지어낸다
class RealAgent:
    def __init__(self):
        self.system = ("당신은 고객상담센터 음성 상담원입니다. 오늘 날짜는 " + TODAY +
                       " 입니다. 1) 본인 확인 2) 조회 3) 되읽어 확인 4) 확인 후 open_case. "
                       "cancel 은 cancel_case. 도구 결과만 쓰세요.")
    def agent_step(self, sess, text, history):
        c = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")
        msgs = [{"role":"system","content":self.system}] + history
        r = c.chat.completions.create(model=MODEL, messages=msgs, temperature=0.0)
        return {"content": r.choices[0].message.content or "네."}
server.LLM_AGENT = RealAgent()
```
주의:
- qwen 을 직접 쓸 때 **반드시 SYSTEM_PROMPT 에 오늘 날짜를 주입** — 안 하면 과거 날짜 환각으로 `get_available_slots` 가 엉뚱한 날짜를 조회한다.
- 도구 호출은 노트북 [3단계 REAL 셀]처럼 **auto + 왕복 루프**로 완성한다 (Ollama 는 `tool_choice` 강제 미지원).
- 실물 전환 후 **latency 대조**: mock 기준점을 남겨 두고(`/api/text-turn` 의 `timing_ms`), 실물이 얼마나 느려졌는지 수치로 비교. 예산이 없으면 `s5_resilience.TimeoutBudget` 으로 턴 전체 예산을 강제한다.

### 5.5 실물·관문 통과 확인
```python
# 3중 관문 + 실물 LLM: 고위험 재현율이 1.0 인지 (계약)
real = OllamaLLM(model=MODEL)            # s2_prompts 주석 참고
for uid, text in HIGH_RISK:              # template/s5_metrics
    d = triple_gate(real.chat([{"role":"user","content":build_ticket_prompt(text)}], json_mode=True), text)
    assert d["intent"] in MUST_TOP_PRIORITY and d["priority"] == P_ORDER[0] and d["escalate"] is True
```
**재현율 1.0 이 아니면 출시 금지.**

---

## 6. 함정 목록 (이걸 어기면 망한다)

| # | 함정 | 방어 (템플릿의 어디) |
|---|---|---|
| T1 | 날짜·경로 하드코딩 | `s3_db.rel_dates(today)` 주입, 시드 '오늘' 기준 |
| T2 | LLM 날짜 환각 | SYSTEM_PROMPT 에 오늘 주입 (§5.4) |
| T3 | 관대한 mock → 거짓 통과 | mock fidelity, 행동 시뮬레이터, 실패 시뮬레이션 |
| T4 | LLM 출력 무신뢰 | 3중 관문 `triple_gate` |
| T5 | 신원을 LLM 에 맡김 | 세션이 신원 보유, 도구 인자에 identity 없음 |
| T6 | 남의 케이스 존재 노출 | '권한 없음'→'없음', 인증 실패 사유 구분 금지 |
| T7 | 상태 전이 if 남발 | 전이표 `CASE_TRANSITIONS` |
| T8 | 슬롯 이중 예약(경합) | `BEGIN IMMEDIATE` 원자성 + `already_taken` |
| T9 | 4xx 재시도 | `retryable_call` 은 ConnectionError/Timeout 만 재시도 |
| T10 | 무한 도구 왕복 | `MAX_TOOL_TURNS` |
| T11 | 빈 입력/짧은 오디오로 LLM/TTS 호출 | `/api/text-turn`·`audio-turn` 400 |
| T12 | 세션만료/혼선 | TTL 세션 저장소, 세션 격리 |
| T13 | 로그에 PHI 노출 | `s5_phi_mask.PHIFilter` |
| T14 | 실패가 조용히 지나감 | assert·게이트·audit(실패 기록) |

---

## 7. 출시 점검표 12항

`template/s6_checklist.py` 의 `CHECKLIST` — 도메인과 무관한 계약 기준으로, mock 기준 전부 충족이 기본값.

1. 본인 확인 없이 쓰기 금지
2. 소유권 — 남의 케이스는 '없음'
3. 취소 시 슬롯 반납
4. 쓰기 작업은 원자적 트랜잭션
5. 실패도 감사 로그에 남는다
6. 상태 전이는 표로 제한
7. 정책 함수는 clock 주입 테스트 가능
8. 로그 개인정보 마스킹
9. 고위험(P1) 재현율 = 1.0 (계약)
10. 세션 격리 (TTL 포함)
11. 짧은 오디오/빈 입력 4xx
12. 백엔드 실패 시 즉시 실패 + 회로 차단

```bash
python -m template.s6_checklist    # 점검표 + 메커니즘 재확인
python tests/test_smoke.py         # 도메인 미채움 포함 전체 스모크
```
채운 도메인(예: `example-project/`)에서는 기본값이 전부 ✅ 로 충족되고, 회귀 게이트도 실제로 돈다.

---

## 부록 A — 파일 ↔ 진행 가이드 셀 매핑

| 템플릿 파일 | 단계 | 근거 셀 (진행 가이드) | 대응 병원/헬프데스크 |
|---|---|---|---|
| `s0_environment.py` | 0 | 셀1 | — |
| `s0_audio_contract.py` | 0 | 셀2~3 | 병원 00 '계약' |
| `app/server.py`·`static/index.html` | 4 | server.py/index.html 생성 셀 | 병원 03 |
| `s1_domain.py` | 1 | 셀7 (골드/우선순위), 셀8(키워드), 셀11(사전), 셀21(시드) | 헬프데스크 1단계 |
| `s1_classifier.py` | 1 | 셀8~9 | 헬프데스크 2단계 |
| `s1_callflow.py` | 1 | 셀10(상태기계), 셀13(진단트리) | 병원 01·00 |
| `s1_asr.py` | 1 | 셀11~12 | — |
| `s2_prompts.py` | 2 | 셀16 | 헬프데스크 3단계 |
| `s2_gates.py` | 2 | 셀17~19 | 헬프데스크 3단계 '믿지 말고 검증' |
| `s3_db.py` | 3 | 셀21 | 병원 00·01 |
| `s3_policy.py` | 3 | 셀22 | 병원 02 |
| `s3_session.py` | 3 | 셀23 | 병원 01 (세션) |
| `s3_tools.py` | 3 | 셀24~25 | 병원 01·02 |
| `s3_agent.py` | 3 | 셀26~28 | 병원 01 6단계, 병원 03 |
| `s3_regression_gates.py` | 3 | 셀29~33 | 병원 01 8단계, 병원 02 |
| `s5_resilience.py` | 5 | 5단계 셀(예산/서킷/멱등) | 병원 04 |
| `s5_phi_mask.py` | 5 | PHI 셀 | 병원 04 |
| `s5_metrics.py` | 5 | 셀(지표) | 헬프데스크 5단계 |
| `s6_checklist.py` | 6 | 점검표·최종 회귀 셀 | — |
| `tests/test_smoke.py` | 전체 | 스모크 재현 | — |

*("셀 n"의 n 은 노트북의 execution_count 와 대응; 4·5단계의 일부 셀은 미실행이어서 번호가 없어 설명으로 대체.)*

위 표의 "대응 병원/헬프데스크"는 이 가이드가 정리될 때 참고한 진행 가이드 도메인들이다. 같은 표는 **example-project(택배/배송)** 에서도 그대로 성립한다 — 이 저장소에서 바로 대조해 볼 수 있다:

- `example-project/template/s1_domain.py` = §4.1 의 7개 슬롯을 **택배 인텐트**(track/claim/redirect/booking/security)로 채운 완성본
- 실측값: 골드 정확도 100% · 3중 관문 차단 5 (형식/스키마/논리 결함 전부 🛑) · 회귀 게이트 10종 ✅ · 점검표 12/12 ✅ · 규칙 고위험 재현율 50% (변형이 만들어낸 계약의 간극 → 실물 LLM 몫, §5)
- `example-project/tests/test_smoke.py` = "빈 골격 + 메모리 주입"인 루트 템플릿 스모크와 달리, **채운 도메인 위에서** end-to-end 로 돈다.

## 부록 B — 선택 설비 (REAL 전용, mock 때엔 불필요)

- `pip install mlx-whisper` — Apple Silicon Metal 한국어 전사 (모델 `~1GB` 최초 1회)
- `pip install sherpa-onnx` — 로컬 한국어 TTS Supertonic (모델 `~130MB`, GitHub release 최초 1회)
- `brew install ollama ffmpeg && ollama pull qwen2.5:32b` — 로컬 LLM (OpenAI 호환 API)
- `gTTS` + `ffmpeg` — 외부 파일 없이 테스트 한국어 음성 합성(오프라인 대체: 440Hz 신호음)