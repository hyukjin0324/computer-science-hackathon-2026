"""대화창 자유 대화: 정해진 낱말로 못 알아들은 말만 Claude에게 묻는다.

- 보내는 것: 사용자가 대화창에 직접 쓴 말과, 이 자유 대화에서 AI가 했던 답, 그리고 팀플 상황 요약(지금 단계·마감까지 남은 날수·늦어진 일 개수)만. 공고·저장소 내용과 이름은 보내지 않는다.
- 받는 것: 짧은 답 + Bento 기능 하나(없으면 none). 기능은 화면에서 버튼으로 보여 주고, 사용자가 눌러야 실행된다.
- 키(ANTHROPIC_API_KEY)가 없으면 부르지 않는다. 비용이 새지 않게 사람별·하루 전체 횟수를 막는다.
"""

import json
import re
import os
import threading
import time

import anthropic

MODEL = "claude-haiku-5-5"  # 짧은 안내·기능 고르기라 빠르고 싼 모델
PRICE = (0.10, 0.50)  # USD / 100만 토큰 (입력, 출력)
MAX_LEN = 500
PER_HOUR = 30
PER_DAY_ALL = 500
USAGE = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "est_usd": 0.0}

ACTIONS = {
    "none": None,
    "discover": "대회 찾기",
    "notice": "공고 읽기",
    "apply": "신청하기",
    "plan": "계획 짜기",
    "today": "오늘 할 일 보기",
    "progress": "진행 점검",
    "submit": "제출 점검",
}

SYSTEM = """너는 대학생 팀 프로젝트(해커톤·공모전)를 돕는 앱 Bento의 대화 도우미다.
Bento가 할 수 있는 일:
- discover: 나갈 만한 대회·공모전 찾기
- notice: 공고 주소나 글을 읽고 마감·자격·제출물 정리
- apply: 참가 자격 확인 후 신청 준비 (신청 버튼은 사용자가 직접 누른다)
- plan: 마감에 맞춰 단계별 계획 짜기, GitHub 할 일로 만들기
- today: 오늘 할 일·리뷰·담당 정하기 보기, 연락이 끊겼거나 맡은 일을 안 하는 팀원 챙기기(안부 댓글)
- progress: 계획 대비 늦은 일 점검, 다시 계획
- submit: 제출 전 빠진 것 점검
kind(말의 종류)를 먼저 정한다:
- do: 사용자가 Bento 기능을 해 달라고 요청한 말("~해 줘", "~할래", "~보여 줘", "~찾아 줘" 등). 이때 action은 반드시 그 기능이고 none이 아니다. 화면이 설명 없이 바로 그 기능을 연다. reply는 아주 짧게 한 문장.
- ask: 팀플·대회와 관련된 질문이나 상담. 아래 규칙대로 짧게 답한다.
- offtopic: 팀플·대회·Bento와 관계없는 말(날씨, 잡담, 코딩 질문, 상식 질문 등). action은 none이고, reply는 쓰지 않아도 된다(화면이 정해진 안내를 보여 준다). 이런 말에 답을 지어내거나 이야기를 이어 가지 않는다.
규칙:
- 한국어 존댓말로 1~3문장, 쉬운 말로 짧게, 대화하듯 답한다. 물은 것에 먼저 바로 답한다.
- 먼저 action을 하나 고른다. 지금 사용자에게 가장 먼저 필요한 기능 하나이고, 맞는 게 없으면 none이다.
- 답 아래에 그 action이 버튼 하나로 붙는다. 그래서 reply에서 권하는 기능은 고른 action 하나뿐이어야 한다.
  다른 기능 이름(대회 찾기, 공고 읽기, 신청, 계획 짜기, 오늘 할 일, 진행 점검, 제출 점검)은 reply에 쓰지 않는다.
- 버튼은 화면이 답 아래에 붙인다. '~을 눌러 보세요'를 매번 쓰지 않는다. none이면 기능을 권하지 않는다.
- Bento 동작: 팀원이 한 명이면 할 일은 모두 그 사람 몫이고, 팀원이 들어오면 역할·맡은 일 수에 맞춰 담당을 나눠 준다(나누기 = 담당자 정하기, 승인해야 실행). 팀 전체 변경은 팀장이나 다른 팀원이 한 번 더 승인한다.
- Bento는 머지·푸시·코드 수정을 하지 않고, 모든 실행은 사람이 승인한다. 이걸 넘는 일을 해 주겠다고 말하지 않는다.
- 대회 일정·상금 같은 사실을 지어내지 않는다. 모르면 공고를 붙여 넣거나 대회 찾기를 써 보라고 안내한다(공고가 이미 연결돼 있으면 다시 달라고 하지 않는다).
- 비밀번호·API 키·개인정보를 달라고 하지 않는다."""

