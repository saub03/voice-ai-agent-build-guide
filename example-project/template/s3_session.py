from __future__ import annotations

# ════════════════════════════════════════════════════════════════════
# [3단계 · 핵심] 신원은 LLM 이 아니라 '서버 세션'이 든다 (병원 01 원칙).
#   - 도구 인자에 identity 가 없다 — 세션(stateful)이 고객을 들고 있다.
#   - load_owned_case : 남의 케이스는 '권한 없음'이 아니라 '없음'으로 —
#                       존재 여부 자체를 숨긴다 (enumeration 방지).
#   - audit() : 성공보다 '실패' 기록이 더 중요하다 (취소 실패 사유를 남긴다).
#   ※ 세션 상태는 DB 커넥션 dir 로 분리돼 있어, 각 reset_session() 이후에 재사용한다.
#     이 모듈은 도메인 데이터와 무관한 '순수 상태·감사 메커니즘'이다.
# ════════════════════════════════════════════════════════════════════
import json

# 세션 상태는 이 모듈 전역으로 둔다. (서버 구현은 이와 별개로 자체 세션 저장소를 가짐)
SESSION = {"verified": False, "customer_id": None, "customer_name": None}
SESSION_META = {"last_slots": [], "did_open": False, "listed_done": False,
                "last_case_id": None, "did_cancel": False}


def reset_session() -> None:
    SESSION.update({"verified": False, "customer_id": None, "customer_name": None})
    SESSION_META.update({"last_slots": [], "did_open": False, "listed_done": False,
                         "last_case_id": None, "did_cancel": False})


def require_verified() -> None:
    if not SESSION["verified"]:
        raise PermissionError("not_verified")


def audit(conn, action: str, ok: bool, customer_id: int | None = None,
          case_id: int | None = None, detail: dict | None = None,
          now=None) -> None:
    """감사 기록. conn: sqlite3.Connection (롤백/커밋 단위를 호출 측이 관리)."""
    from datetime import datetime
    ts = (now or datetime.now()).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO audit_log(action,ok,customer_id,case_id,detail,created_at) VALUES(?,?,?,?,?,?)",
        (action, int(ok), customer_id, case_id,
         json.dumps(detail, ensure_ascii=False) if detail else None, ts))
    conn.commit()


def load_owned_case(conn, case_id: int) -> dict | None:
    """소유권 검증 — 내 것이 아니면 '없음'(None). enumeration 방지.
    conn: sqlite3.Connection (row_factory=Row)". """
    row = conn.execute(
        "SELECT c.*, s.start_time slot_start, s.available slot_avail, sv.name service_name "
        "FROM cases c JOIN slots s ON c.slot_id = s.slot_id "
        "JOIN services sv ON c.service_id = sv.service_id "
        "WHERE c.case_id=?", (case_id,)).fetchone()
    if row is None or row["customer_id"] != SESSION["customer_id"]:
        row = conn.execute(
            "SELECT c.*, NULL slot_start, 1 slot_avail, sv.name service_name "
            "FROM cases c JOIN services sv ON c.service_id = sv.service_id "
            "WHERE c.case_id=?", (case_id,)).fetchone()
        if row is None or row["customer_id"] != SESSION["customer_id"]:
            return None
    return dict(row)


def selfcheck() -> None:
    assert not SESSION["verified"]
    print("세션·감사·소유권 정의 ✅")


if __name__ == "__main__":
    selfcheck()
