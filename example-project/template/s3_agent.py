from __future__ import annotations

# ════════════════════════════════════════════════════════════════════
# [3단계] 에이전트 루프 — LLM 이 '말 or 도구호출' 을 고르는 구조.
#   - chat() : 사용자 텍스트 → 왕복 루프(도구 실행까지) → 최종 응답 문자열.
#     MAX_TOOL_TURNS 가 무한 왕복을 막는다 ('되겠지'가 아니라 정해진 한도로 소리 내며 중단).
#   - MockAgentLLM : 행동 시뮬레이터 — 실제 LLM 대신 '확인→조회→확정→생성→나열→취소'
#     규칙을 최소로 시뮬레이션한다 (병원 03 'MOCK = 행동 시뮬레이터' 철학).
#     관대한 모의가 뒤 단계를 거짓 통과시키지 않도록 '정직'해야 한다.
# ★ 도메인 의존 지점:
#   - 역할·서비스명 / 상대 날짜 문구('내일'·'모레') / 확정 문구('해주세요') / 취소·조회 문구
#     는 도메인에 따라 바꿔야 한다. 아래 MockAgentLLM 은 '기사 방문 booking' 기준 예시이며
#     [도메인] 마커를 따라 로직을 갈아끼우면 된다.
# ════════════════════════════════════════════════════════════════════
import json
import re
from datetime import date, timedelta

from . import s1_domain as domain
from . import s3_session as session
from . import s3_tools as tools

MAX_TOOL_TURNS = 6

# [도메인] booking 서비스명 — s3_tools 는 실행기로 쓴다. 여기서는 시뮬레이터가 가정한다.
BOOKING_SERVICE = "기사 방문"   # ← SEED_SERVICES 의 booking 이름으로 맞출 것


def _rel_date(text: str, offsets: dict[str, int]) -> str | None:
    """상대 날짜 문구 → YYYY-MM-DD. offsets: {"모레":2, "내일":1, "다음":1, ...}"""
    for kw, off in offsets.items():
        if kw in text:
            return (date.today() + timedelta(days=off)).isoformat()
    return None


def _confirm(text: str, confirm_words: tuple[str, ...]) -> bool:
    return any(w in text for w in confirm_words)