STAGES = ("대회 고르기 전", "신청 전", "신청 완료", "협업 중", "제출 완료")
CONTEXT_RULE = """
- [팀 상황]이 주어지면 답에 자연스럽게 활용한다(예: 마감이 가까우면 서두르자고, 늦어진 일이 있으면 먼저 챙기자고). 상황에 없는 대회 이름·일정·팀원은 지어내지 않는다.
- 상황을 그대로 읊지 말고, 사용자가 물은 것에 먼저 답한 뒤 필요할 때만 덧붙인다. 마감(D-몇)은 일정·마감을 물었거나 3일 안일 때만 말한다. 날짜를 물으면 '제출 마감 날짜'로 정확히 답한다.
- [방금 화면]은 바로 전에 화면에 오간 말이다(Bento 말 포함). '그때', '아까', '그거', '무슨 말이야'는 여기서 찾아 풀어서 설명한다.
- 단계별로 권할 기능: 대회 고르기 전은 discover, 신청 전은 apply, 신청 완료·협업 중은 today나 progress(마감이 가깝거나 늦어진 일이 있으면 progress), 제출 완료는 수고했다고 말하고 새 대회를 찾는 discover. 이미 마친 단계의 기능(예: 제출을 마쳤는데 submit)은 권하지 않는다."""

SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["do", "ask", "offtopic"]},  # 말의 종류를 먼저 정한다
        "action": {"type": "string", "enum": list(ACTIONS)},  # 그다음 기능을 고르고, 답은 그 기능에 맞춰 쓴다
        "reply": {"type": "string"},
    },
    "required": ["kind", "action", "reply"],
    "additionalProperties": False,
}

_lock = threading.Lock()
_hits: dict[str, list[float]] = {}
_day = {"key": "", "n": 0}


OFFTOPIC = "저는 대회 준비를 돕는 AI라 이 요청은 도와드리기 어려워요. 대회 찾기, 팀 일정·할 일 관리, 제출 준비와 관련된 질문을 해 주세요."


class ChatError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# 답에서 기능을 부르는 말 → 그 기능. 답이 권한 기능과 버튼이 어긋나지 않게 맞춘다.
MENTION = [
    ("discover", ("대회 찾기", "대회를 찾", "공모전을 찾", "대회 찾아")),
    ("notice", ("공고 읽기", "공고를 읽", "공고를 붙여", "공고 주소")),
    ("apply", ("신청하기", "신청 준비", "신청해")),
    ("plan", ("계획 짜기", "계획을 짜", "계획을 세", "할 일을 나누")),
    ("today", ("오늘 할 일",)),
    ("progress", ("진행 점검", "진행을 점검")),
    ("submit", ("제출 점검", "제출 전 점검", "제출 전에 점검")),
]


def align(reply: str, action: str) -> str:
    """답이 기능을 딱 하나 권했는데 버튼과 다르면, 답이 권한 기능으로 버튼을 바꾼다.
    여러 기능을 말했으면 고른 버튼이 그중 하나일 때만 두고, 아니면 버튼을 붙이지 않는다."""
    if action == "none":
        return "none"  # '계획 짜기 등을 도와요' 같은 자기소개에는 버튼을 붙이지 않는다
    said = [a for a, words in MENTION if any(w in reply for w in words)]
    if not said or action in said:
        return action  # 기능 이름을 안 썼거나, 쓴 것 중에 버튼이 있으면 그대로
    return said[0] if len(said) == 1 else "none"


def ready() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def _allow(who: str, now: float) -> None:
    from .limits import take
    take(_lock, _hits, _day, who, now, PER_HOUR, PER_DAY_ALL,
         ChatError("오늘 쓸 수 있는 AI 대화를 다 썼어요. 내일 다시 써 주세요", 429),
         ChatError("AI 대화를 너무 자주 썼어요. 조금 뒤에 다시 해 주세요", 429))


def _days(v) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and -400 <= v <= 400 else None


def situation(ctx) -> str:
    """화면에서 보낸 팀 상황을 허용된 값만 골라 한 줄로 만든다. 모르는 값·문자열은 버린다."""
    if not isinstance(ctx, dict) or ctx.get("stage") not in STAGES:
        return ""
    parts = [f"지금 단계: {ctx['stage']}"]
    for key, label in (("apply_days", "신청 마감"), ("submit_days", "제출 마감")):
        d = _days(ctx.get(key))
        if d is not None:
            parts.append(f"{label} {'D-' + str(d) if d > 0 else 'D-day' if d == 0 else str(-d) + '일 지남'}")
    late = _days(ctx.get("late"))
    if late:
        parts.append(f"늦어진 일 {late}개")
    due = ctx.get("submit_deadline")  # '12월 1일(화) 23:59' 꼴만 받는다 (날짜를 물으면 D-day 대신 정확히 답하게)
    if isinstance(due, str) and re.fullmatch(r"\d{1,2}월 \d{1,2}일\([월화수목금토일]\) \d{2}:\d{2}", due):
        parts.append(f"제출 마감 날짜 {due}")
    n = ctx.get("members")
    if isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= 30:
        parts.append(f"팀원 {n}명")
    if ctx.get("notice") is True:
        parts.append("공고 연결됨")
    out = "[팀 상황] " + ", ".join(parts)
    # 방금 화면에 오간 말(Bento 말 포함). 토큰을 아끼려고 최대 4줄, 줄마다 120자, 줄바꿈은 지운다
    lines = []
    for r in (ctx.get("recent") or [])[-4:] if isinstance(ctx.get("recent"), list) else []:
        if isinstance(r, dict) and r.get("w") in ("b", "m") and isinstance(r.get("t"), str) and r["t"].strip():
            lines.append(("Bento: " if r["w"] == "b" else "나: ") + " ".join(r["t"].split())[:120])
    if lines:
        out += "\n[방금 화면] " + " / ".join(lines)
    return out


