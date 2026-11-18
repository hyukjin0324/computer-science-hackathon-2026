"""공고 링크에서 본문 글자만 가져온다.

서버가 대신 여는 주소라서 내부망(사설 IP, localhost 등)으로는 가지 않는다.
리다이렉트도 한 번씩 다시 검사한다.
"""

import ipaddress
import re
import socket
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

MAX_BYTES = 2_000_000
MAX_TEXT = 30_000
MAX_REDIRECTS = 3
SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "button", "iframe", "template"}
BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "table", "dt", "dd", "pre", "blockquote"}


CODEY = re.compile(r"\$\{|\{\{|=>|function\s*\(|\bconst\s|\bvar\s|;\s*\}|\}\s*\)|window\.|document\.")  # 프로그램 코드 조각


class FetchError(Exception):
    pass


def _check_host(url: str) -> None:
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise FetchError("http 또는 https 주소만 열 수 있어요")
    if u.username or u.password:
        raise FetchError("아이디·비밀번호가 들어간 주소는 열지 않아요")
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise FetchError("주소를 찾지 못했어요. 링크를 확인해 주세요") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise FetchError("내부망 주소는 열 수 없어요")


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0
        self.title = ""
        self.og_title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta" and a.get("property") == "og:title":
            self.og_title = a.get("content") or ""
        if tag == "title":
            self._in_title = True
        if tag in SKIP:
            self.skip += 1
        if tag in BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in SKIP and self.skip:
            self.skip -= 1
        if tag in BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skip:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    p = _Text()
    p.feed(html)
    lines = [" ".join(line.split()) for line in "".join(p.parts).split("\n")]
    text = "\n".join(line for line in lines if line and not CODEY.search(line))
    title = " ".join(unescape(p.og_title or p.title).split())
    return title, text


def fetch_page(url: str, client: httpx.Client | None = None, max_bytes: int = MAX_BYTES) -> tuple[str, str, str]:
    """(최종 주소, content-type, 본문 글자). 리다이렉트마다 주소를 다시 검사한다."""
    url = url.strip()
    own = client is None
    client = client or httpx.Client(timeout=10, headers={"User-Agent": "Bento/1.0 (student team-project assistant)", "Accept-Language": "ko,en;q=0.8"})
    try:
        for _ in range(MAX_REDIRECTS + 2):
            _check_host(url)
            with client.stream("GET", url, follow_redirects=False) as r:
                if r.is_redirect and r.headers.get("location"):
                    url = urljoin(url, r.headers["location"])
                    continue
                if r.status_code >= 400:
                    raise FetchError(f"페이지를 열지 못했어요 ({r.status_code})")
                ctype = r.headers.get("content-type", "")
                if not any(t in ctype for t in ("text/html", "text/plain", "application/xhtml")):
                    raise FetchError("글로 된 페이지가 아니에요. PDF나 이미지 공고는 본문을 복사해 붙여 주세요")
                body = b""
                for chunk in r.iter_bytes():
                    body += chunk
                    if len(body) > max_bytes:
                        raise FetchError("페이지가 너무 커요. 본문을 복사해 붙여 주세요")
                return url, ctype, body.decode(r.encoding or "utf-8", errors="replace")
        raise FetchError("다른 주소로 너무 많이 넘어가요")
    except httpx.HTTPError as e:
        raise FetchError("페이지를 여는 중에 연결이 끊겼어요") from e
    finally:
        if own:
            client.close()


def probe(url: str, client: httpx.Client | None = None) -> dict:
    """링크가 실제로 열리는지만 본다. 본문은 읽지 않는다."""
    url = url.strip()
    own = client is None
    client = client or httpx.Client(timeout=8, headers={"User-Agent": "Bento/1.0 (student team-project assistant)"})
    try:
        for _ in range(6):
            try:
                _check_host(url)
            except FetchError as e:
                return {"ok": False, "status": None, "url": url, "error": str(e)}
            with client.stream("GET", url, follow_redirects=False) as r:
                if r.is_redirect and r.headers.get("location"):
                    url = urljoin(url, r.headers["location"])
                    continue
                login = "accounts.google.com" in url or "/login" in url
                return {"ok": r.status_code < 400 and not login, "status": r.status_code, "url": url,
                        "error": "로그인해야 열리는 링크예요. 심사위원이 못 볼 수 있어요" if login else None}
        return {"ok": False, "status": None, "url": url, "error": "다른 주소로 너무 많이 넘어가요"}
    except httpx.HTTPError:
        return {"ok": False, "status": None, "url": url, "error": "연결되지 않아요"}
    finally:
        if own:
            client.close()


