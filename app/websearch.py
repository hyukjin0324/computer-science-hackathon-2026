"""공고 페이지를 직접 못 읽을 때(화면을 스크립트로 그리는 사이트 등) AI 웹 검색으로 대신 찾는다.

- 보내는 것: 대회 이름과 공고 주소뿐. 공고 본문이나 저장소 내용은 보내지 않는다.
- 돌려주는 글은 검색 결과에서 Claude가 '인용한 출처 문장'만 모은 것이다. 그래서 다음 단계(공고 분석)의
  원문 인용 대조가 그대로 동작한다. AI가 지어낸 문장은 들어가지 않는다.
- 학교(knu.ac.kr) 글처럼 AI에 보내지 않기로 한 출처는 검색하지 않는다.
- 같은 주소는 하루에 한 번만 찾고, 하루 전체·사람별 횟수를 막는다 (검색 한 번에 비용이 든다).
"""

import json
import os
import threading
import time
from urllib.parse import urlparse

import anthropic

MODEL = "claude-haiku-5-5"
PRICE = (0.10, 0.50)  # USD / 100만 토큰
SEARCH_USD = 0.01  # 검색 1번
MAX_SEARCHES = 3
PER_HOUR = 10
PER_DAY_ALL = 200
CACHE_TTL = 24 * 3600
NO_AI_HOSTS = ("knu.ac.kr",)

USAGE = {"calls": 0, "searches": 0, "est_usd": 0.0}
_lock = threading.Lock()
_cache: dict[str, tuple[float, dict]] = {}
_hits: dict[str, list[float]] = {}
_day = {"key": "", "n": 0}

PROMPT = """아래 대회(해커톤·공모전) 공고의 내용을 웹 검색으로 찾아 주세요.
대회: {title}
공고 주소: {url}

아래 항목마다 따로 답하고, 항목마다 그 내용이 적힌 출처 문장을 꼭 인용하세요.
1. 참가 자격 (누가 참가할 수 있는지)
2. 팀 인원
3. 신청(접수) 기간과 마감 날짜·시간
4. 제출 마감 날짜·시간
5. 제출물
- 이 대회에 대한 내용만 쓰고, 다른 대회와 섞지 마세요.
- 보도자료나 대회 페이지처럼 원문에 가까운 출처를 먼저 찾으세요.
- 찾지 못한 항목은 '못 찾음'이라고만 쓰세요."""

FOCUS = """'{title}' 대회의 {missing}을(를) 찾아 주세요. 공고 주소: {url}
"{title} {query}"처럼 검색하고, 찾은 내용은 출처 문장을 꼭 인용해서 짧게 답하세요. 다른 대회와 섞지 마세요."""

# 인용 글에 이 낱말들이 있어야 '그 항목을 찾았다'고 본다
NEED = {"참가 자격": ("누구나", "자격", "대상", "재학", "학생", "일반인", "참가 가능", "참여할 수"),
        "신청 마감": ("마감", "접수", "신청 기간", "까지")}


class SearchError(Exception):
    pass


def ready() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def allowed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return bool(host) and not any(host == h or host.endswith("." + h) for h in NO_AI_HOSTS)


def _allow(who: str, now: float) -> None:
    from .limits import take
    take(_lock, _hits, _day, who, now, PER_HOUR, PER_DAY_ALL,
         SearchError("오늘 쓸 수 있는 공고 검색을 다 썼어요"),
         SearchError("공고 검색을 너무 자주 했어요. 조금 뒤에 다시 해 주세요"))


GENERIC = {"ai", "개발", "챌린지", "해커톤", "공모전", "대회", "활용", "경진대회", "2026", "2025", "제", "회", "the", "and", "of"}


def keywords(title: str) -> list[str]:
    """대회 이름에서 다른 대회와 구분되는 낱말 (예: '제주', '빅데이터')."""
    import re
    words = re.split(r"[\s·・,()\[\]「」'\"‘’“”/\-]+", title or "")
    return [w for w in words if len(w) >= 2 and w.lower() not in GENERIC]


def related(src: dict, kws: list[str], notice_url: str) -> bool:
    if src["url"].rstrip("/") == notice_url.rstrip("/") or not kws:
        return True
    blob = (src["title"] + " " + " ".join(src["texts"])).lower()
    return any(k.lower() in blob for k in kws)


LABELS = [("참가 자격", r"참가\s*자격|참여\s*자격|참가\s*대상|자격"), ("팀 인원", r"팀\s*인원|인원|팀\s*구성"),
          ("신청 마감", r"신청|접수"), ("제출 마감", r"제출\s*마감|제출\s*기한|제출\s*기간"), ("제출물", r"제출물|산출물|낼\s*것")]