def _history(hist) -> list[dict]:
    out = []
    for h in (hist or [])[-6:]:
        role = h.get("role") if isinstance(h, dict) else None
        text = str(h.get("text") or "")[:MAX_LEN] if isinstance(h, dict) else ""
        if role in ("user", "assistant") and text.strip():
            if out and out[-1]["role"] == role:
                out[-1]["content"] += "\n" + text
            else:
                out.append({"role": role, "content": text})
    while out and out[0]["role"] != "user":
        out.pop(0)
    while out and out[-1]["role"] == "user":  # 마지막은 이번 말이 들어갈 자리
        out.pop()
    return out


def ask(message: str, history=None, who: str = "anon", client=None, context=None) -> dict:
    msg = (message or "").strip()
    if not msg:
        raise ChatError("할 말을 적어 주세요")
    if len(msg) > MAX_LEN:
        raise ChatError(f"{MAX_LEN}자 안으로 적어 주세요")
    if not ready() and client is None:
        return {"ai": False}
    _allow(who, time.time())
    client = client or anthropic.Anthropic(timeout=30, max_retries=1)  # 응답이 늦으면 서버를 오래 붙잡지 않는다
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=400,  # 답은 1~3문장 JSON이라 짧게 (토큰·비용 아끼기)
            system=SYSTEM + (CONTEXT_RULE + "\n" + sit if (sit := situation(context)) else ""),
            thinking={"type": "disabled"},
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=_history(history) + [{"role": "user", "content": msg}],
        )
    except anthropic.RateLimitError as e:
        raise ChatError("AI 요청이 몰려 있어요. 잠시 뒤에 다시 해 주세요", 503) from e
    except anthropic.APIStatusError as e:
        raise ChatError(f"AI 서버 오류예요 ({e.status_code}). 잠시 뒤에 다시 해 주세요", 503) from e
    except anthropic.APIConnectionError as e:
        raise ChatError("AI 서버에 연결하지 못했어요", 503) from e
    u = resp.usage
    inp, out = u.input_tokens or 0, u.output_tokens or 0
    usd = round(inp / 1e6 * PRICE[0] + out / 1e6 * PRICE[1], 6)
    with _lock:
        USAGE["calls"] += 1
        USAGE["input_tokens"] += inp
        USAGE["output_tokens"] += out
        USAGE["est_usd"] = round(USAGE["est_usd"] + usd, 6)
    usage = {"input_tokens": inp, "output_tokens": out, "est_usd": usd}
    print(json.dumps({"event": "bento_ai_usage", "feature": "chat", "model": resp.model, "input_tokens": inp, "output_tokens": out, "est_usd": usd}), flush=True)
    if resp.stop_reason == "refusal":
        return {"ai": True, "reply": "그 이야기는 도와 드리기 어려워요. 대회 찾기나 계획 짜기처럼 팀플 일을 물어봐 주세요.", "action": "none", "usage": usage}
    if resp.stop_reason == "max_tokens":
        raise ChatError("답이 길어져서 끊겼어요. 짧게 다시 물어봐 주세요", 503)
    raw = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ChatError("AI 답을 읽지 못했어요. 다시 해 주세요", 503) from e
    action = data.get("action") if data.get("action") in ACTIONS else "none"
    kind = data.get("kind") if data.get("kind") in ("do", "ask", "offtopic") else "ask"
    if kind == "offtopic":  # 관련 없는 말: 지어낸 답 대신 정해진 안내 한 줄
        return {"ai": True, "kind": "offtopic", "reply": OFFTOPIC, "action": "none", "label": None, "usage": usage}
    reply = str(data.get("reply") or "").strip()[:600] or "무슨 뜻인지 잘 모르겠어요. 조금만 더 알려 주세요."
    if kind == "do" and action != "none":  # 요청: 화면이 설명 없이 바로 그 기능을 연다 (실행·승인은 그 기능 안에서 사람이 한다)
        return {"ai": True, "kind": "do", "reply": reply, "action": action, "label": ACTIONS[action], "usage": usage}
    action = align(reply, action)
    return {"ai": True, "kind": "ask", "reply": reply, "action": action, "label": ACTIONS[action], "usage": usage}
