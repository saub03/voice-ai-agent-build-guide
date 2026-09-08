from __future__ import annotations

# ════════════════════════════════════════════════════════════════════
# [5단계] 로그에서 개인정보(PHI)를 정규식으로 제거하는 logging.Filter.
#   - 주민등록번호·휴대전화 등 패턴을 로그에서 '***마스킹***' 으로 가린다.
#   - 도메인에 따라 패턴을 추가한다 (예: 계좌번호, 주소, 이메일, 카드번호).
#   - 필터는 로그에 붙은 그 로거에만 적용된다 — 실수로라도 원문이 남으면
#     "필터가 없는 로거"와의 대조로 눈에 보이게 한다.
# ════════════════════════════════════════════════════════════════════
import logging
import re


class PHIFilter(logging.Filter):
    """주민등록번호·전화번호 등 개인정보 패턴을 로그에서 제거."""
    # [도메인] 개인정보 패턴 — 여기에 계좌/이메일/주소 패턴을 추가할 수 있다.
    PATTERNS = [
        re.compile(r"\d{6}-[1-4]\d{6}"),                 # 주민등록번호
        re.compile(r"01[016789]-\d{3,4}-\d{4}"),         # 휴대전화
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for p in self.PATTERNS:
            msg = p.sub("***마스킹***", msg)
        record.msg = msg
        record.args = ()
        return True


def masked_logger(name: str = "app") -> logging.Logger:
    """PHI 필터가 붙은 로거를 만들어 돌려준다. (중복 부착 방지)"""
    logger = logging.getLogger(name)
    if not logger.filters:
        logger.addFilter(PHIFilter())
    logger.setLevel(logging.INFO)
    return logger


def selfcheck() -> None:
    import io

    logger = masked_logger("masked_check")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    try:
        raw = "환자 850512-1234567 010-1234-5678 예약 확인"
        logger.info(raw)
        stream.flush()
        captured = stream.getvalue()
    finally:
        logger.removeHandler(handler)
    assert "850512-1234567" not in captured and "010-1234-5678" not in captured
    assert "***마스킹***" in captured
    print("PHI 마스킹 ✅ →", captured.strip())


if __name__ == "__main__":
    selfcheck()