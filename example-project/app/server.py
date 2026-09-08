# app/server.py — 음성 상담 에이전트 API (로컬 실습/프로토타입)
from __future__ import annotations
#
# ── 이 파일의 위치 ─────────────────────────────────────────────────────────
#   template/s3_* 모듈(전역 SESSION·단일 커넥션)을 '세션 별 상태 + DB 락 동시성'으로
#   재배선한 단일 파일 참조 구현입니다. 노트북 진행 가이드 [4단계]와 동작이 같습니다.
#   - sess 를 첫 인자로 받는 도구(세션=신원) · DB_LOCK(동시성) · TTL 세션
#   - 실물 엔진 기본: mlx-whisper(ASR) · sherpa-onnx Supertonic(TTS) · Ollama qwen(LLM). mock 은 주석
#   - 도메인 시드(고객/서비스/슬롯)는 template/s1_domain 을 사용
#
#   ⚠️ 실행 전에 template/s1_domain.py 를 채워야 합니다. (미채움 시 메시지와 함께 종료)
#   ⚠️ [REAL] 엔진 연결 지점은 파일 끝 주석 참고 (가이드 §REAL 전환)
# ────────────────────────────────────────────────────────────────────────────
import base64
import io
import json
import os
import sqlite3
import subprocess  # (실물 ASR: webm → 16k mono wav 변환용)
import threading
import time
import uuid
import wave
from datetime import datetime, timedelta  # noqa: F401
from pathlib import Path

import numpy as np

# ── 템플릿 공통 모듈 ──
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from template import s1_domain as domain  # noqa: E402
from template import s1_asr as asr_contract  # noqa: E402  (환각 필터 · 한글 후처리)
from template import s0_audio_contract as audio_contract  # noqa: E402  (16k·mono·f32 규격화)

# ── .env 로드 (AIGC_* 환경변수) — 없어도 기본값으로 동작 ──
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv 미설치 시 환경변수만 사용
    pass

# ── [REAL] 실물 엔진 라이브러리 ──
import sherpa_onnx
import mlx_whisper
from openai import OpenAI

APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "app.db"

# ── 인메모리 세션 저장소 ──
SESSIONS: dict[str, "Session"] = {}
SESSIONS_LOCK = threading.Lock()
SESSION_TTL_SEC = int(os.getenv("AIGC_SESSION_TTL", 60 * 30))


class Session:
    def __init__(self, sid: str) -> None:
        self.sid = sid
        self.verified = False
        self.customer_id = None
        self.customer_name = None
        self.meta = {"last_slots": [], "did_open": False,
                     "listed_done": False, "last_case_id": None, "did_cancel": False}
        self.history: list[dict] = []
        self.created = time.time()

    def touch(self) -> None:
        self.created = time.time()


def get_session(sid: str) -> Session | None:
    """없거나 만료면 404 효과 (None). 남의 세션 인증을 물려주지 않는다."""
    with SESSIONS_LOCK:
        s = SESSIONS.get(sid)
        if s is None:
            return None
        if time.time() - s.created > SESSION_TTL_SEC:
            del SESSIONS[sid]
            return None
        s.touch()
        return s


# ── SQLite 공용 연결 (동시성) — 첫 사용 시점에 생성 (import 만으로 db 파일을 안 만든다) ──
conn = None
DB_LOCK = threading.Lock()


def _db() -> sqlite3.Connection:
    global conn
    if conn is None:
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
    return conn

SCHEMA = """
DROP TABLE IF EXISTS audit_log; DROP TABLE IF EXISTS cases;
DROP TABLE IF EXISTS slots; DROP TABLE IF EXISTS services; DROP TABLE IF EXISTS customers;
CREATE TABLE customers(customer_id INTEGER PRIMARY KEY, name TEXT NOT NULL, birth_date TEXT NOT NULL);
CREATE TABLE services(service_id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, kind TEXT NOT NULL);
CREATE TABLE slots(slot_id INTEGER PRIMARY KEY, service_id INTEGER NOT NULL, start_time TEXT NOT NULL, available INTEGER NOT NULL DEFAULT 1, UNIQUE(service_id,start_time));
CREATE TABLE cases(case_id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL, service_id INTEGER NOT NULL, slot_id INTEGER, status TEXT NOT NULL DEFAULT 'opened', priority TEXT NOT NULL, summary TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE audit_log(log_id INTEGER PRIMARY KEY, action TEXT NOT NULL, ok INTEGER NOT NULL, customer_id INTEGER, case_id INTEGER, detail TEXT, created_at TEXT NOT NULL);
"""


