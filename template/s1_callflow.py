from __future__ import annotations

# ════════════════════════════════════════════════════════════════════
# [1단계] 대화는 자유 대화가 아니라 '정해진 여정'(상태 기계) 위의 대화다.
#   - CALL_FLOW : 상태마다 "무엇을 묻는지/응답을 어디에 기록하는지"를 표로.
#                 상태 전이는 앞으로만(next_flow_state).
#   - DIAG_TREES : 티켓 전에 자가해결을 시도하는 진단 질문 트리.
#     · resolve_on 이 '네' 같은 응답이면 그 자리에서 resolved
#     · NO_DIAG_INTENTS 는 진단 금지 — 봇이 셀프 조치를 안내하면 안 되고 즉시 접수
#   도메인(상태·진단·문구)은 s1_domain 에서 가져온다. 여기는 메커니즘만 있다.
# ════════════════════════════════════════════════════════════════════
from . import s1_domain as domain


def flow_states() -> list[str]:
    """CALL_FLOW 에서 state 이름 순서만 추출."""
    return [s["state"] for s in domain.CALL_FLOW]


def next_flow_state(current: str) -> str | None:
    """현재 상태 → 다음 상태 (여정은 앞으로만). None 이면 여정 끝."""
    order = flow_states()
    if current not in order:
        raise ValueError(f"모르는 상태: {current}")
    i = order.index(current)
    return order[i + 1] if i + 1 < len(order) else None


def assert_flow_chain(states: list[str]) -> bool:
    """주어진 상태들이 CALL_FLOW 에 허용된 순서대로인지 검증 (회귀에 씀)."""
    cur = states[0]
    for nxt in states[1:]:
        assert next_flow_state(cur) == nxt, f"{cur} → {nxt} 아닙니다 (허용: {next_flow_state(cur)})"
        cur = nxt
    return True


def run_diagnosis(intent: str, answers: list[str]) -> tuple[bool, dict]:
    """진단 질문 트리 실행. answers: '네'/'아니요' 형태의 응답 리스트.
    반환 (resolved, record):
      - 진단 금지 인텐트 → (False, {"phase": "ticket", "reason": "no_diag"})
      - 질문이 더 필요 → (False, {"phase": "ask_more", "question", "step", "groups"})
      - resolve_on 매칭   → (True,  {"phase": "resolved", "action", ...})
      - 트리 소진         → (False, {"phase": "ticket", "reason": "tree_exhausted"})
    """
    if intent in domain.NO_DIAG_INTENTS:
        return False, {"phase": "ticket", "reason": "no_diag_intent"}
    tree = domain.DIAG_TREES.get(intent, [])
    for i, node in enumerate(tree):
        if i >= len(answers):
            return False, {"phase": "ask_more", "question": node["q"],
                           "step": i, "groups": [node.get("resolve_on") and node["resolve_on"].strip()]}
        ans = answers[i]
        if node.get("resolve_on") and ans == node["resolve_on"]:
            return True, {"phase": "resolved", "step": i, "action": node.get("do"), "self_resolved": True}
        return False, {"phase": "_next", "do": node.get("do"), "step": i}
    return True, {"phase": "ticket", "reason": "tree_exhausted", "self_resolved": False}


def selfcheck() -> None:
    if not domain.CALL_FLOW:
        print("도메인 미채움 → CALL_FLOW 를 채우면 상태 기계가 동작합니다.")
        return
    order = flow_states()
    assert order == [s["state"] for s in domain.CALL_FLOW]
    assert_flow_chain(order)  # 전체가 순방향 체인으로 이어져야 함 (회귀)
    print("콜 흐름 상태 기계 ✅  여정:", " → ".join(order))


if __name__ == "__main__":
    selfcheck()
