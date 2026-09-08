# ════════════════════════════════════════════════════════════════════
# [템플릿 스모크] 템플릿의 메커니즘이 깨지지 않았는지 확인한다.
#   - 1부 · 도메인-빈 상태 : s1_domain.py 를 채우기 전 기본 점검 + 미채움 안내
#   - 2부 · 메모리 채움 도메인 : 도메인을 (파일 수정 없이) 메모리에서 채워
#          분류·문해·회귀 게이트·시연·지표가 '뼈대'로 실제 도는지 end-to-end 검증
#
#   실행:  python tests/test_smoke.py
# ════════════════════════════════════════════════════════════════════
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))          # 'template' 패키지 임포트용


def _section(title: str) -> None:
    print("\n─────", title, "─────")


def part1_empty_domain() -> None:
    """도메인 미채움 상태: 안내가 나오고, 도메인 무관 메커니즘은 동작해야 한다."""
    _section("[1부] 빈 도메인 — 메커니즘 점검")
    from template import s1_domain as domain
    ready, missing = domain.domain_status()
    assert not ready, "skeleton 이 비어 있는데 ready=True? s1_domain.py 를 확인하세요"
    print("미채움 항목 (스모크):", len(missing), "개")
    for m in missing:
        print("   -", m)

    # 도메인 무관 메커니즘 selfcheck
    from template import s0_audio_contract as audio
    audio.selfcheck()

    from template import s1_classifier as classifier
    classroom = classifier.rule_classify("아무 문의나")
    assert classroom["intent"] == "unknown_not_filled"

    from template import s1_callflow as callflow
    print("callflow:", "미채움 안내 예정" if not domain.CALL_FLOW else "오류!")

    from template import s2_gates as gates
    assert gates.build_schema()["properties"]["intent"]["enum"] == []
    assert gates.fault_injection_check() == ([], [])

    from template import s3_policy as policy_mod
    policy_mod.selfcheck()

    from template import s3_session as session_mod
    session_mod.selfcheck()

    from template import s3_tools as tools
    tools.schema_integrity()
    assert tools.execute_tool("create_nothing", {})["error"] == "unknown_tool"
    print("도구 스키마 무결성 · 방화벽(unknown_tool) ✅")

    from template import s5_resilience as res
    res.selfcheck()

    from template import s5_phi_mask as phi
    phi.selfcheck()

    from template import s5_metrics as metrics
    metrics.selfcheck()

    from template import s3_agent as agent
    assert "도메인 미채움" in agent.demo_journey()


# ── [2부에서만 쓰는] 도메인 값 — s1_domain.py 를 고치지 않고 모듈 속성만 덮어쓴다 ──
FILLED_DOMAIN = {
    "P_ORDER": ["P1", "P2", "P3"],
    "BASE_PRIORITY": {"booking": "P3", "billing": "P3", "account": "P3",
                      "technical": "P3", "refund": "P2", "security": "P1"},
    "INTENT_RULES": {
        "security": ["보안", "유출", "해킹", "개인정보"],
        "refund": ["환불", "돌려", "결제 두 번"],
        "account": ["계정", "잠겼", "비밀번호"],
        "booking": ["방문", "예약", "기사"],
        "billing": ["요금", "청구"],
        "technical": ["인터넷", "끊기"],
    },
    "UTTERANCES": {
        "g01": ("모레 기사 방문 예약 가능한지 알고 싶어요", "booking", "기사 방문"),
        "g02": ("지난달 요금이 많이 나온 것 같아요", "billing", "요금 문의"),
        "g03": ("개인정보가 유출된 것 같아요", "security", "보안 신고"),
    },
    "VARIANT_UTTERANCES": {
        "v1_booking": "다음 주 초에 누가 시간 맞춰서 와주실 수 있어요?",
        "v5_security": "제 아이디가 남이 쓰는 것 같아요",
    },
    "URGENT_MARKERS": ["급해", "당장"],
    "MUST_TOP_PRIORITY": {"security"},
    "NO_DIAG_INTENTS": {"security"},
    "DIAG_TREES": {
        "account": [{"q": "재설정 링크를 받으실 수 있나요?",
                     "do": "재설정 링크를 보내드렸습니다.", "resolve_on": "네"}],
        "technical": [{"q": "공유기를 껐다 켜보셨나요?",
                       "do": "재부팅 후 확인해 주세요.", "resolve_on": "네"}],
    },
    "CALL_FLOW": [
        {"state": "greeting", "expect": "무엇을 도와드릴지", "bot": "무엇을 도와드릴까요?"},
        {"state": "identify", "expect": "이름+생년월일", "bot": "성함과 생년월일을 말씀해 주세요."},
        {"state": "closed",  "expect": "없음", "bot": "안녕히 가세요."},
    ],
    "SEED_CUSTOMERS": [("김하나", "1985-05-12"), ("김하나", "1990-11-02"), ("박민준", "1978-03-21")],
    "SEED_SERVICES": [("기사 방문", "booking"), ("요금 문의", "ticket"), ("환불 요청", "ticket"),
                      ("계정 잠금", "ticket"), ("통신 장애", "ticket")],
    "SEED_SLOT_OFFSET_DAYS": [1, 2],
    "SEED_SLOT_TIMES": ["09:00", "10:00", "11:00", "14:00", "16:00"],
    "DOMAIN_LEXICON": {"와이 파이": "와이파이"},
}


