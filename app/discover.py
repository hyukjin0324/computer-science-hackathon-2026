"""대회를 먼저 찾아온다: 학과 게시판과 공개 행사 목록에서 해커톤·공모전을 모아 마감순으로 고른다.

예의 지키기
- Bento라고 솔직하게 밝힌다 (User-Agent).
- 같은 사이트에는 요청 사이 10초 이상 (학과 사이트 robots.txt의 Crawl-delay), 목록은 1시간에 한 번만 읽는다.
- 목록 페이지만 읽는다. 글 본문은 사용자가 '신청 준비'를 눌렀을 때 한 번만 연다.
- 학과 사이트는 AI 크롤러를 막아 두었다. 그래서 그곳 글은 AI(Claude)에 보내지 않고 규칙으로만 읽는다.
상업 사이트(링커리어·사람인 등)는 약관을 확인하기 전까지 자동으로 모으지 않는다. 링크를 붙여넣으면 분석한다.
"""

import html as htmllib
import os
import re
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse

import httpx

from .fetch import FetchError, _check_host
from .rules import KST, _dates, human

UA = "Bento/1.0 (student team-project assistant; rule-based reader, no AI training)"
CACHE_TTL = 3600
HOST_GAP = 10  # 초

SOURCES = [
    {"id": "knu-notice", "name": "컴퓨터학부 공지", "kind": "gnuboard", "ai": False,
     "url": "https://aicollege.knu.ac.kr/bbs/board.php?bo_table=sub6_1_a&lang=kor"},
    {"id": "knu-event", "name": "컴퓨터학부 세미나·행사", "kind": "gnuboard", "ai": False,
     "url": "https://aicollege.knu.ac.kr/bbs/board.php?bo_table=sub6_4&lang=kor"},
    {"id": "dev-event", "name": "Dev-Event (개발자 행사 모음)", "kind": "devevent", "ai": True,
     "url": "https://raw.githubusercontent.com/brave-people/Dev-Event/master/README.md",
     "home": "https://github.com/brave-people/Dev-Event"},
]
NOT_AUTO = [
    {"name": "링커리어", "why": "상업 사이트라 약관을 확인하기 전까지 자동으로 모으지 않아요. 공고 링크를 붙여넣으면 분석해요"},
    {"name": "사람인", "why": "채용 공고는 공식 오픈 API(키 신청)로 연결할 수 있어요. 공모전은 링크를 붙여넣어 주세요"},
    {"name": "로켓펀치", "why": "주로 스타트업 채용이라 대회 찾기에는 넣지 않았어요"},
]

CONTEST = re.compile(r"해커톤|공모전|경진|대회|챌린지|hackathon|contest|challenge|아이디어톤|ideathon", re.I)
HACK = re.compile(r"해커톤|hackathon|아이디어톤", re.I)

_CACHE: dict = {"at": 0.0, "items": [], "errors": {}}
_SRC: dict[str, dict] = {}  # 출처마다 따로 기억한다: {"at", "items"}


class TooSoon(Exception):
    """같은 사이트를 10초 안에 다시 부르려 할 때. 기다리지 않고 다음 요청으로 미룬다 (서버리스용)."""
_LAST_HIT: dict[str, float] = {}


def _get(url: str, client: httpx.Client | None = None, sleep=time.sleep) -> str:
    """같은 사이트에 10초 안에 다시 가지 않는다."""
    _check_host(url)
    host = urlparse(url).hostname or ""
    wait = HOST_GAP - (time.time() - _LAST_HIT.get(host, 0))
    if wait > 0:
        if sleep is None:
            raise TooSoon(host)
        sleep(wait)
    own = client is None
    client = client or httpx.Client(timeout=12, headers={"User-Agent": UA}, follow_redirects=False)
    try:
        r = client.get(url)
        _LAST_HIT[host] = time.time()
        if r.status_code >= 400:
            raise FetchError(f"{host} 응답 {r.status_code}")
        return r.text
    except httpx.HTTPError as e:
        raise FetchError(f"{host}에 연결하지 못했어요") from e
    finally:
        if own:
            client.close()


polite_get = _get  # 테스트에서 _get을 바꿔 끼워도 원래 함수를 확인할 수 있게


def _text(s: str) -> str:
    return " ".join(htmllib.unescape(re.sub(r"<[^>]+>", " ", s)).split())