def reset_db_state() -> sqlite3.Connection:
    if not domain.SEED_SERVICES:
        raise RuntimeError(
            "app/server.py 는 template/s1_domain.py 의 SEED_* 를 필요로 합니다. "
            "도메인을 먼저 채우세요.")
    conn = _db()
    with DB_LOCK:
        conn.executescript(SCHEMA)
        d0 = datetime.now().date()
        d1 = d0 + timedelta(days=1)
        d2 = d0 + timedelta(days=2)
        conn.executemany("INSERT INTO customers(name,birth_date) VALUES(?,?)", domain.SEED_CUSTOMERS)
        conn.executemany("INSERT INTO services(name,kind) VALUES(?,?)", domain.SEED_SERVICES)
        for name, kind in domain.SEED_SERVICES:
            if kind != "booking":
                continue
            svcid = conn.execute("SELECT service_id FROM services WHERE name=?", (name,)).fetchone()[0]
            days = {1: d1, 2: d2}
            rows = []
            for off in domain.SEED_SLOT_OFFSET_DAYS:
                d = days.get(off, d0 + timedelta(days=off)).isoformat()
                for t in domain.SEED_SLOT_TIMES:
                    rows.append((svcid, f"{d} {t}"))
            conn.executemany("INSERT INTO slots(service_id,start_time) VALUES(?,?)", rows)
        conn.commit()


# ── 실물 엔진 (mlx-whisper / sherpa-onnx) — [REAL] 기본 ─────────────────────
class RealASR:
    name = "mlx-whisper"

    def transcribe(self, wav_path: Path) -> str:
        # wav_path: ffmpeg 로 만든 16k·mono·wav 여야 한다 (audio-turn 에서 변환)
        r = mlx_whisper.transcribe(
            str(wav_path),
            path_or_hf_repo=os.getenv("AIGC_ASR_MODEL",
                                      "mlx-community/whisper-large-v3-turbo"),
            language="ko",
        )
        text = (r["text"] or "").strip()
        if asr_contract.is_hallucination(text):
            return ""
        return asr_contract.postprocess_ko(text)


class RealTTS:
    name = "supertonic"

    def __init__(self) -> None:
        self._tts = None

    def _load(self) -> None:
        d = Path(__file__).resolve().parent.parent / "models" / os.getenv(
            "AIGC_TTS_SUPERTONIC", "sherpa-onnx-supertonic-3-tts-int8-2026-05-11")
        if not d.exists():
            raise RuntimeError(
                f"Supertonic TTS 모델이 없습니다: {d}\n"
                "GUIDE.md §5.3 의 GitHub release 를 다운로드해 models/ 에 풀어 주세요.")
        def p(name: str) -> str:
            return str(d / name)
        st = sherpa_onnx.OfflineTtsSupertonicModelConfig(
            duration_predictor=p("duration_predictor.int8.onnx"),
            text_encoder=p("text_encoder.int8.onnx"),
            vector_estimator=p("vector_estimator.int8.onnx"),
            vocoder=p("vocoder.int8.onnx"),
            tts_json=p("tts.json"),
            unicode_indexer=p("unicode_indexer.bin"),
            voice_style=p("voice.bin"),
        )
        self._tts = sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(supertonic=st, provider="cpu")))

    def __call__(self, text: str) -> tuple[np.ndarray, int]:
        if self._tts is None:
            self._load()
        g = sherpa_onnx.GenerationConfig()
        g.sid, g.speed, g.extra["lang"] = 0, 1.0, "ko"
        out = self._tts.generate(text, g)
        return np.asarray(out.samples, dtype=np.float32), int(out.sample_rate)