def _apply_filled_domain() -> None:
    from template import s1_domain as domain
    for k, v in FILLED_DOMAIN.items():
        setattr(domain, k, v)
    from template import s5_metrics as metrics
    metrics.HIGH_RISK = [("hr1", "개인정보가 유출된 것 같아요"), ("hr2", "제 아이디가 남이 쓰는 것 같아요")]
    # DB 는 repo 를 오염시키지 않게 임시 경로로
    import tempfile
    from template import s3_db
    s3_db.DEFAULT_DB_PATH = Path(tempfile.mkdtemp()) / "guide.db"


def part2_filled_domain() -> None:
    _section("[2부] 메모리 채움 도메인 — 뼈대가 실제로 돈다")
    _apply_filled_domain()
    from template import s1_domain as domain

    ready, missing = domain.domain_status()
    assert ready, f"여전히 미채움: {missing}"

    # 1단계: 규칙 분류 + 콜흐름
    from template import s1_classifier as classifier
    from template import s1_callflow as callflow
    gold = [(t, g) for t, g, _svc in domain.UTTERANCES.values()]
    acc = sum(1 for t, g in gold if classifier.rule_intent(t) == g) / len(gold)
    assert acc == 1.0, f"골드 정확도 {acc}"
    assert classifier.rule_classify("개인정보가 유출된 것 같아요 당장")["priority"] == "P1"
    assert classifier.classify_with_priority("급해요", "billing") == "P2"
    callflow.selfcheck()

    # 2단계: 3중 관문
    from template import s2_gates as gates
    from template import s2_prompts as prompts
    llm = prompts.MockTicketLLM()
    d = gates.triple_gate(llm.chat([{"role": "user",
                                     "content": prompts.build_ticket_prompt("개인정보가 유출된 것 같아요")}]),
                          "x")
    assert d["intent"] == "security" and d["priority"] == "P1" and d["escalate"] is True
    passed, blocked = gates.fault_injection_check()
    assert "정상" in passed and all("논리" in b or b in ("형식 깨짐", "필드 누락") for b in blocked)
    print(f"3중 관문: 통과 {len(passed)} / 차단 {len(blocked)} ✅")

    # 3단계: 회귀 게이트 전부 통과 (도메인 위에서 도메인 규칙·DB 규칙이 지켜지는지)
    from template import s3_regression_gates as rg
    results = rg.run_gates()
    failed = [k for k, v in results.items() if not v]
    assert not failed, f"회귀 게이트 실패: {failed}"
    print("회귀 게이트 전부 통과 ✅")

    # 3단계: 예약 여정 시연 (어른스럽게 DB 를 임시 경로에 두고)
    from template import s3_agent as agent
    journey = agent.demo_journey()
    assert "취소 + 슬롯 반납 확인" in journey
    print(journey)

    # 5단계: 지표 — 규칙은 변형을 놓쳐 고위험 재현율이 1.0 이 아니어야 함(계약의 간극)
    from template import s5_metrics as metrics
    acc2 = metrics.intent_accuracy(classifier.rule_intent, gold)
    recall = metrics.high_risk_recall(classifier.rule_intent)
    assert 0.0 < recall < 1.0, f"고위험 재현율(규칙) {recall} — 0<x<1 이어야 규칙의 간극을 보인다"
    print(f"지표: 정확도 {acc2:.0%} · 규칙 고위험 재현율 {recall:.0%} (실물 LLM 으로 1.0 달성 지점)")

    # 6단계: 점검표
    from template import s6_checklist as checklist_mod
    met, unmet = checklist_mod.report()
    assert not unmet


def main() -> None:
    print("voice-ai-agent-template 스모크 시작")
    part1_empty_domain()
    part2_filled_domain()
    _section("결과")
    print("1부(빈 도메인 메커니즘) ✅ · 2부(메모리 채움 도메인 end-to-end) ✅")


if __name__ == "__main__":
    main()