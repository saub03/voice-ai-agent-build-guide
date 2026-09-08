from __future__ import annotations

# ════════════════════════════════════════════════════════════════════
# [5단계 · 평가] 지표 3개로 시스템의 건강을 잰다.
#   ① 인텐트 정확도   : 골드만 기준 (classifier/predict_fn 과 골드셋 비교)
#   ② 자가해결률      : 진단 트리 위에서 '해결/전체'
#   ③ 고위험(P1) 재현율 : '놓치면 안 되는 문의'를 놓치지 않는가 = 계약 (1.0 이어야 출시)
#   ★ 재현율은 계약이다. 규칙 분류기는 변형 발화를 놓쳐 1.0 을 못 지킨다.
#     그것이 실물 LLM([REAL])로 채워야 할 몫이다 — GUIDE §5 참고.
# ════════════════════════════════════════════════════════════════════
from typing import Callable


def intent_accuracy(predict_fn: Callable[[str], str], dataset: list[tuple[str, str]]) -> float:
    """(텍스트, 골드 인텐트) 셋의 예측 정확도."""
    if not dataset:
        return 0.0
    return sum(1 for t, g in dataset if predict_fn(t) == g) / len(dataset)


def self_resolve_rate(diagnoser, scenarios: list[tuple[str, list[str]]]) -> float:
    """진단 시나리오 (intent, answers[]) 에서 자가해결된 비율.
    diagnoser: (intent, answers) → (resolved, record) — s1_callflow.run_diagnosis."""
    if not scenarios:
        return 0.0
    resolved = 0
    for intent, answers in scenarios:
        ok, _rec = diagnoser(intent, answers)
        if ok:
            resolved += 1
    return resolved / len(scenarios)


# 고위험(P1) 문의 — 놓치면 계약 위반. [도메인] 여기에 '놓치면 안 되는' 케이스를 넣는다.
# (예: 보안 신고 원형 + 변형) — 택배/배송 도메인: 사기 신고 원형 + 규칙이 놓치는 변형
HIGH_RISK: list[tuple[str, str]] = [
    ("hr1", "택배 사기로 보이는 문자를 받았어요"),
    ("hr2", "카톡으로 이상한 배송 링크가 와서요 뭔가요"),
]


def high_risk_recall(predictor: Callable[[str], str]) -> float:
    """고위험 문의 재현율 — "놓치면 안 되는 것"을 놓치지 않는지."""
    targets = _p1_intents()
    if not HIGH_RISK or not targets:
        return 0.0
    hits = 0
    for _uid, text in HIGH_RISK:
        if predictor(text) in targets:
            hits += 1
    return hits / len(HIGH_RISK)


def _p1_intents() -> set[str]:
    """HIGH_RISK '골드' 판정에 쓸 인텐트 집합 — s1_domain 의 MUST_TOP_PRIORITY."""
    from . import s1_domain as domain
    return set(domain.MUST_TOP_PRIORITY)


def selfcheck() -> None:
    from . import s1_classifier as classifier
    from . import s1_domain as domain

    if not domain.UTTERANCES:
        print("도메인 미채움 → UTTERANCES 를 채우면 지표가 동작합니다.")
        return
    gold_set = [(text, intent) for text, intent, _svc in domain.UTTERANCES.values()]
    acc = intent_accuracy(lambda t: classifier.rule_intent(t), gold_set)
    print(f"① 규칙 인텐트 정확도: {acc:.0%} (골드만)")
    if domain.DIAG_TREES:
        scenarios = []
        for intent, tree in domain.DIAG_TREES.items():
            for node in tree:
                if node.get("resolve_on"):
                    scenarios.append((intent, [node["resolve_on"]]))
        from . import s1_callflow as callflow
        rate = self_resolve_rate(callflow.run_diagnosis, scenarios)
        print(f"② 자가해결률: {rate:.0%}  (진단 트리 {len(scenarios)} 시나리오)")
    if HIGH_RISK:
        recall = high_risk_recall(classifier.rule_intent)
        print(f"③ 고위험 재현율(규칙): {recall:.0%}  ← 변형을 놓치면 계약 위반")
    print("지표 정의 ✅  (재현율 1.0 은 계약 — GUIDE §5 참고)")


if __name__ == "__main__":
    selfcheck()