ASR = RealASR()
TTS = RealTTS()

# ── mock 엔진 (예전 기본값 · 이제는 비활성) ─────────────────────────────────
# class MockASR:
#     name = "mock-asr"
#
#     def transcribe(self, wav_path: Path) -> str:
#         # [도메인] 데모용 '캔드 스크립트' — 실제 마이크 녹음을 mock 으로 대체한다.
#         # 도메인 기준 예시: 택배/배송 — SEED_CUSTOMERS/SEED_SERVICES 와 맞추면 동작한다.
#         booked = next((n for n, k in domain.SEED_SERVICES if k == "booking"), "기사 방문")
#         return f"김하나 1985-05-12 이고요 내일 {booked} 예약하고 싶어요"
#
#
# class MockTTS:
#     name = "mock-tts"
#
#     def __call__(self, text: str) -> tuple[np.ndarray, int]:
#         sr = 16000
#         dur = 0.16 * len(text) + 0.4
#         t = np.arange(int(sr * dur)) / sr
#         y = (0.1 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
#         return y, sr
#
#
# ASR = MockASR()
# TTS = MockTTS()

# ── 도구 (sess 를 첫 인자로!) — 노트북 3단계 도구의 '세션' 이식 ──
def _verify(sess: Session, name: str, birth_date: str) -> dict:
    conn = _db()
    with DB_LOCK:
        rows = conn.execute("SELECT * FROM customers WHERE name=? AND birth_date=?",
                            (name, birth_date)).fetchall()
    if len(rows) == 1:
        sess.verified = True
        sess.customer_id = rows[0]["customer_id"]
        sess.customer_name = name
        return {"ok": True, "message": f"{name}님 본인 확인되었습니다."}
    if len(rows) >= 2:
        return {"ok": False, "message": "여러 고객과 일치합니다. 상담원 연결이 필요합니다.",
                "reason": "ambiguous"}
    return {"ok": False, "message": "고객 정보가 일치하지 않습니다.", "reason": "no_match"}


def _services(sess: Session) -> dict:
    conn = _db()
    with DB_LOCK:
        names = [r["name"] for r in conn.execute("SELECT name FROM services ORDER BY service_id")]
    return {"services": names}


def _slots(sess: Session, service: str, date: str) -> dict:
    conn = _db()
    with DB_LOCK:
        s = conn.execute("SELECT * FROM services WHERE name=?", (service,)).fetchone()
        if s is None:
            return {"ok": False, "message": "없는 서비스입니다.", "reason": "no_service"}
        rows = [dict(r) for r in conn.execute(
            "SELECT slot_id, substr(start_time,12) time FROM slots WHERE service_id=? "
            "AND start_time LIKE ? AND available=1 ORDER BY start_time",
            (s["service_id"], date + "%"))]
    if not rows:
        return {"ok": False, "message": "예약 가능한 시간이 없습니다.", "reason": "no_slots"}
    sess.meta["last_slots"] = rows
    return {"ok": True, "slots": rows}