def _main_part(raw: str) -> tuple[str | None, str]:
    """사이트 메뉴를 빼고 글 본문만. 그누보드 게시판(학과 사이트)과 일반 <article>/<main>을 안다."""
    title = None
    if m := re.search(r'class="bo_v_tit"[^>]*>(.*?)</span>', raw, re.S):
        title = " ".join(unescape(re.sub(r"<[^>]+>", " ", m.group(1))).split())
    for pat in (r'<article id="bo_v".*?</article>', r"<article\b.*?</article>", r"<main\b.*?</main>"):
        if m := re.search(pat, raw, re.S | re.I):
            return title, m.group(0)
    return title, raw


def focus_detail(text: str) -> str:
    """공모전 모음 사이트(링커리어 등)를 브라우저로 읽으면 추천 공고·후기·댓글이 한데 섞인다.
    '상세내용' 탭이 여러 번 나오면 맨 위 정보 칸(접수기간·참여대상)과 마지막 '상세내용' 이후 본문만 남긴다."""
    lines = text.splitlines()
    marks = [i for i, l in enumerate(lines) if l.strip() == "상세내용"]
    if len(marks) < 2:
        return text
    head: list[str] = []
    for i, l in enumerate(lines[:marks[0]]):
        if l.strip() in ("접수기간", "참여대상", "시상규모") and i + 1 < len(lines) and lines[i + 1].strip():
            head.append(f"{l.strip()}: {lines[i + 1].strip()}")
    body = lines[marks[-1] + 1:]
    stop = next((i for i, l in enumerate(body) if re.match(r"^(합격\s?스펙|최종합격후기|합격 ?후기|합격자료|담당자 Q&A|댓글|이 친구와)", l.strip()) and i > 0), len(body))
    return "\n".join(head + body[:stop])


def fetch_notice(url: str, client: httpx.Client | None = None) -> dict:
    url, ctype, raw = fetch_page(url, client)
    if "text/plain" in ctype:
        title, text, imgs = "", raw, 0
    else:
        main_title, part = _main_part(raw)
        page_title, text = html_to_text(part)
        title = main_title or page_title
        imgs = len(re.findall(r"<img\b", part, re.I))
    rendered = False
    if len(text) < 200:  # 화면을 스크립트로 그리는 사이트: 브라우저처럼 열어서 다시 읽는다 (이 컴퓨터에서만)
        from . import render
        if render.available():
            try:
                rt, rtext = render.render_text(url)
                if len(rtext) > len(text):
                    rtext = focus_detail(rtext)
                    title, text, rendered, imgs = (title or rt), rtext, True, 0
            except Exception:  # noqa: BLE001 - 못 열면 처음 읽은 글로 계속한다
                pass
    if title and title not in text[:200]:
        text = f"{title}\n{text}"
    if len(text) < 80 and not title:
        raise FetchError("페이지에서 본문을 거의 읽지 못했어요. 로그인이 필요하거나 화면을 스크립트로 그리는 사이트일 수 있어요. 본문을 복사해 붙여 주세요")
    note = "본문 대부분이 이미지(포스터)라서 글자로 읽을 수 있는 게 적어요. 마감은 제목에서 가져왔어요" if imgs and len(text) < 400 else None
    if rendered:
        note = "화면을 스크립트로 그리는 사이트라 브라우저로 열어서 읽었어요"
    return {"url": url, "title": title, "text": text[:MAX_TEXT], "truncated": len(text) > MAX_TEXT, "note": note, "rendered": rendered}
