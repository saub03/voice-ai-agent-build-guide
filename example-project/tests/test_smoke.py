# ════════════════════════════════════════════════════════════════════
# [example-project 스모크] example-project(template/s1_domain.py = 택배/배송 도메인)의
#   메커니즘이 "채운 도메인 위에서" 실제로 도는지 end-to-end 로 확인한다.
#   기본 템플릿의 tests/test_smoke.py 는 '빈 골격 + 메모리 주입' 방식이지만,
#   example-project 는 도메인을 실제로 채운 완성본이므로 자신의 도메인으로 검증한다.
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


def part1_domain_ready() -> None:
    """채운 도메인 상태: domain_status 가 ready 이고 도메인 무관 메커니즘이 돈다."""
    _section("[1부] 도메인 채움 상태 점검")
    from template import s1_domain as domain
    ready, missing = domain.domain_status()
    assert ready, f"example-project 의 s1_domain.py 가 미채움: {missing}"
    print("도메인 채움 상태 ✅  (인텐트", len(domain.BASE_PRIORITY), "종):",
          ", ".join(domain.intent_names()))

    # 도메인 무관 메커니즘 selfcheck (채워진 도메인 위에서 조율)
    from template import s0_audio_contract as audio
    audio.selfcheck()

    from template import s1_classifier as classifier
    classifier.selfcheck()

    from template import s1_callflow as callflow
    callflow.selfcheck()

    from template import s1_asr as asr
    asr.selfcheck()

    from template import s2_gates as gates
    gates.fault_injection_check()

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
    assert "도메인 미채움" not in agent.demo_journey()

    # 1단계: 택배 도메인 골드 정확도 = 1.0 + 우선순위 정책
    gold = [(t, g) for t, g, _svc in domain.UTTERANCES.values()]
    acc = sum(1 for t, g in gold if classifier.rule_intent(t) == g) / len(gold)
    assert acc == 1.0, f"골드 정확도 {acc}"
    print(f"① 택배 도메인 골드 정확도: {acc:.0%} ({len(gold)}종 전부 rule_intent==gold)")

    # 우선순위: 보안은 항상 P1(승격 없음), 긴급은 '한 단계만' 승격 (내리기 금지)
    assert classifier.rule_classify("택배 사기로 보이는 문자를 받았어요")["priority"] == "P1"
    assert classifier.classify_with_priority("급해요", "track") == "P2"  # P3 → P2 한 단계만
    assert classifier.classify_with_priority("급해요", "claim") == "P1"  # P2 → P1 한 단계만

    # 2단계: 3중 관문 (실제 도메인 위에서)
    from template import s2_gates as gates2
    from template import s2_prompts as prompts
    llm = prompts.MockTicketLLM()
    d = gates2.triple_gate(llm.chat([{"role": "user",
                                      "content": prompts.build_ticket_prompt("택배 사기로 보이는 문자를 받았어요")}]),
                           "x")
    assert d["intent"] == "security" and d["priority"] == "P1" and d["escalate"] is True
    passed, blocked = gates2.fault_injection_check()
    assert "정상" in passed and all("논리" in b or b in ("형식 깨짐", "필드 누락") for b in blocked)
    print(f"② 3중 관문: 통과 {len(passed)} / 차단 {len(blocked)} ✅")


def _apply_test_db() -> None:
    """DB 는 repo 를 오염시키지 않게 임시 경로로."""
    from template import s3_db
    s3_db.DEFAULT_DB_PATH = Path(tempfile.mkdtemp()) / "example.db"


def part2_end_to_end() -> None:
    _section("[2부] 채운 도메인 end-to-end — 회귀 게이트 + 예약 여정 + 지표")
    _apply_test_db()

    # 3단계: 회귀 게이트 전부 통과 (택배 도메인 SEED 위에서)
    from template import s3_regression_gates as rg
    results = rg.run_gates()
    failed = [k for k, v in results.items() if not v]
    assert not failed, f"회귀 게이트 실패: {failed}"
    print("회귀 게이트 전부 통과 ✅ (", len(results), "종)")

    # 3단계: 예약 여정 시연 (택배 도메인 booking '기사 방문' 기준)
    from template import s3_agent as agent
    journey = agent.demo_journey()
    assert "취소 + 슬롯 반납 확인" in journey
    print(journey)

    # 5단계: 지표 — 규칙은 변형을 놓쳐 고위험 재현율이 1.0 이 아니어야 함(계약의 간극)
    from template import s1_classifier as classifier
    from template import s1_domain as domain
    from template import s5_metrics as metrics
    gold = [(t, g) for t, g, _svc in domain.UTTERANCES.values()]
    acc = metrics.intent_accuracy(classifier.rule_intent, gold)
    recall = metrics.high_risk_recall(classifier.rule_intent)
    assert acc == 1.0, f"골드 정확도 {acc}"
    assert 0.0 < recall < 1.0, f"고위험 재현율(규칙) {recall} — 0<x<1 이어야 규칙의 간극을 보인다"
    print(f"③ 지표: 정확도 {acc:.0%} · 규칙 고위험 재현율 {recall:.0%} "
          f"(실물 LLM 으로 1.0 달성 지점 — GUIDE §5)")

    # 6단계: 점검표
    from template import s6_checklist as checklist_mod
    met, unmet = checklist_mod.report()
    assert not unmet

    # 서버 데모 — example-project app/server.py 의 MockASR 캔드 스크립트가 시드와 맞는지
    from template import s1_domain as dom2
    assert any(k == "booking" for _n, k in dom2.SEED_SERVICES), "booking 서비스가 SEED_SERVICES 에 있어야 함"


def main() -> None:
    print("example-project (택배/배송 도메인) 스모크 시작")
    part1_domain_ready()
    part2_end_to_end()
    _section("결과")
    print("example-project 도메인 · 분류/관문/회귀/예약/지표/점검표 전부 ✅")


if __name__ == "__main__":
    main()
