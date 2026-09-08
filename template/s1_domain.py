from __future__ import annotations

# ════════════════════════════════════════════════════════════════════
# [1단계 · 도메인] ★ 이 파일만 채우면 됩니다 (도메인 = 빈 골격)
#
#   아래 슬롯은 전부 "이 도메인만의 것"입니다. 기본값은 빈 상태로 두었고,
#   각 슬롯 주석에 (예: 콜센터 도메인) 값을 적어 두었습니다.
#   - 이 파일의 함수/메커니즘은 template/s1_* … s6_* 모듈이 import 해서 씁니다.
#   - 다 채운 뒤에는 python tests/test_smoke.py 로 "남은 빈칸"을 확인하세요.
#
#   ⚠️ 순서:
#     1. BASE_PRIORITY 로 "알아들을 인텐트 목록"을 정한다  (기준이 되는 곳)
#     2. INTENT_RULES / UTTERANCES(골드) / VARIANT_UTTERANCES 를 채운다
#     3. CALL_FLOW · DIAG_TREES · NO_DIAG_INTENTS · MUST_TOP_PRIORITY 를 채운다
#     4. SEED_* 로 DB 시드(고객·서비스·슬롯)를 채운다
#
#   ※ 참고 (이 슬롯이 쓰이는 곳)
#     - s1_classifier : INTENT_RULES, BASE_PRIORITY, P_ORDER, URGENT_MARKERS
#     - s1_callflow   : CALL_FLOW, DIAG_TREES, NO_DIAG_INTENTS
#     - s1_asr        : DOMAIN_LEXICON
#     - s2_prompts/gates : intent_names(), P_ORDER, MUST_TOP_PRIORITY
#     - s3_db         : SEED_CUSTOMERS, SEED_SERVICES, SEED_SLOT_*
# ════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────
# 1. 우선순위 척도 — P_ORDER[0] 이 최상위. 도메인 등급이 다르면 수정.
#    (예: ["P1", "P2", "P3"] — 보안/고위험 = P1, 일반 = P3)
# ─────────────────────────────────────────────────────────────
P_ORDER: list[str] = ["P1", "P2", "P3"]

# 인텐트별 기본 우선순위 (intent 이름 자체가 곧 분류의 결과 목록이 된다)
#    (예: {"security": "P1", "refund": "P2", "technical": "P3",
#          "billing": "P3", "account": "P3", "booking": "P3"})
BASE_PRIORITY: dict[str, str] = {}

# ─────────────────────────────────────────────────────────────
# 2. 인텐트 판별 — 키워드 규칙 + 골드 데이터
# ─────────────────────────────────────────────────────────────
# 인텐트 분류 키워드표. 검사 순서도 중요하다: 겹치는 단어가 있어도
# 리스트 앞쪽(먼저 검사되는) 인텐트로 기운다. 먼저 검사할 인텐트를 앞에 둘 것.
#    (예: {"security": ["보안","유출","해킹","개인정보","증적","신고"],
#          "refund":   ["환불","돌려","이중결제","결제 두 번"],
#          "account":  ["계정","잠겼","비밀번호","로그인"],
#          "booking":  ["방문","예약","기사","점검"],
#          "billing":  ["요금","청구","결제 금액"],
#          "technical":["인터넷","와이파이","끊기","통신","네트워크"]})
INTENT_RULES: dict[str, list[str]] = {}

# 골드 데이터 — "이 프로젝트가 반드시 알아들어야 하는 발화". uid → (원문, intent, 서비스명)
#    (예: {"g01": ("모레 기사 방문 예약 가능한지 알고 싶어요", "booking", "기사 방문"),
#          "g02": ("지난달 요금이 많이 나온 것 같아요", "billing", "요금 문의")})
#   ※ uid 는 wav 파일 stem 으로도 쓰인다 (모의 ASR 이 uid → 정답 텍스트를 찾는 규칙)
UTTERANCES: dict[str, tuple[str, str, str]] = {}

# 변형 발화 — 같은 뜻, 다른 표현 (규칙이 놓치는 간극을 LLM 판단으로 메우는 실험·지표용)
#    (예: {"v1_booking": "다음 주 초에 누가 시간 맞춰서 와주실 수 있어요?"})
VARIANT_UTTERANCES: dict[str, str] = {}

# 긴급 신호 — 우선순위를 '올릴 수만' 있고, 절대 내리지 않는다 (s1_classifier)
#    (예: ["급해", "급합니다", "당장", "회의 중", "빨리 와주세요", "지금 바로"])
URGENT_MARKERS: list[str] = []

# ─────────────────────────────────────────────────────────────
# 3. 대화 흐름 · 진단 트리
# ─────────────────────────────────────────────────────────────
# 콜 흐름 상태 기계 — 상태마다 "무엇을 묻는지(bot 문구)/무엇을 기대하는지(expect)".
#    상태는 앞으로만 전이한다. (bot 문구에 {summary} 등 자리표시자를 쓸 수 있음)
#    (예: [{"state": "greeting", "expect": "무엇을 도와드릴지", "bot": "무엇을 도와드릴까요?"},
#          {"state": "identify", "expect": "이름+생년월일",     "bot": "성함과 생년월일을 말씀해 주세요."},
#          ... , {"state": "closed", "expect": "없음", "bot": "안녕히 가세요."}])
CALL_FLOW: list[dict] = []

