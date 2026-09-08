from __future__ import annotations

# ════════════════════════════════════════════════════════════════════
# [5단계] 탄력성의 부품 3개 (+ 멱등성).
#   TimeoutBudget   : 턴 전체의 시간 예산. 남은 예산만큼만 다음 호출에 허용.
#   CircuitBreaker  : closed →(연속 실패 N)→ open →(쿨다운)→ half_open →(성공)→ closed
#   retryable_call  : ConnectionError/TimeoutError 만 재시도 (4xx 는 재시도 무의미).
#                     '재시도할 가치가 있는 실패'를 타입으로 구분한다.
#   idem_key        : 멱등성 키 = 세션+행동+슬롯 해시 (uuid4 는 멱등성이 아니다)
#   MockHIS         : 정직한 mock 백엔드 — 실패/지연도 시뮬레이션한다
#                     ('항상 성공하는 mock 은 거짓말')
#   전원 clock/sleep 을 주입받아 테스트 가능.
# ════════════════════════════════════════════════════════════════════
import hashlib
import time
from typing import Callable


class TimeoutBudget:
    """턴 전체의 시간 예산. 남은 예산만큼만 다음 호출에 허용."""

    def __init__(self, total_s: float) -> None:
        self.total = total_s
        self.used = 0.0

    def remaining(self) -> float:
        return max(0.0, self.total - self.used)

    def spend(self, s: float) -> None:
        self.used += s


class CircuitOpen(Exception):
    pass


class CircuitBreaker:
    """민감 실패 누적으로 열리고, 쿨다운 후 반열림에서 성공하면 닫힌다. clock 주입."""

    def __init__(self, fail_threshold: int = 3, cooldown_sec: float = 30,
                 clock: Callable[[], float] = time.time) -> None:
        self.th = fail_threshold
        self.cooldown = cooldown_sec
        self.clock = clock
        self.failures = 0
        self.state = "closed"
        self.opened_at = None

    def allow(self) -> bool:
        if self.state == "open":
            if self.clock() - self.opened_at >= self.cooldown:
                self.state = "half_open"
                return True
            return False
        if self.state == "half_open":
            return True
        return True

    def record_success(self) -> None:
        self.failures = 0
        self.state = "closed"

    def record_failure(self) -> None:
        if self.state == "half_open":
            self.state = "open"
            self.opened_at = self.clock()
        else:
            self.failures += 1
            if self.failures >= self.th:
                self.state = "open"
                self.opened_at = self.clock()


def retryable_call(call: Callable[..., object], budget: TimeoutBudget, breaker: CircuitBreaker,
                   sleep: Callable[[float], None] = time.sleep, max_retries: int = 3) -> object:
    """ConnectionError/TimeoutError 만 재시도. 4xx 는 재시도해도 같은 실패 → 재시도하면 안 됨."""
    if not breaker.allow():
        raise CircuitOpen("circuit_open")
    last = None
    for attempt in range(max_retries):
        if budget.remaining() < 0.05:
            raise TimeoutError("budget_exhausted") from last
        t0 = time.time()
        try:
            r = call(timeout=budget.remaining())
            budget.spend(time.time() - t0)
            breaker.record_success()
            return r
        except (ConnectionError, TimeoutError) as e:
            budget.spend(time.time() - t0)
            breaker.record_failure()
            last = e
            if attempt < max_retries - 1:
                sleep(0.05 * (attempt + 1))     # 짧은 백오프 — 실전에는 무작위 지연(jitter) 혼합
    raise last


def idem_key(session_id: str, action: str, slot_ref: str) -> str:
    """같은 통화의 같은 행동은 같은 키 → 재시도가 안전하다. (uuid4 는 멱등성이 아니다)"""
    return hashlib.sha256(f"{session_id}|{action}|{slot_ref}".encode()).hexdigest()