def _open(sess: Session, service: str, summary: str, slot_id: int | None = None,
          now: datetime | None = None) -> dict:
    tim = now or datetime.now()
    if not sess.verified:
        return {"ok": False, "reason": "not_verified"}
    conn = _db()
    with DB_LOCK:
        s = conn.execute("SELECT * FROM services WHERE name=?", (service,)).fetchone()
        if s is None:
            return {"ok": False, "message": "없는 서비스입니다.", "reason": "no_service"}
        open_cnt = conn.execute(
            "SELECT COUNT(*) c FROM cases WHERE customer_id=? AND status IN ('opened','in_progress')",
            (sess.customer_id,)).fetchone()["c"]
        if open_cnt >= 3:   # [도메인] 활성 상한 — s3_policy 와 맞춘다
            return {"ok": False, "message": "진행 중인 케이스가 많습니다.", "reason": "too_many_active"}
        conn.execute("BEGIN IMMEDIATE")
        try:
            if s["kind"] == "booking":
                if not slot_id:
                    conn.rollback()
                    return {"ok": False, "message": "예약 시간대를 선택해 주세요.", "reason": "need_slot"}
                sl = conn.execute("SELECT * FROM slots WHERE slot_id=? AND service_id=?",
                                  (slot_id, s["service_id"])).fetchone()
                if sl is None or not sl["available"]:
                    conn.rollback()
                    return {"ok": False, "message": "그 시간은 예약이 불가합니다.", "reason": "already_taken"}
                conn.execute("UPDATE slots SET available=0 WHERE slot_id=?", (slot_id,))
            else:
                slot_id = None
            cur = conn.execute(
                "INSERT INTO cases(customer_id,service_id,slot_id,status,priority,summary,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (sess.customer_id, s["service_id"], slot_id, "opened",
                 domain.P_ORDER[-1], summary, tim.isoformat(timespec="seconds")))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    sess.meta["did_open"] = True
    sess.meta["last_case_id"] = cur.lastrowid
    return {"ok": True, "case_id": cur.lastrowid, "status": "opened"}


def _mine(sess: Session) -> dict:
    conn = _db()
    with DB_LOCK:
        rows = [dict(r) for r in conn.execute(
            "SELECT c.case_id, s.name service, c.status FROM cases c "
            "JOIN services s ON c.service_id=s.service_id "
            "WHERE c.customer_id=? ORDER BY c.case_id DESC", (sess.customer_id,))]
    sess.meta["listed_done"] = True
    return {"cases": rows}


def _cancel(sess: Session, case_id: int) -> dict:
    if not sess.verified:
        return {"ok": False, "reason": "not_verified"}
    conn = _db()
    with DB_LOCK:
        c = conn.execute("SELECT * FROM cases WHERE case_id=? AND customer_id=?",
                         (case_id, sess.customer_id)).fetchone()
        if c is None:
            return {"ok": False, "message": "해당 예약을 찾을 수 없습니다.", "reason": "not_found"}
        if c["status"] in ("cancelled", "resolved"):
            return {"ok": False, "message": "이미 처리되었습니다.", "reason": "not_active"}
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("UPDATE cases SET status='cancelled' WHERE case_id=?", (case_id,))
            if c["slot_id"]:
                conn.execute("UPDATE slots SET available=1 WHERE slot_id=?", (c["slot_id"],))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    sess.meta["did_cancel"] = True
    return {"ok": True, "case_id": case_id, "status": "cancelled"}


TOOLS = {"verify_customer": _verify, "list_services": _services, "get_available_slots": _slots,
         "open_case": _open, "list_cases": _mine, "cancel_case": _cancel}
WRITE_TOOLS = {"open_case", "cancel_case"}   # 실행기에서 이중 방어


def _execute_tool(sess: Session, name: str, args: dict) -> dict:
    if name not in TOOLS:
        return {"error": "unknown_tool", "message": f"정의되지 않은 도구: {name}"}
    if name in WRITE_TOOLS and not sess.verified:
        return {"error": "not_verified", "message": "본인 확인이 먼저 필요합니다."}
    try:
        return TOOLS[name](sess, **args)
    except (TypeError, ValueError) as e:
        return {"error": "bad_arguments", "message": f"{type(e).__name__}: {e}"}


# ── 실물 에이전트 — Ollama qwen (OpenAI 호환 API) ────────────────────────────
#   도구 루프는 GUIDE §5.4 주의사항대로 'auto + 왕복 루프'로 자체 수행한다.
#   - SYSTEM_PROMPT 에 '오늘 날짜'를 반드시 주입 (날짜 환각 방지, GUIDE T2)
#   - 반환은 서버 _agent_turn 계약: {"content", "tool_log"} — tool_log 는 UI 로그용
_TOOL_DESCRIPTIONS = {
    "verify_customer": "고객 본인 확인 — name(이름), birth_date(YYYY-MM-DD)",
    "list_services": "제공 서비스 목록 조회",
    "get_available_slots": "예약 가능 슬롯 조회 — service(서비스명), date(YYYY-MM-DD, 반드시 오늘 날짜 기준으로 산정)",
    "open_case": "케이스 개설 — service(서비스명), summary(요청 내용), slot_id(booking 시 고른 슬롯)",
    "list_cases": "내 케이스/예약 목록 조회",
    "cancel_case": "케이스 취소 — case_id(정수)",
}


