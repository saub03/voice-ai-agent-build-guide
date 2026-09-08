from __future__ import annotations

# ════════════════════════════════════════════════════════════════════
# [3단계 · 회귀] 회귀 게이트 — "프롬프트가 아니라 코드가" 도메인 규칙·DB 규칙을
#   지키는지 검사한다. 도메인(SEED_*)을 채우면 다음 게이트들이 실제로 돈다:
#
#   G1 본인확인 없이 쓰기 차단 (not_verified)
#   G2 정상 흐름 — 확인→조회→생성 (booking)
#   G3 이중 슬롯 경합 차단 (already_taken)
#   G4 남의 예약 불가 — '권한 없음'이 아니라 '없음' (no_found)
#   G5 취소 시 슬롯 반납 (available 1)
#   G6 24시간 취소 마감 + 중복 취소 (past_deadline / not_active)
#   G7 실패 감사 기록
#   G8 활성 케이스 상한 (too_many_active)
#   G9 나쁜 인자에도 생존 (unknown_tool / bad_arguments)
#   G10 동명이인 특정 (이름+생년월일 = 신원), 겹칠 땐 ambiguous
#
#   ★ 도메인 무관 부품(전이표·정책·원자성·소유권)이 깨지지 않았는지를
#     "채운 도메인 위에서" 재확인하는 것이 회귀 게이트의 목적이다.
# ════════════════════════════════════════════════════════════════════
from datetime import datetime, timedelta

from . import s1_domain as domain
from . import s3_db
from . import s3_policy as policy
from . import s3_session as session
from . import s3_tools as tools


class _Ctx:
    """게이트마다 DB·세션을 초기화해 독립적으로 돌게 하는 헬퍼."""

    def __init__(self) -> None:
        self.conn = None

    def reset(self) -> None:
        self.conn = s3_db.reset_db_state()
        tools.bind_db(self.conn)
        session.reset_session()


def _slot_id(ctx: _Ctx, service: str, offset_days: int, index: int = 0) -> dict:
    d = (datetime.now().date() + timedelta(days=offset_days)).isoformat()
    slots = tools.get_available_slots(service, d)
    assert slots["ok"], slots
    return slots["slots"][index]


def _booking_service() -> str | None:
    return next((s[0] for s in domain.SEED_SERVICES if s[1] == "booking"), None)


def _ticket_services() -> list[str]:
    return [s[0] for s in domain.SEED_SERVICES if s[1] == "ticket"]


def _a_customer(ctx: _Ctx) -> None:
    """시드 첫 고객으로 인증. 시드에 고객이 없으면 게이트 실패로 처리."""
    if not domain.SEED_CUSTOMERS:
        raise AssertionError("SEED_CUSTOMERS 미채움")
    name, birth = domain.SEED_CUSTOMERS[0]
    assert tools.verify_customer(name, birth)["ok"]


def _b_customer(ctx: _Ctx) -> None:
    name, birth = domain.SEED_CUSTOMERS[-1]
    assert tools.verify_customer(name, birth)["ok"]


