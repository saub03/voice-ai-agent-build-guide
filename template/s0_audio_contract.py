from __future__ import annotations

# ════════════════════════════════════════════════════════════════════
# [0단계 · 공통] 오디오 '계약' = 16kHz · mono · float32.
#   이 프로젝트 시리즈의 핵심 통찰:
#   "(소리)파이프라인은 내용이 아니라 계약으로 연결한다."
#   - validate_audio_contract : 계약을 강제한다 (위반 시 즉시 AssertionError, 조용히 넘기지 않는다)
#   - to_16k_mono             : 어떤 sr/채널이든 16k·mono·f32 로 규격화 — 파이프라인 입구의 방어
#   - mock_synthesize         : 모델·네트워크 없이 계약만 지키는 결정적 합성음 (mock 엔진)
#   - write_wav / read_wav    : 계약 기반 저장/읽기
# 도메인과 무관한 "순수 메커니즘" — 그대로 재사용한다.
# ════════════════════════════════════════════════════════════════════
import wave
from pathlib import Path

import numpy as np

# 프로젝트 공통 샘플레이트 (바꿀 이유가 없으면 유지)
SR = 16000
AUDIO_CONTRACT_KEYS = {"audio", "sr", "duration_ms", "engine"}


def validate_audio_contract(r: dict) -> bool:
    """오디오 계약 검증 — 위반 시 즉시 AssertionError."""
    missing = AUDIO_CONTRACT_KEYS - set(r.keys())
    if missing:
        raise AssertionError(f"오디오 계약 위반 — 누락 키: {sorted(missing)}")
    if r["sr"] != SR:
        raise AssertionError(f"샘플레이트 위반: {r['sr']} != {SR}")
    a = r["audio"]
    if getattr(a, "ndim", 1) != 1:
        raise AssertionError("오디오는 mono(1차원)여야 함")
    if a.dtype != np.float32:
        raise AssertionError("오디오는 float32 여야 함")
    if r["duration_ms"] <= 0:
        raise AssertionError("duration_ms 는 양수여야 함")
    return True


def resample_linear(x: np.ndarray, src: int, dst: int) -> np.ndarray:
    """선형 resample — scipy 없이 (간단 버전). 실전은 ffmpeg/쉐르파 유틸 사용."""
    if src == dst:
        return x
    n = int(round(len(x) * dst / src))
    t = np.linspace(0, len(x) - 1, n)
    lo = t.astype(int)
    hi = np.minimum(lo + 1, len(x) - 1)
    w = t - lo
    return (x[lo] * (1 - w) + x[hi] * w).astype(np.float32)


def to_16k_mono(audio: np.ndarray | list, sr: int) -> np.ndarray:
    """어떤 sr/채널이든 16kHz·mono·float32 로 규격화."""
    a = np.asarray(audio, dtype=np.float32)
    if a.ndim > 1:
        a = a.mean(axis=1)
    if sr != SR:
        a = resample_linear(a, sr, SR)
    return a


def write_wav(path: str | Path, audio: np.ndarray | list, sr: int = SR) -> Path:
    """계약 기반 wav 저장 (16k·mono·int16 로 내려서 기록)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    x = to_16k_mono(audio, sr)
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())
    return p


def read_wav(path: str | Path) -> tuple[np.ndarray, int]:
    """wav 읽기 → (16k·mono·f32, 원본 sr)."""
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        x = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32767.0
    return to_16k_mono(x, sr), sr


def mock_synthesize(text: str, seed: int = 42) -> dict:
    """mock TTS — 실제 합성은 아니지만 계약을 지키는 결정적 합성음 (네트워크·모델 불필요)."""
    rng = np.random.default_rng(seed + len(text))
    dur = 0.16 * len(text.replace(" ", "")) + 0.5  # 한국어 ≈ 6음절/초
    t = np.arange(int(SR * dur)) / SR
    env = 0.5 * (1 + np.sin(2 * np.pi * 5.5 * t))
    y = (0.15 * env * np.sin(2 * np.pi * 220 * t)
         + 0.05 * env * np.sin(2 * np.pi * 440 * t)
         + 0.004 * rng.normal(size=len(t))).astype(np.float32)
    return {"audio": y, "sr": SR,
            "duration_ms": round(len(y) / SR * 1000.0, 1), "engine": "mock"}


def selfcheck() -> None:
    """오디오 계약 헬퍼 자가 검증 — 메커니즘이 깨지지 않았는지."""
    ok = mock_synthesize("안녕하세요 고객님")
    assert validate_audio_contract(ok)
    bad = dict(ok)
    bad["sr"] = 44100
    try:
        validate_audio_contract(bad)
        raise SystemExit("방어 실패!")
    except AssertionError as e:
        print("계약 방어 확인 ✅ →", e)
    print("오디오 계약 헬퍼 검증 통과 ✅")


if __name__ == "__main__":
    selfcheck()
