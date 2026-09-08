from __future__ import annotations

# ════════════════════════════════════════════════════════════════════
# [1단계] 규칙 기반 인텐트 분류 + 우선순위 정책.
#   - rule_intent   : 키워드 규칙 (보안부터 순서대로 — 겹쳐도 보안으로 기운다)
#   - rule_classify : {intent, priority, method}
#   - classify_with_priority : 우선순위 정책 — 긴급 신호는 '한 단계만' 올릴 수 있고,
#                             절대 내리지 않는다. (운영 정책 = 설계 고정값)
# 도메인(키워드표·기준 우선순위·긴급 신호)은 s1_domain 에서 가져온다.
#   ※ classify_with_priority / rule_intent 는 s3_regression_gates 의 근거가 되므로
#     도메인을 채우기 전에는 "미채움 안내"를 반환한다.
# ════════════════════════════════════════════════════════════════════
from . import s1_domain as domain


def rule_intent(text: str) -> str:
    """키워드 규칙 — domain.INTENT_RULES 를 키 순서대로 검사. 없으면 'unknown'."""
    if not domain.INTENT_RULES:
        return "unknown_not_filled"  # 도메인 미채움 신호
    for intent in domain.INTENT_RULES:  # dict 순서 = 검사 순서 (먼저 검사할 걸 앞에)
        if any(k in text for k in domain.INTENT_RULES[intent]):
            return intent
    return "unknown"


def classify_with_priority(text: str, intent: str) -> str:
    """intent 의 기준 우선순위에서, 긴급 신호면 한 단계만 승격. (내리기 금지)
    security/최상위 인텐트는 승격할 이유가 없으므로 그대로 최상위가 된다."""
    if not domain.BASE_PRIORITY:
        return "?"
    base = domain.BASE_PRIORITY[intent]
    if intent in domain.MUST_TOP_PRIORITY:
        return domain.P_ORDER[0]
    if any(m in text for m in domain.URGENT_MARKERS):
        i = domain.P_ORDER.index(base)
        if i > 0:
            base = domain.P_ORDER[i - 1]
    return base


def rule_classify(text: str) -> dict:
    """규칙 분류 + 우선순위 → {intent, priority, method}."""
    intent = rule_intent(text)
    if intent in ("unknown", "unknown_not_filled"):
        return {"intent": intent, "priority": "?", "method": "rule"}
    return {"intent": intent, "priority": classify_with_priority(text, intent), "method": "rule"}


def urgent_or_base(text: str, intent: str) -> str:
    """우선순위 규칙 단축 — 우선순위를 계산만 한다 (s3_tools 등에서 시드/기본값 용)."""
    return classify_with_priority(text, intent)


def selfcheck() -> None:
    """메커니즘(우선순위 '승격은 한 단계, 내리기 금지')이 깨지지 않았는지 (도메인 무관)."""
    if not domain.BASE_PRIORITY:
        print("도메인 미채움 → s1_domain.py 를 채우면 규칙 분류가 동작합니다.")
        return
    # P_ORDER 위에서: 어떤 인텐트든 긴급 신호가 '추가 승격'으로 우선순위가 내려가진 않는다
    for intent, pri in domain.BASE_PRIORITY.items():
        promoted = classify_with_priority("급해요", intent)
        if domain.P_ORDER.index(promoted) > domain.P_ORDER.index(pri):
            raise AssertionError(f"{intent}: 긴급 승격이 우선순위를 내렸다 ({pri}→{promoted})")
    print(f"우선순위 정책 검증 ✅ (인텐트 {len(domain.BASE_PRIORITY)}종)")


if __name__ == "__main__":
    selfcheck()
