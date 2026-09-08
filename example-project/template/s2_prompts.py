from __future__ import annotations

# ════════════════════════════════════════════════════════════════════
# [2단계] LLM 클라이언트 '계약' + 프롬프트 계약.
#   - LLMClient      : Mock 과 실제(Ollama)가 같은 계약(chat)을 지킨다
#     → 덕분에 관문 검증을 mock 으로 온전히 돌릴 수 있다.
#   - build_ticket_prompt : 프롬프트는 '계약서'. **문자열 연결로만** 조립한다
#                           (format 치환 금지 → 중괄호 KeyError 방지).
#   - MockTicketLLM  : 정직한 모의 — 프롬프트 본문의 단어가 분류를 오염시키지 않게
#                      '문의: ' 뒤 원문만 읽는다. 도메인 규칙(s1)을 근거로 티켓 JSON 을 짓는다.
#                      faulty=True 면 최상위 인텐트를 일부러 낮춰보내 '3중 관문이 막는지' 시험한다.
#   - OllamaLLM      : [REAL] 실제 Ollama 어댑터 (openai SDK). 도메인과 무관한 순수 어댑터.
# ════════════════════════════════════════════════════════════════════
import json

from . import s1_classifier as classifier
from . import s1_domain as domain


class LLMClient:
    def chat(self, messages: list[dict], json_mode: bool = False,
             tools: list | None = None, temperature: float = 0.0) -> str:
        """messages: [{role, content}...], tools: OpenAI 형식. → 응답 문자열"""
        raise NotImplementedError


def intent_enum_txt() -> str:
    """인텐트 enum 문자열 ('a,b,c') — 프롬프트 계약이 사용."""
    return ",".join(domain.intent_names())


def build_ticket_prompt(text: str) -> str:
    """티켓 작성 프롬프트 = 계약서 본문 + '문의: ' + 원문 (문자열 연결 조립)."""
    return _TICKET_PROMPT() + text


def _TICKET_PROMPT() -> str:
    enums = intent_enum_txt() or "<BASE_PRIORITY 를 채워야 함>"
    pri = ",".join(domain.P_ORDER or ["P1", "P2", "P3"])
    return (
        "고객 문의를 다음 JSON 객체 하나로 요약하라. 추가 설명 금지, JSON 만 출력.\n"
        "필드:\n"
        "- intent: " + enums + " 중 하나\n"
        "- priority: " + pri + " 중 하나. " + (f"({', '.join(sorted(domain.MUST_TOP_PRIORITY))} 는 반드시 최상위.)"
                                                if domain.MUST_TOP_PRIORITY else "(최상위 인텐트 강제 항목 추가 가능)") + "\n"
        "- summary: 한 문장 요약\n"
        "- escalate: priority 가 최상위이면 true, 아니면 false\n"
        "문의: "
    )


class MockTicketLLM(LLMClient):
    """정직한 모의 — 도메인 규칙을 근거로 티켓 JSON 을 '작성한다'(거짓말을 못 함)."""

    def __init__(self, faulty: bool = False) -> None:
        self.faulty = faulty

    def chat(self, messages: list[dict], **kw: object) -> str:
        last = [m for m in messages if m.get("role") == "user"][-1]["content"]
        # 프롬프트 계약의 '문의: ' 뒤 원문만 사용 (프롬프트 본문 단어가 분류를 오염시키지 않게)
        text = last.rsplit("문의: ", 1)[-1] if "문의: " in last else last
        intent = classifier.rule_intent(text)
        if intent in ("unknown", "unknown_not_filled"):
            return json.dumps({"intent": "unknown", "priority": "P3",
                               "summary": text[:24], "escalate": False}, ensure_ascii=False)
        if self.faulty and intent in domain.MUST_TOP_PRIORITY:
            # 최상위 인텐트를 일부러 낮춰내려 3중 관문이 막는지 시험
            return json.dumps({"intent": intent, "priority": domain.P_ORDER[-1],
                               "summary": "고위험 문의", "escalate": False}, ensure_ascii=False)
        priority = classifier.classify_with_priority(text, intent)
        top = domain.MUST_TOP_PRIORITY and intent in domain.MUST_TOP_PRIORITY
        return json.dumps({"intent": intent, "priority": priority,
                           "summary": text[:24],
                           "escalate": (top or priority == domain.P_ORDER[0])},
                          ensure_ascii=False)


## [REAL · 선택] 실제 Ollama 어댑터 — openai SDK 로 Ollama 를 향함 (도메인 무관)
# class OllamaLLM(LLMClient):
#     def __init__(self, model: str = "qwen2.5:32b") -> None:
#         self.model = model
#     def chat(self, messages, json_mode=False, tools=None, temperature=0.0) -> str:
#         from openai import OpenAI
#         c = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")
#         kwargs = {"model": self.model, "messages": messages, "temperature": temperature}
#         if json_mode:
#             kwargs["response_format"] = {"type": "json_object"}
#         if tools:
#             kwargs["tools"] = tools
#         r = c.chat.completions.create(**kwargs)
#         return r.choices[0].message.content