# 진단 질문 트리 — 티켓 전에 자가해결을 시도한다.
#    node = {"q": 질문, "do": 질문에 대한 조치/안내, "resolve_on": 이 응답이면 해결("네" 등)}
#    resolve_on 이 None 인 마지막 노드는 매칭되면 실패(접수)로 전환된다.
#    (예: {"account": [{"q": "재설정 링크를 받으실 수 있나요?",
#                       "do": "재설정 링크를 보내드렸습니다.", "resolve_on": "네"}],
#          "technical": [{"q": "공유기를 껐다 켜보셨나요?", "do": "재부팅 후 확인해 주세요.", "resolve_on": "네"}]})
DIAG_TREES: dict[str, list[dict]] = {}

# 진단 금지 인텐트 — 봇이 자가조치를 안내하면 안 되는 인텐트 (보안 신고 등) → 즉시 최상위 접수
#    (예: {"security"})
NO_DIAG_INTENTS: set[str] = set()

# 최상위 강제 인텐트 — 이 인텐트는 어떤 경우든 최상위(P_ORDER[0]) + escalate 이어야 한다.
#    (예: {"security"})  ※ s2_gates 의 논리 관문이 강제한다.
MUST_TOP_PRIORITY: set[str] = set()

# ─────────────────────────────────────────────────────────────
# 4. DB 시드 — s3_db.reset_db_state() 가 이 값으로 테이블을 채운다.
#    ※ 이 시드가 곧 "회귀 게이트가 재현될 수 있게" 하는 베이스라인이다.
# ─────────────────────────────────────────────────────────────
# 고객. 동명이인을 넣으면 "이름+생년월일 = 신원" 방어(중복 → 상담원 연결)를 검증할 수 있다.
#    (예: [("김하나", "1985-05-12"), ("김하나", "1990-11-02"), ("박민준", "1978-03-21")])
SEED_CUSTOMERS: list[tuple[str, str]] = []

# 서비스 카탈로그 — kind: "booking"(슬롯 필요) 또는 "ticket"(접수만)
#    (예: [("기사 방문", "booking"), ("요금 문의", "ticket"), ("보안 신고", "ticket")])
SEED_SERVICES: list[tuple[str, str]] = []

# booking 서비스의 슬롯: '오늘 기준' 며칠 뒤에 열지를 결정 (하드코딩 날짜 금지)
#    (예: [1, 2]  → 내일·모레)
SEED_SLOT_OFFSET_DAYS: list[int] = []

# booking 서비스의 하루 슬롯 시각
#    (예: ["09:00", "10:00", "11:00", "14:00", "16:00"])
SEED_SLOT_TIMES: list[str] = []

# ─────────────────────────────────────────────────────────────
# 5. 언어 후처리 사전 (ASR 오인식 → 표준 표기) — s1_asr.postprocess_ko
#    (예: {"와이 파이": "와이파이", "에이 에스": "AS", "카드 사": "카드사"})
# ─────────────────────────────────────────────────────────────
DOMAIN_LEXICON: dict[str, str] = {}


# ── 아래는 메커니즘 헬퍼 (채울 필요 없음) ────────────────────────
def intent_names() -> list[str]:
    """인텐트 목록 = BASE_PRIORITY 의 키 (정렬). 이 값이 곧 분류·스키마의 enum 이 된다."""
    return sorted(BASE_PRIORITY.keys())


# (슬롯 이름, 채워야 하는 이유) — 도메인 미채움 안내를 위해
_REQUIRED_SLOTS: dict[str, str] = {
    "BASE_PRIORITY": "intent → 기본 우선순위 (분류 결과 목록의 근거). 가장 먼저 채울 것",
    "INTENT_RULES": "인텐트별 키워드표",
    "UTTERANCES": "골드 발화 uid → (text, intent, 서비스명)",
    "CALL_FLOW": "콜 흐름 상태 목록 [{state, expect, bot}, …]",
    "DIAG_TREES": "진단 질문 트리 (최소 1개 인텐트 권장)",
    "SEED_CUSTOMERS": "고객 시드 [(name, birth_date), …]",
    "SEED_SERVICES": "서비스 시드 [(name, kind), …]",
}


def domain_status() -> tuple[bool, list[str]]:
    """(채워졌는가, 남은 항목 설명). 가이드·스모크·회귀 실행기가 사용한다."""
    missing: list[str] = []
    for key, desc in _REQUIRED_SLOTS.items():
        if not globals().get(key):  # 빈 dict/list/None → 채워야 함
            missing.append(f"{key}  ← {desc}")
    return not missing, missing
