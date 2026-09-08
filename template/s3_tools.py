from __future__ import annotations

# ════════════════════════════════════════════════════════════════════
# [3단계] 도구 5종 — 읽기(verify/list_services/조회) + 쓰기(open/cancel) + 목록.
#   - 신원은 LLM 이 아니라 세션이 들고 있다 (도구 인자에 identity 없음).
#   - verify_customer 는 customer_id 를 '반환하지 않는다'(최소 권한) — 세션이 들고 간다.
#   - 쓰기의 주인공 open_case 는 게이트가 겹겹:
#       ① 본인 확인 ② 중복·상한 ③ 슬롯 원자적 점유(BEGIN IMMEDIATE)
#   - LLM 이 볼 도구 '명세서'(TOOLS, OpenAI 형식) + 실제 실행기(방화벽) execute_tool
# 여기 구현은 '전역 SESSION/conn'에 묶이므로 단일 스레드 검증용이다.
#   서버(동시성) 버전은 app/server.py 에서 sess·DB_LOCK 로 다시 배선한다.
# ════════════════════════════════════════════════════════════════════
import json
import uuid
from datetime import datetime

from . import s1_classifier as classifier
from . import s1_domain as domain
from . import s3_db
from . import s3_policy as policy
from . import s3_session as session

# 기본 커넥션 — 호출 전에 s3_db.reset_db_state() 로 만들어 주어야 한다.
_CONN = None


def _conn():
    if _CONN is None:
        raise RuntimeError("DB 미초기화 — s3_tools.bind_db(s3_db.reset_db_state()) 호출 필요")
    return _CONN


def bind_db(conn) -> None:
    """실행 전에 s3_tools.bind_db(conn) 로 커넥션을 연결한다."""
    global _CONN
    _CONN = conn


# ── 읽기 도구 ──
def verify_customer(name: str, birth_date: str) -> dict:
    """① 본인 확인 — 결과가 성공/실패 뿐. customer_id 는 반환하지 않는다 (최소 권한)."""
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM customers WHERE name=? AND birth_date=?",
        (name, birth_date)).fetchall()
    if len(rows) == 1:
        session.SESSION.update({"verified": True, "customer_id": rows[0]["customer_id"],
                                "customer_name": name})
        return {"ok": True, "message": f"{name}님 본인 확인되었습니다."}
    if len(rows) >= 2:
        return {"ok": False, "message": "여러 고객과 일치합니다. 상담원 연결이 필요합니다.",
                "reason": "ambiguous"}
    # ※ 실패 사유를 구분해서 말하지 않는다(enumeration 방지)
    return {"ok": False, "message": "고객 정보가 일치하지 않습니다.", "reason": "no_match"}


def list_services() -> dict:
    conn = _conn()
    return {"services": [r["name"] for r in conn.execute(
        "SELECT name FROM services ORDER BY service_id")]}


def get_available_slots(service: str, date: str) -> dict:
    """예약 가능 슬롯 조회 (JOIN 조회 패턴)."""
    conn = _conn()
    s = conn.execute("SELECT * FROM services WHERE name=?", (service,)).fetchone()
    if s is None:
        return {"ok": False, "message": f"{service} 서비스를 찾을 수 없습니다.", "reason": "no_service"}
    if s["kind"] != "booking":
        return {"ok": False, "message": f"{service}은(는) 예약 서비스가 아닙니다.", "reason": "not_booking"}
    rows = conn.execute(
        "SELECT slot_id, substr(start_time,12) AS time, available "
        "FROM slots WHERE service_id=? AND start_time LIKE ? AND available=1 ORDER BY start_time",
        (s["service_id"], date + "%")).fetchall()
    if not rows:
        return {"ok": False, "message": "예약 가능한 시간이 없습니다.", "reason": "no_slots"}
    session.SESSION_META["last_slots"] = [dict(r) for r in rows]
    return {"ok": True, "slots": [dict(r) for r in rows]}


