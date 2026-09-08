from __future__ import annotations

# ════════════════════════════════════════════════════════════════════
# [1단계] 어떤 ASR 이든 {engine, text} 로 접는 어댑터 + '순수 텍스트' 계약을 정의한다.
#   - ASREngine        : 표준 계약 {engine, text}. text 는 순수 전사 문자열
#   - MockWhisper      : '파일명 stem = 골드 uid' 규칙으로 전사하는 모의 엔진.
#                        ★ mock fidelity: 실제 mlx-whisper 의 출력 '모양'을 그대로 따라야 한다.
#                          관대한 모의(실물보다 잘 넘겨주는 모의)는 뒤 단계를 거짓 통과시킨다.
#   - is_hallucination : 짧은 발화 오인식·환각으로 보이는 출력을 거른다
#   - postprocess_ko   : 도메인 오인식 → 표준 표기 사전 (s1_domain.DOMAIN_LEXICON)
#   - asr_pipeline     : 오디오 → 전사 → 후처리 → 규칙 분류 + 우선순위
# ════════════════════════════════════════════════════════════════════
import re
from pathlib import Path

from . import s1_classifier as classifier
from . import s1_domain as domain


class ASREngine:
    name = "base-asr"

    def _raw(self, path: str) -> str:
        raise NotImplementedError

    def transcribe(self, path: str) -> dict:
        """표준 계약: {engine, text}. text 는 순수 전사 문자열."""
        return {"engine": self.name, "text": (self._raw(path) or "").strip()}


# [도메인 무관] 설득형·자막형 환각 패턴 (도메인이면 여기에 추가하세요)
HALLUCINATION_PATTERNS = [
    "시청해주셔서 감사", "구독", "좋아요", "자막 제공", "감사합니다",
    "Thank you for watching",
]


def is_hallucination(text: str, has_audio_speech: bool = True) -> bool:
    """비었다/발화 없다/자막형 문구 → 환각으로 취급."""
    t = (text or "").strip()
    if not t or not has_audio_speech:
        return True
    return any(p in t for p in HALLUCINATION_PATTERNS)


def postprocess_ko(text: str) -> str:
    """도메인 오인식 → 표준 표기 정규화 (음운이 아니라 표기 간격 교정)."""
    out = text or ""
    for wrong, right in domain.DOMAIN_LEXICON.items():
        out = out.replace(wrong, right)
    return " ".join(out.split())


class MockWhisper(ASREngine):
    """모의 엔진 — 실제 ASR 의 '순수 텍스트' 출력 모양을 그대로 따르는 결정적 전사.
    파일명 stem(=골드 uid) → domain.UTTERANCES 의 원문을 돌려준다.
    만약 골드 uid 별 '<별도 전사 사전>'을 쓰고 싶다면 ASR_TABLE 을 주입받아도 된다.
    """
    name = "mock-whisper"

    def __init__(self, table: dict | None = None) -> None:
        self.table = table

    def _raw(self, path: str) -> str:
        uid = Path(str(path)).stem
        if self.table is not None:
            return self.table.get(uid, "")
        row = domain.UTTERANCES.get(uid)
        return row[0] if row else ""


def asr_pipeline(wav_path: str | Path, asr: ASREngine | None = None) -> dict:
    """오디오 → 전사 → (후처리) → 규칙 분류 + 우선순위. {intent, priority, method, asr_text}."""
    asr = asr or MockWhisper()
    rec = asr.transcribe(wav_path)
    text = postprocess_ko(rec["text"])
    label = classifier.rule_classify(text)
    label["asr_text"] = text
    label["method"] = "rule+asr"
    return label


def selfcheck() -> None:
    """ASR 계약·환각 필터·후처리 메커니즘 검증 (도메인 무관, 모의 전사로 확인)."""
    import tempfile

    if not domain.UTTERANCES:
        print("도메인 미채움 → UTTERANCES 를 채우면 모의 ASR 파이프라인이 동작합니다.")
        return
    import numpy as np  # s0 의 write_wav 사용

    from . import s0_audio_contract as audio

    n = 0
    with tempfile.TemporaryDirectory() as d:
        for uid, (text, gold, _svc) in domain.UTTERANCES.items():
            p = Path(d) / f"{uid}.wav"
            audio.write_wav(p, audio.mock_synthesize(text)["audio"])
            out = asr_pipeline(p)
            n += int(out["intent"] == gold)
        assert n == len(domain.UTTERANCES), f"모의 ASR 파이프라인 골드 {n}/{len(domain.UTTERANCES)}"
    print(f"음성 파이프라인(모의) 골드 {n}/{len(domain.UTTERANCES)} ✅")


if __name__ == "__main__":
    selfcheck()
