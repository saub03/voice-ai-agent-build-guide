from __future__ import annotations

# ════════════════════════════════════════════════════════════════════
# [3단계] 'if 남발' 대신 '표'로 상태 규칙을 강제한다.
#   - CASE_TRANSITIONS : 존재하는 전이만 허용 — 불가능한 이동이 애초에 없다.
#   - policy_can_cancel : 취소 마감 정책 (booking 의 시작 전 일정 시점까지)
#   - policy_can_open   : 중복·상한 정책
#   - 정책 함수는 DB 를 만지지 않고 판단만 한다(순수 함수) + now 를 주입받아 시계 테스트 가능.
#   ※ 마감 시간/활성 상한은 [도메인] 운영 정책(설계 고정값)이다.
# ════════════════════════════════════════════════════════════════════
from datetime import datetime, timedelta


# [도메인] 상태 전이표 — 가능한 상태 전이만 (필요하면 도메인에서 추가)
CASE_TRANSITIONS: dict[str, set[str]] = {
    "opened":      {"in_progress", "resolved", "cancelled"},
    "in_progress": {"resolved", "cancelled"},
    "resolved":    set(),   # 종착 상태
    "cancelled":   set(),   # 종착 상태
}
TERMINAL_STATES = {"resolved", "cancelled"}

# [도메인] 운영 정책 상수 (도메인에 맞게 조정)
CANCEL_DEADLINE_HOURS = 24   # booking 예약은 '시작 24시간 전'까지 취소 가능
MAX_OPEN_PER_CUSTOMER = 3    # 동시 활성 케이스 상한


def can_transition(status: str, to_status: str) -> bool:
    return to_status in CASE_TRANSITIONS.get(status, set())


def policy_can_cancel(case: dict, now: datetime,
                      slot_dt_fn=None) -> tuple[bool, str | None]:
    """(허용 여부, 사유). now 를 주입받아야 테스트가 된다.
    slot_dt_fn: 'YYYY-MM-DD HH:MM' → datetime (s3_db.slot_dt 를 넘겨받거나 주입).
    case 는 slot_start 를 가진 dict 로 가정한다."""
    if case.get("status") in TERMINAL_STATES:
        return False, "not_active"
    if case.get("slot_id") and case.get("slot_start") and slot_dt_fn:
        start = slot_dt_fn(case["slot_start"])
        if start - now < timedelta(hours=CANCEL_DEADLINE_HOURS):
            return False, "past_deadline"
    return True, None


def policy_can_open(open_cnt: int, dup_cnt: int) -> tuple[bool, str | None]:
    """판단만 한다(순수 함수) — 호출 측이 카운트를 계산해 건네준다.
    (active 케이스 수, 같은 서비스 열린 케이스 수) → (허용, 사유)"""
    if open_cnt >= MAX_OPEN_PER_CUSTOMER:
        return False, "too_many_active"
    if dup_cnt:
        return False, "already_open_service"
    return True, None


def selfcheck() -> None:
    assert can_transition("opened", "cancelled") and not can_transition("cancelled", "opened")
    assert can_transition("in_progress", "resolved")
    print("상태 전이표·정책 함수 ✅  (CANCEL_DEADLINE_HOURS =", CANCEL_DEADLINE_HOURS, ")")


if __name__ == "__main__":
    selfcheck()
