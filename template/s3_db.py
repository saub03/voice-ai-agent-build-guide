from __future__ import annotations

# ════════════════════════════════════════════════════════════════════
# [3단계] SQLite 스키마 + 시드. 의도한 설계:
#   ① 날짜는 전부 '오늘' 기준(D0/D1/D2)으로 계산 — 하드코딩 금지 (시드 daylight 안정성)
#   ② 신원(customers)·예약자원(slots)·접수(cases)·감사(audit_log) 를 한 DB 로
#   ③ reset_db_state() 는 몇 번을 돌려도 같은 상태 — 테스트를 재현 가능하게
#   도메인(고객·서비스·슬롯 시드)은 s1_domain 에서 가져온다.
#   ※ 참고: 본 모듈은 import 시 reset_db_state() 를 부르지 않는다.
#     실행(회귀/서버/스모크)이 필요한 시점에 호출한다. — 노트북과 달리 '부수효과 없는 import'.
# ════════════════════════════════════════════════════════════════════
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from . import s1_domain as domain

# 프로젝트 DB 경로 — 실행 위치에 따라 바뀌므로 모듈 밖에서 DB_PATH 를 주입받을 수 있게 한다.
DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "guide.db"

SCHEMA = """
DROP TABLE IF EXISTS audit_log;
DROP TABLE IF EXISTS cases;
DROP TABLE IF EXISTS slots;
DROP TABLE IF EXISTS services;
DROP TABLE IF EXISTS customers;
CREATE TABLE customers (
  customer_id  INTEGER PRIMARY KEY,
  name         TEXT NOT NULL,
  birth_date   TEXT NOT NULL
);
CREATE TABLE services (
  service_id   INTEGER PRIMARY KEY,
  name         TEXT NOT NULL UNIQUE,
  kind         TEXT NOT NULL CHECK(kind IN ('booking','ticket'))
);
CREATE TABLE slots (
  slot_id      INTEGER PRIMARY KEY,
  service_id   INTEGER NOT NULL REFERENCES services(service_id),
  start_time   TEXT NOT NULL,
  available    INTEGER NOT NULL DEFAULT 1,
  UNIQUE(service_id, start_time)
);
CREATE TABLE cases (
  case_id      INTEGER PRIMARY KEY,
  customer_id  INTEGER NOT NULL REFERENCES customers(customer_id),
  service_id   INTEGER NOT NULL REFERENCES services(service_id),
  slot_id      INTEGER REFERENCES slots(slot_id),
  status       TEXT NOT NULL DEFAULT 'opened',
  priority     TEXT NOT NULL,
  summary      TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  moved_from   INTEGER
);
CREATE TABLE audit_log (
  log_id       INTEGER PRIMARY KEY,
  action       TEXT NOT NULL,
  ok           INTEGER NOT NULL,
  customer_id  INTEGER,
  case_id      INTEGER,
  detail       TEXT,
  created_at   TEXT NOT NULL
);
"""


def rel_dates(today: date | None = None) -> tuple[date, date, date, str]:
    """D0/D1/D2 + today 문자열. 시계 대신 today 를 주입받아 재현 가능하게."""
    d0 = today or date.today()
    d1 = d0 + timedelta(days=1)
    d2 = d0 + timedelta(days=2)
    return d0, d1, d2, d0.isoformat()


def reset_db_state(db_path: str | Path | None = None) -> sqlite3.Connection:
    """스키마 재생성 + 시드 (몇 번을 돌려도 결과가 같도록 재현 가능)."""
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row

    if not domain.SEED_SERVICES:
        raise RuntimeError(
            "s1_domain.py 의 SEED_* 를 채우기 전에는 DB 를 재설정할 수 없습니다. (도메인 미채움)")

    conn.executescript(SCHEMA)
    _d0, d1, d2, _ = rel_dates()
    # 도메인 시드
    conn.executemany("INSERT INTO customers(name,birth_date) VALUES(?,?)", domain.SEED_CUSTOMERS)
    conn.executemany("INSERT INTO services(name,kind) VALUES(?,?)", domain.SEED_SERVICES)
    booking = [s for s in domain.SEED_SERVICES if s[1] == "booking"]
    if booking:
        svc = conn.execute("SELECT service_id FROM services WHERE name=?",
                           (booking[0][0],)).fetchone()[0]
        rows = []
        for offset_days in domain.SEED_SLOT_OFFSET_DAYS:
            d = (_d0 + timedelta(days=offset_days)).isoformat()
            for t in domain.SEED_SLOT_TIMES:
                rows.append((svc, f"{d} {t}"))
        conn.executemany("INSERT INTO slots(service_id,start_time) VALUES(?,?)", rows)
    conn.commit()
    return conn


def slot_dt(slot_time_str: str) -> datetime:
    """'YYYY-MM-DD HH:MM' → datetime."""
    return datetime.strptime(slot_time_str, "%Y-%m-%d %H:%M")


def selfcheck() -> None:
    if not domain.SEED_SERVICES:
        print("도메인 미채움 → SEED_CUSTOMERS/SEED_SERVICES 를 채우면 DB 재설정이 동작합니다.")
        return
    conn = reset_db_state()
    n = conn.execute("SELECT COUNT(*) c FROM customers").fetchone()["c"]
    print(f"DB 재설정 ✅  고객 {n}명 · 서비스 {len(domain.SEED_SERVICES)}종 "
          f"· 슬롯 {conn.execute('SELECT COUNT(*) c FROM slots').fetchone()['c']}개")
    conn.close()


if __name__ == "__main__":
    selfcheck()