# ── 쓰기 도구 ──
def open_case(service: str, summary: str, slot_id: int | None = None,
              now: datetime | None = None) -> dict:
    """게이트 ①본인확인 ②중복·상한 ③슬롯 재확인·원자적 점유."""
    conn = _conn()
    now = now or datetime.now()
    try:
        session.require_verified()
    except PermissionError:
        return {"ok": False, "message": "본인 확인이 먼저 필요합니다.", "reason": "not_verified"}
    s = conn.execute("SELECT * FROM services WHERE name=?", (service,)).fetchone()
    if s is None:
        return {"ok": False, "message": f"{service} 서비스를 찾을 수 없습니다.", "reason": "no_service"}
    open_cnt = conn.execute(
        "SELECT COUNT(*) c FROM cases WHERE customer_id=? AND status IN ('opened','in_progress')",
        (session.SESSION["customer_id"],)).fetchone()["c"]
    dup = conn.execute(
        "SELECT COUNT(*) c FROM cases WHERE customer_id=? AND service_id=? AND status IN ('opened','in_progress')",
        (session.SESSION["customer_id"], s["service_id"])).fetchone()["c"]
    ok, why = policy.policy_can_open(open_cnt, dup)
    if not ok:
        return {"ok": False, "message": "진행 중인 케이스가 있어 새로 만들 수 없습니다.", "reason": why}
    pri = classifier.rule_classify(summary)
    priority = pri["priority"] if pri["intent"] not in ("unknown", "unknown_not_filled") else domain.P_ORDER[-1]
    conn.execute("BEGIN IMMEDIATE")          # 조회와 수정 사이의 틈을 막는다
    try:
        if s["kind"] == "booking":
            if not slot_id:
                conn.rollback()
                return {"ok": False, "message": "예약할 시간대를 선택해 주세요.", "reason": "need_slot"}
            sl = conn.execute("SELECT * FROM slots WHERE slot_id=? AND service_id=?",
                              (slot_id, s["service_id"])).fetchone()
            if sl is None:
                conn.rollback()
                return {"ok": False, "message": "잘못된 슬롯입니다.", "reason": "no_slot"}
            if not sl["available"]:
                conn.rollback()
                return {"ok": False, "message": "그 시간은 이미 예약되었습니다.", "reason": "already_taken"}
            if s3_db.slot_dt(sl["start_time"]) < now:
                conn.rollback()
                return {"ok": False, "message": "지난 일정은 예약할 수 없습니다.", "reason": "past_slot"}
            conn.execute("UPDATE slots SET available=0 WHERE slot_id=?", (slot_id,))
        else:
            slot_id = None
        c = conn.execute(
            "INSERT INTO cases(customer_id,service_id,slot_id,status,priority,summary,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (session.SESSION["customer_id"], s["service_id"], slot_id, "opened", priority, summary,
             now.isoformat(timespec="seconds")))
        conn.commit()
        session.audit(conn, "open_case", True, customer_id=session.SESSION["customer_id"],
                      case_id=c.lastrowid, detail={"service": service})
        session.SESSION_META["did_open"] = True
        session.SESSION_META["last_case_id"] = c.lastrowid
        return {"ok": True, "case_id": c.lastrowid, "status": "opened", "priority": priority,
                "message": f"{service} 접수가 완료되었습니다. (케이스 #{c.lastrowid})"}
    except Exception:
        conn.rollback()
        raise


def list_cases() -> dict:
    """내 케이스 목록 — '누구의 것인가'는 세션에서 나온다."""
    conn = _conn()
    rows = conn.execute(
        "SELECT c.case_id, s.name service, c.status, c.priority, c.summary "
        "FROM cases c JOIN services s ON c.service_id=s.service_id "
        "WHERE c.customer_id=? ORDER BY c.case_id DESC", (session.SESSION["customer_id"],)).fetchall()
    session.SESSION_META["listed_done"] = True
    return {"cases": [dict(r) for r in rows]}