def _tool_schemas() -> list[dict]:
    props = {
        "verify_customer": {"name": {"type": "string"}, "birth_date": {"type": "string"}},
        "list_services": {},
        "get_available_slots": {"service": {"type": "string"}, "date": {"type": "string"}},
        "open_case": {"service": {"type": "string"}, "summary": {"type": "string"},
                      "slot_id": {"type": "integer"}},
        "list_cases": {},
        "cancel_case": {"case_id": {"type": "integer"}},
    }
    required = {
        "verify_customer": ["name", "birth_date"],
        "get_available_slots": ["service", "date"],
        "open_case": ["service", "summary"],
        "cancel_case": ["case_id"],
    }
    return [{"type": "function", "function": {
                "name": name, "description": desc,
                "parameters": {"type": "object", "properties": props.get(name, {}),
                               "required": required.get(name, [])}}}
            for name, desc in _TOOL_DESCRIPTIONS.items()]


class RealAgent:
    """Ollama qwen 실물 에이전트 — 세션 별 채팅 메시지를 자체 보관하고,
    도구는 auto 왕복 루프(서버 _execute_tool 재사용)로 완성한다."""

    MAX_TOOL_TURNS = 6        # GUIDE T10 — 무한 도구 왕복 방지

    def __init__(self) -> None:
        self.model = os.getenv("AIGC_LLM_MODEL", "qwen2.5:32b")
        self.client = OpenAI(base_url=os.getenv(
            "AIGC_LLM_BASE_URL", "http://localhost:11434/v1"), api_key="ollama")
        self.chats: dict[str, list[dict]] = {}
        today = datetime.now().date().isoformat()
        catalog = " · ".join(f"{n}({('예약' if k == 'booking' else '접수')})"
                             for n, k in domain.SEED_SERVICES)
        self.system = (
            "당신은 택배/배송 고객상담센터의 음성 상담원입니다. "
            f"오늘 날짜는 {today} 입니다. 날짜는 반드시 이 값만 사용하세요.\n"
            f"서비스 카탈로그(정확한 이름만 사용): {catalog}\n"
            "규칙:\n"
            "1) verify_customer(이름+생년월일) 로 본인 확인을 먼저 한 뒤 업무를 진행한다.\n"
            "2) 예약(booking)은 get_available_slots(service, date) 로 슬롯을 조회하고, "
            "고객이 고른 시간의 slot_id 로 open_case(service, summary, slot_id)를 연다.\n"
            "3) 조회는 list_cases, 취소는 cancel_case(case_id) 를 쓴다.\n"
            "4) 도구 결과만 근거로 최종 안내를 한국어로 2~3문장 이내로 간결하게 한다.\n"
            "5) 이번 응답에서 도구가 필요하면 함께 호출하고, 결과를 기다린 뒤 최종 응답한다.\n"
            "6) 서비스명이 확실하지 않으면 list_services 로 확인한다."
        )

    def agent_step(self, sess: Session, text: str, history: list) -> dict:
        sid = sess.sid
        conv = self.chats.setdefault(sid, [{"role": "system", "content": self.system}])
        conv.append({"role": "user", "content": text})
        tool_log: list[dict] = []
        for _ in range(self.MAX_TOOL_TURNS):
            r = self.client.chat.completions.create(
                model=self.model, messages=conv, tools=_tool_schemas(), temperature=0.0)
            msg = r.choices[0].message
            calls = getattr(msg, "tool_calls", None)
            if not calls:
                answer = (getattr(msg, "content", None) or "네.").strip()
                conv.append({"role": "assistant", "content": answer})
                self._prune()
                return {"content": answer, "tool_log": tool_log}
            conv.append({"role": "assistant", "content": getattr(msg, "content", None) or "",
                         "tool_calls": [{"id": c.id, "type": "function",
                                         "function": {"name": c.function.name,
                                                      "arguments": c.function.arguments}}
                                        for c in calls]})
            for c in calls:
                name = c.function.name
                try:
                    args = json.loads(c.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                res = _execute_tool(sess, name, args)
                tool_log.append({"tool": name, "args": args, "result": res})
                conv.append({"role": "tool", "tool_call_id": c.id, "name": name,
                             "content": json.dumps(res, ensure_ascii=False)})
        self._prune()
        return {"content": "도구 호출이 계속되어 중단되었습니다.", "tool_log": tool_log}

    def _prune(self) -> None:
        live = set(SESSIONS)
        if len(self.chats) > len(live) + 5:
            self.chats = {k: v for k, v in self.chats.items() if k in live}


LLM_AGENT = RealAgent()

# ── mock 에이전트 (예전 기본값 · 이제는 비활성) ─────────────────────────────
# class MockAgent:
#     # [도메인] booking 서비스명 — SEED_SERVICES 의 booking 이름과 일치해야 함
#     BOOKING_SERVICE = next((n for n, k in domain.SEED_SERVICES if k == "booking"), "")
#
#     def agent_step(self, sess: Session, text: str, history: list) -> dict:
#         import re
#         ids = re.search(r"([가-힣]{2,4})\s*(\d{4}-\d{2}-\d{2})", text)
#         if not sess.verified:
#             if ids:
#                 return {"tool_calls": [("verify_customer",
#                                         {"name": ids.group(1), "birth_date": ids.group(2)})]}
#             return {"content": "본인 확인을 위해 성함과 생년월일을 말씀해 주세요."}
#         if "취소" in text:
#             if sess.meta["did_cancel"]:
#                 return {"content": "이미 취소되었습니다."}
#             if sess.meta["last_case_id"]:
#                 return {"tool_calls": [("cancel_case", {"case_id": sess.meta["last_case_id"]})]}
#             return {"tool_calls": [("list_cases", {})]}
#         if ("내일" in text or "모레" in text) and not sess.meta["last_slots"]:
#             d = "모레" if "모레" in text else "내일"
#             date_str = (datetime.now().date() + timedelta(days=2 if d == "모레" else 1)).isoformat()
#             return {"tool_calls": [("get_available_slots",
#                                     {"service": self.BOOKING_SERVICE, "date": date_str})]}
#         if any(w in text for w in ("조회", "내 예약", "내 케이스", "보여줘")):
#             if sess.meta["listed_done"]:
#                 return {"content": "조회 결과는 이미 안내해 드렸습니다."}
#             return {"tool_calls": [("list_cases", {})]}
#         m = re.search(r"(\d{1,2})\s*시", text)
#         if sess.meta["last_slots"] and not sess.meta["did_open"] and ("해주세요" in text or m):
#             slot = next((s for s in sess.meta["last_slots"]
#                          if m and s["time"].startswith(f"{int(m.group(1)):02d}")),
#                         sess.meta["last_slots"][0])
#             return {"tool_calls": [("open_case", {"service": self.BOOKING_SERVICE,
#                                                   "summary": text, "slot_id": slot["slot_id"]})]}
#         return {"content": "네, 그렇게 도와드릴게요."}
#
#
# LLM_AGENT = MockAgent()

# ── FastAPI 앱 ──
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

app = FastAPI(title="voice-agent-template")


def _agent_turn(sess: Session, text: str) -> tuple[str, list]:
    """1턴: LLM 루프(도구 실행까지) → 최종 응답 문자열."""
    sess.history.append({"role": "user", "content": text})
    tool_log: list[dict] = []
    for _ in range(6):
        out = LLM_AGENT.agent_step(sess, text, sess.history)
        if out.get("tool_calls"):
            for name, args in out["tool_calls"]:
                res = _execute_tool(sess, name, args)
                tool_log.append({"tool": name, "args": args, "result": res})
                sess.history.append({"role": "assistant",
                                     "content": json.dumps({"tool": name, "args": args},
                                                           ensure_ascii=False)})
                sess.history.append({"role": "tool",
                                     "content": json.dumps(res, ensure_ascii=False)})
        else:
            if out.get("tool_log"):   # 실물 에이전트의 도구 로그를 UI 로그로 병합
                tool_log.extend(out["tool_log"])
            sess.history.append({"role": "assistant", "content": out["content"]})
            return out["content"], tool_log
    return "도구 호출이 계속되어 중단되었습니다.", tool_log


@app.on_event("startup")
def _init_db() -> None:
    reset_db_state()


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "mode": getattr(ASR, "name", "?")}


