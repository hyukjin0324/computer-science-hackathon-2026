"""화면을 스크립트로 그리는 공고 페이지를 브라우저처럼 열어서 글을 읽는다 (이 컴퓨터에서만).

- 사용자가 고른 공고 페이지 하나만 연다. 목록을 돌며 긁지 않는다.
- robots.txt가 그 페이지를 막아 두었으면 열지 않는다.
- 페이지가 내부망(사설 IP·localhost)으로 요청하면 막는다. 이미지·글꼴·영상은 받지 않는다.
- 서버리스(Vercel)에서는 브라우저를 돌릴 수 없어서 쓰지 않는다. 그때는 붙여넣기로 대신한다.
"""

import ipaddress
import os
import socket
from functools import lru_cache
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36 bento-agent/1.0"
TIMEOUT_MS = 20_000


def available() -> bool:
    if os.getenv("VERCEL") or os.getenv("BENTO_NO_RENDER"):
        return False
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return True


@lru_cache(maxsize=256)
def _public_host(host: str) -> bool:
    try:
        return all(ipaddress.ip_address(i[4][0]).is_global for i in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM))
    except (socket.gaierror, ValueError):
        return False


def robots_allows(url: str) -> bool:
    u = urlparse(url)
    rp = RobotFileParser()
    try:
        import httpx
        r = httpx.get(f"{u.scheme}://{u.netloc}/robots.txt", headers={"User-Agent": UA}, timeout=8, follow_redirects=True)
        if r.status_code >= 400:
            return True  # robots.txt가 없으면 막은 곳도 없다
        rp.parse(r.text.splitlines())
    except Exception:  # noqa: BLE001 - 못 읽으면 보수적으로 열지 않는다
        return False
    return rp.can_fetch("bento-agent", url)


def render_text(url: str) -> tuple[str, str]:
    """(제목, 본문 글자). 열지 못하면 RuntimeError."""
    if not available():
        raise RuntimeError("이 서버에서는 브라우저로 열 수 없어요")
    if not robots_allows(url):
        raise RuntimeError("이 사이트는 자동으로 여는 것을 막아 두었어요")
    from playwright.sync_api import sync_playwright

    def guard(route):
        req = route.request
        host = urlparse(req.url).hostname or ""
        if req.resource_type in ("image", "media", "font") or not _public_host(host):
            return route.abort()
        return route.continue_()

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        try:
            ctx = b.new_context(user_agent=UA, java_script_enabled=True, locale="ko-KR")
            page = ctx.new_page()
            page.route("**/*", guard)
            page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:  # noqa: BLE001 - 계속 무언가를 불러오는 사이트도 있다
                pass
            title = page.title() or ""
            text = ""
            for sel in ("main", "article", "body"):
                loc = page.locator(sel)
                if loc.count():
                    text = loc.first.inner_text(timeout=5000)
                    if len(text) > 200:
                        break
            return title, text
        finally:
            b.close()