def run_gates() -> dict[str, bool]:
    """도메인 위에서 회귀 게이트를 실행해 {이름: 통과여부} 반환."""
    results: dict[str, bool] = {}
    ctx = _Ctx()
    booking = _booking_service()

    # G1: 미인증 쓰기 차단
    ctx.reset()
    try:
        r = tools.execute_tool("open_case", {"service": booking or "x", "summary": "예약", "slot_id": 1})
        results["G1_미인증 쓰기 차단"] = r.get("error") == "not_verified"
    except Exception:
        results["G1_미인증 쓰기 차단"] = False

    # G2: 정상 흐름 (booking)
    ctx.reset()
    try:
        if booking:
            _a_customer(ctx)
            s = _slot_id(ctx, booking, 1)
            r = tools.open_case(booking, "내일 10시", s["slot_id"])
            results["G2_정상 흐름(booking)"] = bool(r["ok"])
        else:
            results["G2_정상 흐름(booking)"] = False
    except Exception:
        results["G2_정상 흐름(booking)"] = False

    # G3: 이중 슬롯 경합
    ctx.reset()
    try:
        if booking and len(domain.SEED_CUSTOMERS) >= 2:
            _a_customer(ctx)
            s = _slot_id(ctx, booking, 2)
            assert tools.open_case(booking, "A", s["slot_id"])["ok"]
            session.reset_session()
            _b_customer(ctx)
            results["G3_이중 슬롯 차단"] = \
                tools.open_case(booking, "B", s["slot_id"]).get("reason") == "already_taken"
        else:
            results["G3_이중 슬롯 차단"] = False
    except Exception:
        results["G3_이중 슬롯 차단"] = False

    # G4: 남의 예약은 '없음'
    ctx.reset()
    try:
        if booking and len(domain.SEED_CUSTOMERS) >= 2:
            _a_customer(ctx)
            s = _slot_id(ctx, booking, 1)
            cid = tools.open_case(booking, "A", s["slot_id"])["case_id"]
            session.reset_session()
            _b_customer(ctx)
            results["G4_남의 예약 불가"] = tools.cancel_case(cid).get("reason") == "not_found"
        else:
            results["G4_남의 예약 불가"] = False
    except Exception:
        results["G4_남의 예약 불가"] = False

    # G5: 취소 시 슬롯 반납
    ctx.reset()
    try:
        if booking:
            _a_customer(ctx)
            s = _slot_id(ctx, booking, 2)
            cid = tools.open_case(booking, "A", s["slot_id"])["case_id"]
            before = ctx.conn.execute("SELECT available FROM slots WHERE slot_id=?",
                                      (s["slot_id"],)).fetchone()["available"]
            assert before == 0
            start = s3_db.slot_dt(ctx.conn.execute(
                "SELECT start_time FROM slots WHERE slot_id=?",
                (s["slot_id"],)).fetchone()["start_time"])
            assert tools.cancel_case(cid, now=start - timedelta(hours=30))["ok"]
            a = ctx.conn.execute("SELECT available FROM slots WHERE slot_id=?",
                                 (s["slot_id"],)).fetchone()["available"]
            results["G5_슬롯 반납"] = a == 1
        else:
            results["G5_슬롯 반납"] = False
    except Exception:
        results["G5_슬롯 반납"] = False

    # G6: 24시간 마감 + 중복 취소
    ctx.reset()
    try:
        if booking:
            _a_customer(ctx)
            s = _slot_id(ctx, booking, 1)
            start = s3_db.slot_dt(ctx.conn.execute(
                "SELECT start_time FROM slots WHERE slot_id=?",
                (s["slot_id"],)).fetchone()["start_time"])
            late = start - timedelta(hours=1)
            cid = tools.open_case(booking, "A", s["slot_id"], now=late - timedelta(hours=1))["case_id"]
            assert tools.cancel_case(cid, now=late).get("reason") == "past_deadline"
            ctx.conn.execute("UPDATE cases SET status='opened' WHERE case_id=?", (cid,))
            ctx.conn.execute("UPDATE slots SET available=0 WHERE slot_id=?", (s["slot_id"],))
            ctx.conn.commit()
            assert tools.cancel_case(cid, now=start - timedelta(hours=30))["ok"]
            results["G6_마감·중복취소"] = tools.cancel_case(
                cid, now=start - timedelta(hours=30)).get("reason") == "not_active"
        else:
            results["G6_마감·중복취소"] = False
    except Exception:
        results["G6_마감·중복취소"] = False

    # G7: 실패 감사 기록
    ctx.reset()
    try:
        _a_customer(ctx)
        tools.cancel_case(99999)
        n = ctx.conn.execute("SELECT COUNT(*) c FROM audit_log WHERE action='cancel_case' AND ok=0").fetchone()["c"]
        results["G7_실패 감사"] = n >= 1
    except Exception:
        results["G7_실패 감사"] = False

    # G8: 활성 케이스 상한 — ticket 서비스로 상한까지 채운 뒤 차단
    ctx.reset()
    try:
        _a_customer(ctx)
        tickets = _ticket_services()
        if len(tickets) >= policy.MAX_OPEN_PER_CUSTOMER + 1:
            for svc in tickets[:policy.MAX_OPEN_PER_CUSTOMER]:
                assert tools.open_case(svc, "x")["ok"]
            results["G8_활성 상한"] = tools.open_case(tickets[-1], "x").get("reason") == "too_many_active"
        else:
            results["G8_활성 상한"] = False
    except Exception:
        results["G8_활성 상한"] = False

    # G9: 나쁜 인자에도 생존
    ctx.reset()
    try:
        unk = tools.execute_tool("create_nothing", {})["error"] == "unknown_tool"
        bad = tools.execute_tool("open_case", {"patient_id": 1})
        bad_ok = bad.get("error") in ("bad_arguments", "not_verified") or "open_case" in str(bad.get("message"))
        results["G9_나쁜 인자 생존"] = unk and bad_ok
    except Exception:
        results["G9_나쁜 인자 생존"] = False

    # G10: 동명이인 특정 / 미인증 불일치
    ctx.reset()
    try:
        if len(domain.SEED_CUSTOMERS) >= 2:
            name = domain.SEED_CUSTOMERS[0][0]
            first_bd = domain.SEED_CUSTOMERS[0][1]
            second_bd = domain.SEED_CUSTOMERS[1][1]
            dup_name = name == domain.SEED_CUSTOMERS[1][0]
            # 존재하지 않는 생년월일 → no_match (실패 사유 구분 금지 확인)
            bad = tools.verify_customer(name, "1900-01-01").get("reason") == "no_match"
            if dup_name and first_bd != second_bd:
                tools.verify_customer(name, first_bd)
                first_id = session.SESSION["customer_id"]
                tools.verify_customer(name, second_bd)
                second_id = session.SESSION["customer_id"]
                ok_first = tools.verify_customer(name, first_bd)["ok"]
                tools.verify_customer(name, second_bd)
                ok_second = bool(session.SESSION["verified"])
                results["G10_동명이인 특정"] = ok_first and ok_second and bad and (first_id != second_id)
            else:
                results["G10_동명이인 특정"] = bad
        else:
            results["G10_동명이인 특정"] = False
    except Exception:
        results["G10_동명이인 특정"] = False

    return results


def all_pass(results: dict[str, bool]) -> bool:
    failed = [k for k, v in results.items() if not v]
    if failed:
        print("회귀 게이트 실패:", failed)
    else:
        print(f"회귀 게이트 전부 통과 ✅ ({len(results)}종)")
    return not failed


if __name__ == "__main__":
    all_pass(run_gates())