def _deadline_in(text: str, now: datetime) -> datetime | None:
    """'(~11.30(월))', '(~ 11.20(금))', '접수: 10. 01 ~ 11. 12 10:00' 같은 데서 마감을 찾는다."""
    tail = text.split("~", 1)[1] if "~" in text else ""
    ds = _dates(tail, now) if tail else []
    return max(ds) if ds else None


def parse_gnuboard(page: str, source: dict, now: datetime) -> list[dict]:
    body = page[page.find("<tbody>"):page.find("</tbody>")]
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S):
        t = re.search(r'<div class="bo_tit">\s*<a href="([^"]+)"[^>]*>(.*?)</a>', row, re.S)
        if not t:
            continue
        title = _text(t.group(2))
        posted = re.search(r"td_datetime[^\"]*\">\s*([\d-]{10})", row)
        out.append({"title": title, "url": htmllib.unescape(t.group(1)), "posted": posted.group(1) if posted else None,
                    "deadline": _deadline_in(title, now), "tags": [], "host": None})
    return out


def parse_devevent(md: str, now: datetime) -> list[dict]:
    out, year, month = [], now.year, None
    md = md.split("## 지난 행사 기록")[0]
    cur = None
    for line in md.splitlines():
        if m := re.match(r"^## `(\d{2})년 (\d{1,2})월`", line):
            year, month = 2000 + int(m.group(1)), int(m.group(2))
            continue
        if month is None:
            continue
        if m := re.match(r"^- __\[(.+?)\]\((https?://[^)]+)\)__", line):
            cur = {"title": m.group(1).strip(), "url": m.group(2), "posted": None, "deadline": None, "tags": [], "host": None}
            out.append(cur)
        elif cur and (m := re.match(r"^\s+- 분류: (.+)", line)):
            cur["tags"] = re.findall(r"`([^`]+)`", m.group(1))
        elif cur and (m := re.match(r"^\s+- 주최: (.+)", line)):
            cur["host"] = m.group(1).strip()
        elif cur and (m := re.match(r"^\s+- 접수: (.+)", line)):
            base = now.replace(year=year) if year != now.year else now
            cur["deadline"] = _deadline_in(m.group(1), base)
    return out


def score(item: dict, source: dict, interests: list[str], now: datetime) -> tuple[int, list[str]]:
    why, s = [], 0
    text = item["title"] + " " + " ".join(item["tags"])
    if HACK.search(text):
        s += 3; why.append("해커톤")
    elif "대회" in item["tags"] or CONTEST.search(item["title"]):
        s += 2; why.append("공모전·대회")
    if source["id"].startswith("knu"):
        s += 2; why.append("우리 학과 공지")
    hits = [k for k in interests if k and re.search((r"\b" + re.escape(k) + r"\b") if k.isascii() else re.escape(k), text, re.I)]
    if hits:
        s += len(hits); why.append("관심 분야: " + ", ".join(hits))
    if item["deadline"]:
        days = (item["deadline"] - now).days
        if days <= 7:
            s += 1; why.append(f"마감 {max(days, 0)}일 남음")
    if any(t.startswith("온라인") or "대구" in t or "경북" in t for t in item["tags"]):
        s += 1; why.append("온라인·가까운 곳")
    return s, why


def _contest_like(item: dict) -> bool:
    return "대회" in item["tags"] or bool(CONTEST.search(item["title"]))


def refresh(fetch=None, now: datetime | None = None, force: bool = False, wait: bool | None = None) -> dict:
    """오래된 출처만 다시 읽는다. wait=False(서버리스)면 같은 사이트 10초 간격을 기다리지 않고 다음 요청으로 미룬다."""
    now = now or datetime.now(KST)
    if wait is None:
        wait = not os.getenv("VERCEL")
    errors, pending = {}, []
    for src in SOURCES:
        old = _SRC.get(src["id"])
        if old and not force and time.time() - old["at"] < CACHE_TTL:
            continue
        try:
            page = fetch(src["url"]) if fetch else _get(src["url"], sleep=time.sleep if wait else None)
            found = parse_gnuboard(page, src, now) if src["kind"] == "gnuboard" else parse_devevent(page, now)
        except TooSoon:
            pending.append(src["id"])
            continue
        except (FetchError, ValueError) as e:
            errors[src["id"]] = str(e)
            continue
        for it in found:
            it.update(source=src["id"], source_name=src["name"], ai_ok=src["ai"])
        _SRC[src["id"]] = {"at": time.time(), "items": found}
    items = [it for s_ in SOURCES for it in _SRC.get(s_["id"], {}).get("items", [])]
    ats = [v["at"] for v in _SRC.values()]
    _CACHE.update(at=max(ats) if ats else 0.0, items=items, errors=errors, pending=pending)
    return _CACHE


