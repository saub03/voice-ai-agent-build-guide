from __future__ import annotations

# ════════════════════════════════════════════════════════════════════
# [0단계 · 환경 점검] 이 프로젝트가 쓸 라이브러리/프로그램 준비도를 확인한다.
#   - AVAIL  : Python 패키지 유무 (numpy, jsonschema, gtts, openai, fastapi, …)
#   - OLLAMA : ollama CLI 유무  → 뒤의 [REAL] 셀/스위치의 깃발
#   - FFMPEG : ffmpeg 유무      → gTTS(mp3)→wav 변환, 실물 오디오 규격화
# 메커니즘 참고:
#   - 최소 요건은 numpy 하나 — mock 모드로는 여기만으로도 끝까지 진행된다.
#   - 실물 엔진([REAL])은 "있으면 연결, 없으면 스킵"이 아니라
#     '구현은 mock 으로 다 끝내고, 있으면 깃발을 올려 검증'하는 데 쓴다.
# ════════════════════════════════════════════════════════════════════
import importlib.util
import json
import shutil
import sys
from typing import Any

# 이 프로젝트가 선택적으로 쓰는 패키지 이름 (많아지면 여기에 추가)
OPTIONAL_PACKAGES = [
    "numpy",
    "jsonschema",
    "gtts",
    "mlx_whisper",
    "sherpa_onnx",
    "fastapi",
    "uvicorn",
    "httpx",
    "openai",
]


def _avail(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def probe() -> tuple[dict[str, bool], bool, bool]:
    """패키지/CLI 가용성 점검 → (AVAIL dict, ollama 유무, ffmpeg 유무)."""
    avail = {name: _avail(name) for name in OPTIONAL_PACKAGES}
    return avail, shutil.which("ollama") is not None, shutil.which("ffmpeg") is not None


# import 시 한 번만 계산해 두고 전역으로 공유한다. (뒤의 [REAL] 스위치들이 읽음)
AVAIL, OLLAMA, FFMPEG = probe()


def report(require: tuple[str, ...] = ("numpy",)) -> dict[str, Any]:
    """노트북 [0단계] 형태의 사람이 읽는 점검 리포트. 최소 요건이 없으면 AssertionError."""
    print(f"Python {sys.version.split()[0]} · {sys.platform}")
    print("ollama:", "설치됨" if OLLAMA else "없음 (brew install ollama)")
    print("ffmpeg:", "설치됨" if FFMPEG else "없음 (brew install ffmpeg)")
    print("gtts:", "설치됨" if AVAIL.get("gtts") else "없음 (pip install gTTS)")
    print(json.dumps(AVAIL, ensure_ascii=False, indent=2))
    missing = [r for r in require if not AVAIL.get(r)]
    assert not missing, f"최소 요건({', '.join(missing)})이 없습니다"
    print("mock 최소 요건 충족 ✅   (실물은 [REAL] 스위치에서만)")
    return {"avail": AVAIL, "ollama": OLLAMA, "ffmpeg": FFMPEG}


if __name__ == "__main__":
    report()