class MockHIS:
    """정직한 mock 백엔드 — 실패/지연을 시뮬레이션. 멱등 장부(_idem)로 재시도 안전."""

    def __init__(self, fail_first_n: int = 0, latency: float = 0.0) -> None:
        self.fail_first_n = fail_first_n
        self.latency = latency
        self.calls = 0
        self._idem = {}            # 멱등성 장부

    def create_booking(self, key: str, slot_ref: str, timeout: float | None = None) -> dict:
        self.calls += 1
        if self.latency:
            time.sleep(min(self.latency, timeout or self.latency))
        if timeout is not None and self.latency > timeout:
            raise TimeoutError("HIS timeout (예산 초과)")       # 클라이언트가 준 timeout 만큼만 기다린다
        if self.calls <= self.fail_first_n:
            raise ConnectionError("HIS down (다운 시뮬레이션)")
        if key in self._idem:                                   # 같은 키 → 같은 결과 (재시도 안전)
            return {**self._idem[key], "replayed": True}
        ref = f"REF-{len(self._idem) + 1}"
        self._idem[key] = {"appointment_ref": ref, "replayed": False}
        return dict(self._idem[key])


def selfcheck() -> None:
    """재시도 + 멱등성 + 서킷브레이커 + 예산 방어를 한 번에 검증."""
    # 1) 재시도 + 멱등성: 1회 실패 후 성공, 같은 키면 같은 결과(replayed)
    his = MockHIS(fail_first_n=1)
    b = CircuitBreaker(fail_threshold=3, cooldown_sec=30, clock=lambda: 1000.0)
    budget = TimeoutBudget(2.0)
    key = idem_key("s1", "create", "slot:10")
    r1 = retryable_call(lambda timeout: his.create_booking(key, "slot:10", timeout=timeout),
                        budget, b, sleep=lambda s: None)
    r2 = retryable_call(lambda timeout: his.create_booking(key, "slot:10", timeout=timeout),
                        budget, b, sleep=lambda s: None)
    assert r1["appointment_ref"] == r2["appointment_ref"], "멱등성 실패 — 예약이 두 건"
    assert r2["replayed"] is True

    # 2) 서킷브레이커: 열린 동안은 '안 부른다' (calls 불변)
    fake_clock = {"t": 0.0}
    his2 = MockHIS(fail_first_n=999, latency=0.0)
    bre = CircuitBreaker(fail_threshold=2, cooldown_sec=60, clock=lambda: fake_clock["t"])
    bud = TimeoutBudget(5.0)
    for _ in range(2):
        try:
            retryable_call(lambda timeout: his2.create_booking("k", "s", timeout=timeout),
                           bud, bre, sleep=lambda s: None)
        except (ConnectionError, TimeoutError, CircuitOpen):
            pass
    assert bre.state == "open", bre.state
    before = his2.calls
    try:
        retryable_call(lambda timeout: his2.create_booking("k", "s", timeout=timeout),
                       bud, bre, sleep=lambda s: None)
    except (CircuitOpen, TimeoutError):
        pass
    assert his2.calls == before, f"open 상태에서 백엔드를 불렀다 ({before}→{his2.calls})"
    fake_clock["t"] += 61  # 쿨다운 후 half_open → 성공하면 closed (회복)
    his3 = MockHIS(fail_first_n=0)
    r = retryable_call(lambda timeout: his3.create_booking("k", "s", timeout=timeout),
                       bud, bre, sleep=lambda s: None)
    assert bre.state == "closed" and r["appointment_ref"] == "REF-1"

    # 3) 예산 소진 방어
    try:
        retryable_call(lambda timeout: MockHIS(latency=0.5).create_booking("k", "s", timeout=timeout),
                       TimeoutBudget(0.2), CircuitBreaker(), sleep=lambda s: None)
        raise SystemExit("예산 방어 실패!")
    except TimeoutError:
        pass
    print("재시도+멱등성 ✅ · 서킷브레이커(안 부름+회복) ✅ · 예산 소진 방어 ✅")


if __name__ == "__main__":
    selfcheck()