def cancel_case(case_id: int, now: datetime | None = None) -> dict:
    """①소유권 ②상태 전이표 ③취소 마감 정책 ④슬롯 반납 — 전부 한 트랜잭션."""
    conn = _conn()
    now = now or datetime.now()
    try:
        session.require_verified()
    except PermissionError:
        return {"ok": False, "message": "본인 확인이 먼저 필요합니다.", "reason": "not_verified"}
    case = session.load_owned_case(conn, case_id)
    if case is None:
        session.audit(conn, "cancel_case", False, customer_id=session.SESSION["customer_id"],
                      case_id=case_id, detail={"reason": "not_found"})
        return {"ok": False, "message": "해당 예약을 찾을 수 없습니다.", "reason": "not_found"}
    ok, why = policy.policy_can_cancel(case, now, slot_dt_fn=s3_db.slot_dt)
    if not ok:
        session.audit(conn, "cancel_case", False, customer_id=session.SESSION["customer_id"],
                      case_id=case_id, detail={"reason": why})
        return {"ok": False, "message": "지금은 취소할 수 없습니다.", "reason": why}
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("UPDATE cases SET status='cancelled' WHERE case_id=?", (case_id,))
        if case["slot_id"]:
            conn.execute("UPDATE slots SET available=1 WHERE slot_id=?", (case["slot_id"],))
        conn.commit()
        session.audit(conn, "cancel_case", True, customer_id=session.SESSION["customer_id"],
                      case_id=case_id, detail={"released_slot": case["slot_id"]})
        session.SESSION_META["did_cancel"] = True
        return {"ok": True, "case_id": case_id, "status": "cancelled", "message": "취소되었습니다."}
    except Exception:
        conn.rollback()
        raise


WRITE_TOOLS = {"open_case", "cancel_case"}   # 실행기에서 이중 방어


# ── 도구 명세서(OpenAI 형식) — LLM 이 볼 스키마 ──
def _fn(name: str, desc: str, props: dict, required: list) -> dict:
    return {"type": "function",
            "function": {"name": name, "description": desc,
                         "parameters": {"type": "object",
                                        "properties": props,
                                        "required": required,
                                        "additionalProperties": False}}}


def build_tools() -> list[dict]:
    """TOOLS 명세서 — 도메인 SEED_SERVICES 는 상세에 넣지 않는다(실행기가 가져옴)."""
    return [
        _fn("verify_customer", "본인 확인. 성함과 생년월일로 고객을 확인한다.",
            {"name": {"type": "string"}, "birth_date": {"type": "string", "description": "YYYY-MM-DD 형식"}},
            ["name", "birth_date"]),
        _fn("list_services", "상담 가능한 서비스 목록을 보여준다.", {}, []),
        _fn("get_available_slots", "특정 서비스·날짜의 예약 가능 시간 조회. 날짜는 YYYY-MM-DD.",
            {"service": {"type": "string"}, "date": {"type": "string", "description": "YYYY-MM-DD"}},
            ["service", "date"]),
        _fn("open_case", "사용자가 요청을 명시적으로 확인한 뒤에만 호출. 접수를 생성한다.",
            {"service": {"type": "string"}, "summary": {"type": "string"},
             "slot_id": {"type": ["integer", "null"], "description": "예약 서비스일 때 선택 slot"}},
            ["service", "summary"]),
        _fn("list_cases", "세션 고객의 케이스 목록 조회.", {}, []),
        _fn("cancel_case", "케이스/예약 취소.",
            {"case_id": {"type": "integer"}, "now": {"type": ["string", "null"]}}, ["case_id"]),
    ]


TOOL_IMPL = {
    "verify_customer": verify_customer,
    "list_services": list_services,
    "get_available_slots": get_available_slots,
    "open_case": open_case,
    "list_cases": list_cases,
    "cancel_case": cancel_case,
}


def _tc(name: str, args: dict, call_id: str | None = None) -> dict:
    return {"id": call_id or f"call_{uuid.uuid4().hex[:8]}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


def execute_tool(name: str, arguments: dict) -> dict:
    """방화벽: 목록에 없으면 오류. 어떤 예외든 LLM 이 읽고 재시도할 수 있게 결과로 돌려준다."""
    if name not in TOOL_IMPL:
        return {"error": "unknown_tool", "message": f"정의되지 않은 도구: {name}"}
    if name in WRITE_TOOLS and not session.SESSION["verified"]:
        return {"error": "not_verified", "message": "본인 확인이 먼저 필요합니다."}
    try:
        return TOOL_IMPL[name](**arguments)
    except (TypeError, ValueError) as e:
        return {"error": "bad_arguments", "message": f"{type(e).__name__}: {e}"}


def schema_integrity() -> None:
    """도구 스키마 자체 검증 — required ⊆ properties, additionalProperties=False."""
    for t in build_tools():
        p = t["function"]["parameters"]
        assert set(p["required"]) <= set(p["properties"]), t
        assert p["additionalProperties"] is False


if __name__ == "__main__":
    schema_integrity()
    print("도구 스키마·실행기 정의 ✅")