class MockAgentLLM:
    """행동 시뮬레이터 — '변화하는 도구 호출 시나리오'를 최소 규칙으로 재현.
    사용 전에 s3_tools.bind_db(conn) 와 session.reset_session() 을 한 뒤 쓴다."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        # [도메인] 규칙 파라미터 — 도메인에 맞게 조절
        self.rel_offsets = {"모레": 2, "내일": 1, "다음": 1}
        self.confirm_words = ("해주세요", "로 해주세요", "확정", "그걸로", "네 그")

    def agent_step(self, history: list[dict], _tools: list | None = None, **kw: object) -> dict:
        user_msgs = [m for m in history if m["role"] == "user"]
        text = user_msgs[-1]["content"] if user_msgs else ""
        name, birth = self._parse_identity(text)
        meta = session.SESSION_META

        if not session.SESSION["verified"]:
            if name and birth:
                self.calls.append(("verify_customer", {"name": name, "birth_date": birth}))
                return {"content": None, "tool_calls": [tools._tc("verify_customer",
                                                                  {"name": name, "birth_date": birth})]}
            return {"content": "본인 확인을 위해 성함과 생년월일을 말씀해 주세요.", "tool_calls": []}

        if "취소" in text:
            if meta["did_cancel"]:
                return {"content": "이미 취소되었습니다.", "tool_calls": []}
            if meta["last_case_id"]:
                self.calls.append(("cancel_case", {"case_id": meta["last_case_id"]}))
                return {"content": None, "tool_calls": [tools._tc("cancel_case",
                                                                  {"case_id": meta["last_case_id"]})]}
            return {"content": "조회 후 취소해 드리겠습니다.", "tool_calls": [tools._tc("list_cases", {})]}

        rel = _rel_date(text, self.rel_offsets)
        if rel and not meta["last_slots"]:
            self.calls.append(("get_available_slots", {"service": BOOKING_SERVICE, "date": rel}))
            return {"content": None, "tool_calls": [tools._tc("get_available_slots",
                                                              {"service": BOOKING_SERVICE, "date": rel})]}

        if meta["last_slots"] and not meta["did_open"] and _confirm(text, self.confirm_words):
            m = re.search(r"(\d{1,2})\s*시", text)
            slot = next((s for s in meta["last_slots"]
                         if m and s["time"].startswith(f"{int(m.group(1)):02d}")),
                        meta["last_slots"][0])
            self.calls.append(("open_case", {"service": BOOKING_SERVICE, "summary": text,
                                             "slot_id": slot["slot_id"]}))
            return {"content": None, "tool_calls": [tools._tc("open_case",
                                                              {"service": BOOKING_SERVICE, "summary": text,
                                                               "slot_id": slot["slot_id"]})]}

        if any(w in text for w in ("조회", "내 예약", "내 케이스", "보여줘")):
            if meta["listed_done"]:
                return {"content": "조회 결과를 안내해 드렸습니다.", "tool_calls": []}
            return {"content": None, "tool_calls": [tools._tc("list_cases", {})]}

        if meta["did_open"] and not meta["did_cancel"]:
            return {"content": "접수해 드렸습니다. 더 필요한 것이 있으신가요?", "tool_calls": []}
        return {"content": "무엇을 도와드릴까요?", "tool_calls": []}

    @staticmethod
    def _parse_identity(text: str) -> tuple[str | None, str | None]:
        m = re.search(r"([가-힣]{2,4})\s*(\d{4}-\d{2}-\d{2})", text)
        return (m.group(1), m.group(2)) if m else (None, None)


def chat(history: list[dict], user_text: str, agent: MockAgentLLM | None = None) -> str:
    """왕복 루프 — 도구 호출이 있으면 실행 결과를 history 에 붙여 계속, 없으면 응답 반환."""
    agent = agent or MockAgentLLM()
    history.append({"role": "user", "content": user_text})
    for _ in range(MAX_TOOL_TURNS):
        out = agent.agent_step(history, tools=tools.build_tools())
        if out.get("tool_calls"):
            for tc in out["tool_calls"]:
                result = tools.execute_tool(tc["function"]["name"],
                                            json.loads(tc["function"]["arguments"]))
                history.append({"role": "assistant", "content": None, "tool_calls": [tc]})
                history.append({"role": "tool", "tool_call_id": tc["id"],
                                "content": json.dumps(result, ensure_ascii=False)})
        else:
            history.append({"role": "assistant", "content": out["content"]})
            return out["content"]
    return "도구 호출이 계속되어 중단되었습니다. (MAX_TOOL_TURNS)"


def demo_journey() -> str:
    """예약 여정 시연 (booking): 인증+조회 → 확정 → 조회 → 취소. (도메인 기준 스모크)"""
    if not (domain.SEED_SERVICES and domain.UTTERANCES):
        return "도메인 미채움 — SEED_SERVICES/UTTERANCES 를 채우면 시연이 동작합니다."
    conn = None
    try:
        from . import s3_db
        conn = s3_db.reset_db_state()
        tools.bind_db(conn)
        session.reset_session()
    except RuntimeError as e:
        return f"DB 미초기화: {e}"

    h: list[dict] = []
    lines = []
    lines.append("── 턴1: 본인 확인 + 모레 슬롯 조회 ──")
    lines.append("AI: " + chat(h, "안녕하세요, 김하나 1985-05-12 이고 모레 기사 방문 예약하고 싶어요"))
    lines.append("── 턴2: 10시 확정 ──")
    lines.append("AI: " + chat(h, "오전 10시로 해주세요"))
    cid = session.SESSION_META["last_case_id"]
    if cid:
        row = conn.execute("SELECT * FROM cases WHERE case_id=?", (cid,)).fetchone()
        sl = conn.execute("SELECT * FROM slots WHERE slot_id=?", (row["slot_id"],)).fetchone()
        assert row["status"] == "opened" and sl["available"] == 0
        lines.append("   ⇒ 케이스 생성 + 슬롯 점유 확인 ✅ (status opened, slot available=0)")
    lines.append("── 턴3: 내 예약 조회 ──")
    lines.append("AI: " + chat(h, "내 예약 좀 보여줘"))
    lines.append("── 턴4: 취소 ──")
    lines.append("AI: " + chat(h, "그건 취소할게요"))
    if cid:
        sl2 = conn.execute("SELECT * FROM slots WHERE slot_id=?", (
            conn.execute("SELECT slot_id FROM cases WHERE case_id=?", (cid,)).fetchone()["slot_id"],)).fetchone()
        status = conn.execute("SELECT status FROM cases WHERE case_id=?", (cid,)).fetchone()["status"]
        assert status == "cancelled" and sl2["available"] == 1
        lines.append("   ⇒ 취소 + 슬롯 반납 확인 ✅ (status cancelled, slot available=1)")
    conn.close()
    return "\n".join(lines)


if __name__ == "__main__":
    print(demo_journey())