@app.post("/api/session")
def create_session() -> dict:
    sid = uuid.uuid4().hex
    with SESSIONS_LOCK:
        SESSIONS[sid] = Session(sid)
    return {"session_id": sid}


def _session(sid: str) -> Session:
    s = get_session(sid)
    if s is None:
        raise HTTPException(status_code=404, detail="session_not_found")
    return s


@app.post("/api/text-turn")
async def text_turn(session_id: str = Form(...), text: str = Form(...)) -> dict:
    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="empty_text")
    sess = _session(session_id)
    t0 = time.time()
    answer, tool_log = _agent_turn(sess, text)
    return {"answer": answer, "tools": tool_log,
            "timing_ms": {"turn": round((time.time() - t0) * 1000)}}


@app.post("/api/audio-turn")
async def audio_turn(request: Request) -> dict:
    form = await request.form()
    sess = _session(form.get("session_id"))
    file = form.get("audio")
    data = await file.read() if file else None
    if not data or len(data) < 2000:
        raise HTTPException(status_code=400, detail="audio_too_short")
    tmp = APP_DIR / "tmp_upload.webm"
    tmp.write_bytes(data)

    # [REAL] webm → 16k·mono·wav 변환 (mlx-whisper 입력 규격 — 오디오 계약)
    wav = APP_DIR / "tmp_upload.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(tmp), "-ar", "16000", "-ac", "1",
         "-sample_fmt", "s16", str(wav)],
        check=True, capture_output=True)

    t0 = time.time()
    user_text = ASR.transcribe(wav)          # mlx-whisper (Apple Silicon Metal)
    asr_ms = (time.time() - t0) * 1000
    if not user_text:   # 환각 필터가 '문구뿐'으로 판정 → 비는 채로 LLM 에 넘기지 않는다
        raise HTTPException(status_code=400, detail="음성이 인식되지 않았습니다. 다시 말씀해 주세요.")
    answer, tool_log = _agent_turn(sess, user_text)

    audio, sr = TTS(answer)                  # sherpa-onnx Supertonic(ko)
    audio = audio_contract.to_16k_mono(audio, sr)   # 계약(16k·mono·f32) 규격화
    buf = io.BytesIO()
    arr = np.asarray(audio, dtype=np.float32)
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(audio_contract.SR)
        w.writeframes((np.clip(arr, -1, 1) * 32767).astype(np.int16).tobytes())
    audio_b64 = base64.b64encode(buf.getvalue()).decode()
    return {"user_text": user_text, "answer": answer, "audio_b64": audio_b64,
            "tools": tool_log, "timing_ms": {"asr": round(asr_ms), "llm": 0, "tts": 0}}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (APP_DIR / "static" / "index.html").read_text(encoding="utf-8")


# ── [REAL] 실물 엔진 기본 (mock 은 위에 주석 처리) ─────────────────────────
#   ASR  : mlx-whisper — AIGC_ASR_MODEL(HF 저장소명), 최초 1회 모델(~1GB) 다운로드
#   TTS  : sherpa-onnx Supertonic — AIGC_TTS_SUPERTONIC, models/ 에 모델 필요
#   LLM  : Ollama qwen — AIGC_LLM_MODEL (예: qwen2.5:32b), ollama pull 필요
#   env  : example-project/.env 에 AIGC_* 를 넣어 두면 로드된다 (python-dotenv)
#   mock 전환 : 위 ASR/TTS/LLM_AGENT 대입 부분에서 mock 버전으로 바꾸면 됨

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("AIGC_PORT", "8000")))