def item_id(it: dict) -> str:
    return it["source"] + ":" + re.sub(r"\W+", "", it["url"])[-24:]


_POSTERS: dict[str, tuple[float, str | None]] = {}
POSTER_TTL = 86400  # 포스터는 하루에 한 번만 찾는다
GENERIC = re.compile(r"/(images/common|logo|favicon|default|og[-_]?default|meta\.(jpg|png))", re.I)


def find_poster(page: str, base: str, prefer_body: bool) -> str | None:
    """대표 이미지: 학과 게시판은 본문 첫 그림, 다른 곳은 og:image. 사이트 공통 로고는 고르지 않는다."""
    from urllib.parse import urljoin
    og = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', page, re.I) or \
        re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image', page, re.I)
    body = page[page.find('id="bo_v_con"'):] if 'id="bo_v_con"' in page else page
    imgs = [m for m in re.findall(r'<img[^>]+src=["\']([^"\']+)', body, re.I) if not GENERIC.search(m) and not m.startswith("data:")]
    order = (imgs[:1] + ([og.group(1)] if og else [])) if prefer_body else (([og.group(1)] if og else []) + imgs[:1])
    for u in order:
        u = htmllib.unescape(urljoin(base, u.strip()))
        if u.startswith("https://") and len(u) < 1000 and not GENERIC.search(u):
            return u
    return None


def poster(pid: str, fetch=None, wait: bool | None = None) -> dict:
    """찾아온 대회의 포스터 주소. 화면에 보이는 것만 부르고, 하루 동안 기억한다."""
    hit = _POSTERS.get(pid)
    if hit and time.time() - hit[0] < POSTER_TTL:
        return {"image": hit[1], "pending": False}
    it = next((x for x in _CACHE["items"] if item_id(x) == pid), None)
    if not it:
        return {"image": None, "pending": False}
    if wait is None:
        wait = not os.getenv("VERCEL")
    try:
        page = fetch(it["url"]) if fetch else _get(it["url"], sleep=time.sleep if wait else None)
    except TooSoon:
        return {"image": None, "pending": True}
    except (FetchError, ValueError):
        page = ""
    img = find_poster(page, it["url"], prefer_body=it["source"].startswith("knu")) if page else None
    _POSTERS[pid] = (time.time(), img)
    return {"image": img, "pending": False}


def discover(interests: list[str] | None = None, force: bool = False, fetch=None, now: datetime | None = None) -> dict:
    now = now or datetime.now(KST)
    refresh(fetch, now, force=force)
    seen, picks = set(), []
    src_by_id = {s["id"]: s for s in SOURCES}
    for it in _CACHE["items"]:
        if not _contest_like(it):
            continue
        if it["deadline"] and it["deadline"] < now:
            continue  # 마감 지난 것
        if not it["deadline"] and it["posted"] and it["posted"] < (now - timedelta(days=45)).strftime("%Y-%m-%d"):
            continue  # 마감을 모르는 오래된 글
        key = re.sub(r"\W+", "", it["title"].lower())[:40]
        if key in seen:
            continue
        seen.add(key)
        s, why = score(it, src_by_id[it["source"]], interests or [], now)
        picks.append({**{k: v for k, v in it.items() if k != "deadline"},
                      "id": item_id(it),
                      "deadline_iso": it["deadline"].isoformat() if it["deadline"] else None,
                      "deadline_text": human(it["deadline"]) if it["deadline"] else None,
                      "score": s, "why": why})
    picks.sort(key=lambda x: (-x["score"], x["deadline_iso"] or "9999"))
    return {"items": picks[:20], "checked_at": datetime.fromtimestamp(_CACHE["at"], KST).isoformat(),
            "errors": _CACHE["errors"], "pending": _CACHE.get("pending", []), "sources": [{"id": s["id"], "name": s["name"], "url": s.get("home") or s["url"], "ai": s["ai"]} for s in SOURCES],
            "not_auto": NOT_AUTO}