UTC_NOTE = "※ 대회 플랫폼 페이지 요약의 시각은 UTC(한국 시간보다 9시간 이름)로 보일 수 있어요. '오전 10시'처럼 한국 시간이 적힌 출처가 있으면 그 시간을 따르세요."


def _label(text: str, cur: str | None) -> str | None:
    """답 글에서 지금 어느 항목을 말하는 중인지 (번호 머리말 기준)."""
    import re
    for line in text.splitlines():
        head = line.strip()[:30]
        for name, pat in LABELS:
            if re.search(pat, head):
                cur = name
                break
    return cur


def corpus_from(resps, title: str, notice_url: str = "") -> tuple[str, list[dict]]:
    """응답들의 인용(citations)만 모아 출처별 글로 만든다. 이 대회와 상관없는 출처는 뺀다.

    인용 문장 앞에는 Claude가 그 문장을 어느 항목의 근거로 댔는지 '[참가 자격]'처럼 붙인다.
    문장 자체는 출처 원문 그대로라서, 다음 단계의 원문 인용 대조가 그대로 동작한다.
    """
    if not isinstance(resps, (list, tuple)):
        resps = [resps]
    by_url: dict[str, dict] = {}
    for resp in resps:
        cur = None
        for b in resp.content:
            if getattr(b, "type", None) != "text":
                continue
            cur = _label(getattr(b, "text", "") or "", cur)
            for c in getattr(b, "citations", None) or []:
                url, txt = getattr(c, "url", None), (getattr(c, "cited_text", None) or "").strip()
                if not url or not txt:
                    continue
                src = by_url.setdefault(url, {"url": url, "title": getattr(c, "title", None) or url, "texts": []})
                line = f"[{cur}] {txt}" if cur else txt
                if txt not in " ".join(src["texts"]):
                    src["texts"].append(line)
    kws = keywords(title)
    by_url = {u: v for u, v in by_url.items() if related(v, kws, notice_url)}
    if not by_url:
        return "", []
    parts = [title] if title else []
    parts.append(UTC_NOTE)
    for s in by_url.values():
        parts.append(f"[출처: {s['title']}]")
        parts.extend(s["texts"])
    return "\n".join(parts), [{"url": s["url"], "title": s["title"]} for s in by_url.values()]


def missing(text: str) -> list[str]:
    """인용 글에서 아직 못 찾은 핵심 항목 (참가 자격 · 신청 마감)."""
    body = "\n".join(line for line in text.splitlines()[1:] if not line.startswith("※"))
    return [k for k, words in NEED.items() if not any(w in body for w in words)]


def _ask(client, prompt: str) -> object:
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=2000,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": MAX_SEARCHES}],
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIError as e:
        raise SearchError("AI 웹 검색이 잠깐 안 돼요. 조금 뒤에 다시 해 주세요") from e
    u = resp.usage
    n = getattr(getattr(u, "server_tool_use", None), "web_search_requests", 0) or 0
    usd = round((u.input_tokens or 0) / 1e6 * PRICE[0] + (u.output_tokens or 0) / 1e6 * PRICE[1] + n * SEARCH_USD, 5)
    with _lock:
        USAGE["calls"] += 1
        USAGE["searches"] += n
        USAGE["est_usd"] = round(USAGE["est_usd"] + usd, 5)
    print(json.dumps({"event": "bento_ai_usage", "feature": "notice_search", "model": resp.model, "searches": n, "est_usd": usd}), flush=True)
    return resp


def search_notice(url: str, title: str | None, who: str = "anon", client=None) -> dict | None:
    """찾으면 {text, sources, missing}, 인용이 하나도 없으면 None.

    참가 자격이나 신청 마감이 빠졌으면 그 항목만 한 번 더 찾는다. 둘 다 찾은 결과만 하루 동안 기억한다
    (덜 찾은 결과는 기억하지 않아서 '공고 다시 읽기'로 다시 찾을 수 있다).
    """
    title = (title or "").strip()[:120]
    hit = _cache.get(url)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    if client is None and not ready():
        return None
    _allow(who, time.time())
    client = client or anthropic.Anthropic(timeout=90, max_retries=1)
    name = title or "(이름 모름)"
    resps = [_ask(client, PROMPT.format(title=name, url=url))]
    text, sources = corpus_from(resps, title, url)
    lack = missing(text) if text else list(NEED)
    if lack:
        query = " ".join({"참가 자격": "참가 자격 대상", "신청 마감": "접수 기간 마감"}[k] for k in lack)
        resps.append(_ask(client, FOCUS.format(title=name, url=url, missing="·".join(lack), query=query)))
        text, sources = corpus_from(resps, title, url)
        lack = missing(text) if text else list(NEED)
    if not text:
        return None
    out = {"text": text, "sources": sources, "missing": lack}
    if not lack:
        _cache[url] = (time.time(), out)
    return out
