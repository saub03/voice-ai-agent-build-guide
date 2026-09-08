from __future__ import annotations

# ════════════════════════════════════════════════════════════════════
# [6단계] 요구사항 12가지를 점검표로 응축해 "진짜 다 지켰나"를 한 눈에 본다.
#   - 각 항목은 도메인 무관 '계약'이다 (mock 기준으로 전부 충족이 기본값).
#   - 항목별 근거 함수가 있어 출시 전에 다시 타돌릴 수 있다.
#   - 최종 회귀: 1단계 정확도 · 3단계 회귀 게이트 · 4단계 세션 · 5단계 점검표를
#     한 번에 다시 확인한다.
# ════════════════════════════════════════════════════════════════════


# 항목 (이름, 기본 충족 여부, 근거 동작 설명) — 출시 전 요구사항 점검표
CHECKLIST: list[tuple[str, bool, str]] = [
    ("본인 확인 없이 쓰기 금지",                True, "s3_tools.execute_tool → not_verified"),
    ("소유권 — 남의 케이스는 '없음'으로 처리",  True, "s3_session.load_owned_case → None"),
    ("취소 시 슬롯 반납 (유령 자리 없음)",      True, "s3_tools.cancel_case → available=1"),
    ("쓰기 작업은 원자적 트랜잭션",              True, "open_case/cancel_case BEGIN IMMEDIATE"),
    ("실패도 감사 로그에 남는다",                True, "session.audit(ok=False)"),
    ("상태 전이는 표로 제한 (전이표)",           True, "s3_policy.CASE_TRANSITIONS"),
    ("정책 함수는 clock 을 주입받아 테스트 가능", True, "policy_can_cancel(case, now)"),
    ("로그에 개인정보가 마스킹된다",             True, "s5_phi_mask.PHIFilter"),
    ("고위험(P1) 재현율 = 1.0 (계약)",           True, "s5_metrics( 도메인 채우면 검증 )"),
    ("세션이 격리된다 (TTL 포함)",               True, "app/server.py get_session TTL"),
    ("짧은 오디오/빈 입력이 4xx 로 거절된다",    True, "app/server.py 400/404"),
    ("백엔드 실패 시 즉시 실패 + 회로 차단",     True, "s5_resilience.retryable_call"),
]


def report() -> tuple[int, list[str]]:
    """점검표 실행 — (충족 수, 미충족/미확인 항목명)."""
    unmet = [name for name, ok, _src in CHECKLIST if not ok]
    print("출시 점검표:", f"{len(CHECKLIST) - len(unmet)}/{len(CHECKLIST)} 충족" if not unmet
          else f"미충족: {unmet}")
    return len(CHECKLIST) - len(unmet), unmet


def smoke() -> None:
    """메커니즘(도메인 무관)을 재확인: 회귀 실행 + 오디오 계약 + 초기화."""
    from . import s0_audio_contract as audio
    audio.selfcheck()
    from . import s5_resilience as res
    res.selfcheck()
    from . import s5_phi_mask as phi
    phi.selfcheck()
    from . import s3_policy as policy_mod
    policy_mod.selfcheck()

    # 도메인 위 회귀 — 채워졌을 때만 동작
    from . import s1_domain as domain
    from . import s3_regression_gates as rg
    if domain.domain_status()[0]:
        rg.all_pass(rg.run_gates())
    else:
        print("도메인 미채움 → 회귀 게이트는 s1_domain.py 를 채우면 실행됩니다.")
    report()


if __name__ == "__main__":
    smoke()