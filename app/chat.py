"""대화창 자유 대화: 정해진 낱말로 못 알아들은 말만 Claude에게 묻는다.

- 보내는 것: 사용자가 대화창에 직접 쓴 말과, 이 자유 대화에서 AI가 했던 답만. 공고·저장소 내용은 보내지 않는다.
- 받는 것: 짧은 답 + Bento 기능 하나(없으면 none). 기능은 화면에서 버튼으로 보여 주고, 사용자가 눌러야 실행된다.
- 키(ANTHROPIC_API_KEY)가 없으면 부르지 않는다. 비용이 새지 않게 사람별·하루 전체 횟수를 막는다.
"""

import json
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
- today: 오늘 할 일·리뷰·담당 정하기 보기
- progress: 계획 대비 늦은 일 점검, 다시 계획
- submit: 제출 전 빠진 것 점검
규칙:
- 한국어 존댓말로 1~3문장, 쉬운 말로 짧게 답한다.
- 사용자의 말이 위 기능 중 하나와 맞으면 action에 그 이름을, 아니면 none을 넣는다.
- Bento는 머지·푸시·코드 수정을 하지 않고, 모든 실행은 사람이 승인한다. 이걸 넘는 일을 해 주겠다고 말하지 않는다.
- 대회 일정·상금 같은 사실을 지어내지 않는다. 모르면 공고를 붙여 넣거나 대회 찾기를 써 보라고 안내한다.
- 비밀번호·API 키·개인정보를 달라고 하지 않는다."""

SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "action": {"type": "string", "enum": list(ACTIONS)},
    },
    "required": ["reply", "action"],
    "additionalProperties": False,
}

_lock = threading.Lock()
_hits: dict[str, list[float]] = {}
_day = {"key": "", "n": 0}


class ChatError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def ready() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def _allow(who: str, now: float) -> None:
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    with _lock:
        if _day["key"] != day:
            _day.update(key=day, n=0)
        if _day["n"] >= PER_DAY_ALL:
            raise ChatError("오늘 쓸 수 있는 AI 대화를 다 썼어요. 내일 다시 써 주세요", 429)
        recent = [t for t in _hits.get(who, []) if now - t < 3600]
        if len(recent) >= PER_HOUR:
            raise ChatError("AI 대화를 너무 자주 썼어요. 조금 뒤에 다시 해 주세요", 429)
        recent.append(now)
        _hits[who] = recent
        _day["n"] += 1


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


def ask(message: str, history=None, who: str = "anon", client=None) -> dict:
    msg = (message or "").strip()
    if not msg:
        raise ChatError("할 말을 적어 주세요")
    if len(msg) > MAX_LEN:
        raise ChatError(f"{MAX_LEN}자 안으로 적어 주세요")
    if not ready() and client is None:
        return {"ai": False}
    _allow(who, time.time())
    client = client or anthropic.Anthropic()
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM,
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
    print(json.dumps({"event": "bento_ai_usage", "feature": "chat", "model": resp.model, "input_tokens": inp, "output_tokens": out, "est_usd": usd}), flush=True)
    if resp.stop_reason == "refusal":
        return {"ai": True, "reply": "그 이야기는 도와 드리기 어려워요. 대회 찾기나 계획 짜기처럼 팀플 일을 물어봐 주세요.", "action": "none"}
    if resp.stop_reason == "max_tokens":
        raise ChatError("답이 길어져서 끊겼어요. 짧게 다시 물어봐 주세요", 503)
    raw = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ChatError("AI 답을 읽지 못했어요. 다시 해 주세요", 503) from e
    action = data.get("action") if data.get("action") in ACTIONS else "none"
    reply = str(data.get("reply") or "").strip()[:600] or "무슨 뜻인지 잘 모르겠어요. 조금만 더 알려 주세요."
    return {"ai": True, "reply": reply, "action": action, "label": ACTIONS[action]}
