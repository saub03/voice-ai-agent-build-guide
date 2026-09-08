from __future__ import annotations

# ════════════════════════════════════════════════════════════════════
# [2단계] 3중 관문 — '믿지 말고 검증'이 이 템플릿의 LLM 사용법이다.
#   triple_gate(draft_text, original_text):
#     ① 형식  : JSON 파싱 (울타리 ```json``` / 주변 설명 제거)
#     ② 스키마: jsonschema — 필드·타입·enum·추가필드 금지 (TICKET_SCHEMA)
#     ③ 논리  : 우선순위는 설계 고정값(BASE_PRIORITY)보다 '느슨해질 수 없다'(내릴 수 없음)
#               + 최상위 인텐트는 P1+escalate 강제 + escalate=true 는 최상위여야 함
#   - 통과를 봐서는 검증이 아니다. "막아야 할 것을 막는지"가 핵심(결함 주입 실험).
#   → 스키마·enum·우선순위 기준은 s1_domain 에서 온다.
# ════════════════════════════════════════════════════════════════════
import json
import re

import jsonschema

from . import s1_domain as domain


def build_schema() -> dict:
    """TICKET_SCHEMA 동적 생성 — enum 은 도메인 인텐트/우선순위에서 온다."""
    return {
        "type": "object",
        "properties": {
            "intent":   {"type": "string", "enum": domain.intent_names()},
            "priority": {"type": "string", "enum": domain.P_ORDER},
            "summary":  {"type": "string"},
            "escalate": {"type": "boolean"},
        },
        "required": ["intent", "priority", "summary", "escalate"],
        "additionalProperties": False,
    }


class GateError(Exception):
    def __init__(self, stage: str, msg: str) -> None:
        self.stage = stage
        super().__init__(f"[{stage} 관문 실패] {msg}")


def _to_json_fenced(text: str) -> str:
    """울타리(```json···```)나 주변 설명을 걸러내고 순수 JSON 조각만 추출."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise GateError("format", "JSON 객체가 없습니다")
    return m.group(0)


def triple_gate(draft_text: str, original_text: str) -> dict:
    """형식 → 스키마 → 논리 3중 관문. 통과하면 dict 를, 실패하면 GateError 를 낸다."""
    schema = build_schema()
    try:
        obj = json.loads(_to_json_fenced(draft_text))          # ① 형식
    except (json.JSONDecodeError, GateError) as e:
        raise GateError("format", str(e)[:140]) from e
    if not isinstance(obj, dict):
        raise GateError("format", "JSON 객체(딕셔너리)가 아님")
    try:
        jsonschema.validate(instance=obj, schema=schema)        # ② 스키마
    except jsonschema.ValidationError as e:
        raise GateError("schema", str(e)[:160])

    # ③ 논리 — 우선순위는 설계 고정값보다 '느슨해질 수 없다'(내릴 수 없다)
    if not domain.BASE_PRIORITY:
        raise GateError("logic", "도메인(BASE_PRIORITY) 미채움")
    top = domain.P_ORDER[0]
    if obj["intent"] in domain.MUST_TOP_PRIORITY and (obj["priority"] != top or obj["escalate"] is not True):
        raise GateError("logic", f"최상위 인텐트({domain.MUST_TOP_PRIORITY})는 {top}+escalate 여야 함")
    if obj["escalate"] is True and obj["priority"] != top:
        raise GateError("logic", "escalate=true 인데 최상위가 아님")
    base_pri = domain.BASE_PRIORITY.get(obj["intent"])
    if base_pri is not None and domain.P_ORDER.index(obj["priority"]) > domain.P_ORDER.index(base_pri):
        raise GateError("logic", f"우선순위 계약 위반: {obj['priority']} 는 {base_pri} 보다 느슨할 수 없음")
    return obj


def fault_injection_check() -> tuple[list[str], list[str]]:
    """결함 주입 자가 검증 — 막아야 할 것을 막는지. (passed, blocked) 문자열 목록 반환.
    도메인 무관 실험: 형식 깨짐/필드 누락/논리 위반(최상위·우선순위·escalate) 은 전부 🛑."""
    if not domain.BASE_PRIORITY:
        return [], []
    neutral = domain.intent_names()[0] if domain.intent_names() else "x"
    base = domain.BASE_PRIORITY.get(neutral, domain.P_ORDER[-1])
    top_intent = next(iter(domain.MUST_TOP_PRIORITY), None)
    # '우선순위 하락' 실험이 의미 있으려면 기준이 최하위보다 높은 인텐트가 필요하다
    downgrade_intent = next((i for i, p in domain.BASE_PRIORITY.items()
                             if domain.P_ORDER.index(p) > 0), None)
    downgrade_base = domain.BASE_PRIORITY.get(downgrade_intent, domain.P_ORDER[-1]) \
        if downgrade_intent else None
    trials = [
        ("정상", json.dumps({"intent": neutral, "priority": base, "summary": "x", "escalate": False}), True),
        ("형식 깨짐", "{intent: x}", False),
        ("필드 누락", json.dumps({"intent": neutral, "priority": base, "summary": "x"}), False),
    ]
    if top_intent:
        trials.append(("논리(최상위 강제 저하)",
                       json.dumps({"intent": top_intent, "priority": domain.P_ORDER[-1], "summary": "x", "escalate": False}),
                       False))
    if downgrade_intent and downgrade_base:
        trials.append(("논리(우선순위 하락)",
                       json.dumps({"intent": downgrade_intent, "priority": domain.P_ORDER[-1],
                                   "summary": "x", "escalate": False}),
                       False))
    trials.append(("논리(escalate=true 인데 비최상위)",
                   json.dumps({"intent": neutral, "priority": base, "summary": "x", "escalate": True}),
                   False))
    passed: list[str] = []
    blocked: list[str] = []
    for label, draft, should_pass in trials:
        try:
            triple_gate(draft, "x")
        except GateError:
            blocked.append(label)
            continue
        passed.append(label)
    return passed, blocked


if __name__ == "__main__":
    passed, blocked = fault_injection_check()
    print("결함 주입:", "통과", passed, "/ 차단", blocked)
