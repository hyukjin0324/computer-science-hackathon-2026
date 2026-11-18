"""팀 디스코드 연결: 팀원이 웹훅 주소를 한 번 붙여 넣으면 그 팀 전체가 디스코드로 메시지를 보낸다.

- 주소는 비밀값이라 서버 키로 암호화해서, 역할표처럼 팀 저장소의 라벨(bento-discord-1, -2)에 나눠 저장한다.
  저장소가 공개여도 서버 키 없이는 주소를 알 수 없다. 서버 키가 바뀌면 다시 연결해야 한다.
- 서버 설정(DISCORD_WEBHOOK_URL)이 있으면 연결하지 않은 팀의 기본값으로 쓴다.
- 연결할 때 주소가 진짜인지는 메시지를 보내지 않고 웹훅 정보를 읽어서만 확인한다.
"""

import base64
import hashlib
import os
import re

import httpx
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .github import GitHubError

PREFIX = "bento-discord-"
CHUNK = 90  # 라벨 설명은 100자까지
URL = re.compile(r"^https://(?:ptb\.|canary\.)?(?:discord|discordapp)\.com/api/(?:v\d+/)?webhooks/(\d{5,25})/([A-Za-z0-9_\-]{20,120})/?$")


class DiscordError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def parse(url: str) -> tuple[str, str]:
    m = URL.match((url or "").strip())
    if not m:
        raise DiscordError("디스코드 웹훅 주소가 아니에요. https://discord.com/api/webhooks/… 로 시작하는 주소를 붙여 넣어 주세요")
    return m.group(1), m.group(2)


def to_url(wid: str, token: str) -> str:
    return f"https://discord.com/api/webhooks/{wid}/{token}"


def _key(signing_key: str, repo: str) -> bytes:
    return hashlib.sha256(f"bento-discord|{repo}|{signing_key}".encode()).digest()


def seal(url: str, signing_key: str, repo: str) -> str:
    wid, token = parse(url)
    nonce = os.urandom(12)
    ct = AESGCM(_key(signing_key, repo)).encrypt(nonce, f"{wid}/{token}".encode(), repo.encode())
    return base64.urlsafe_b64encode(nonce + ct).decode().rstrip("=")


def unseal(blob: str, signing_key: str, repo: str) -> str | None:
    try:
        raw = base64.urlsafe_b64decode(blob + "=" * (-len(blob) % 4))
        wid, token = AESGCM(_key(signing_key, repo)).decrypt(raw[:12], raw[12:], repo.encode()).decode().split("/", 1)
    except (InvalidTag, ValueError):
        return None
    return to_url(wid, token)


def read(gh, signing_key: str) -> str | None:
    """팀 저장소에 연결해 둔 웹훅 주소. 없거나 못 읽으면 None."""
    try:
        labels = gh.labels()
    except Exception:  # noqa: BLE001 - 못 읽으면 연결 안 된 것으로
        return None
    parts = {}
    for lb in labels:
        name = lb.get("name") or ""
        if name.startswith(PREFIX) and name[len(PREFIX):].isdigit():
            parts[int(name[len(PREFIX):])] = lb.get("description") or ""
    if not parts:
        return None
    blob = "".join(parts[i] for i in sorted(parts))
    return unseal(blob, signing_key, gh.repo)


def write(gh, url: str, signing_key: str) -> None:
    blob = seal(url, signing_key, gh.repo)
    pieces = [blob[i:i + CHUNK] for i in range(0, len(blob), CHUNK)]
    have = {lb["name"] for lb in gh.labels() if (lb.get("name") or "").startswith(PREFIX)}
    for i, piece in enumerate(pieces, 1):
        name = f"{PREFIX}{i}"
        try:
            gh.update_label(name, piece)
        except GitHubError:
            gh.create_label(name, "5865F2", piece)
        have.discard(name)
    for extra in have:  # 예전에 더 길게 저장했던 조각은 지운다
        gh.delete_label(extra)


def clear(gh) -> None:
    for lb in gh.labels():
        if (lb.get("name") or "").startswith(PREFIX):
            gh.delete_label(lb["name"])


def check(url: str, client: httpx.Client | None = None) -> dict:
    """주소가 살아 있는 웹훅인지 확인한다. 메시지는 보내지 않고 웹훅 정보만 읽는다."""
    wid, token = parse(url)
    own = client is None
    client = client or httpx.Client(timeout=8)
    try:
        r = client.get(to_url(wid, token))
    except httpx.HTTPError as e:
        raise DiscordError("디스코드에 연결하지 못했어요. 잠시 뒤에 다시 해 주세요", 502) from e
    finally:
        if own:
            client.close()
    if r.status_code in (401, 404):
        raise DiscordError("이 웹훅은 없거나 지워졌어요. 디스코드에서 새로 만들어 주소를 다시 붙여 넣어 주세요")
    if r.status_code >= 400:
        raise DiscordError(f"디스코드가 주소를 받아 주지 않았어요 ({r.status_code})", 502)
    try:
        info = r.json()
    except ValueError:
        info = {}
    return {"name": str(info.get("name") or "")[:60], "channel_id": str(info.get("channel_id") or "")}


def webhook_for(gh, settings) -> str | None:
    """이 팀이 쓸 웹훅: 팀이 연결한 것이 먼저, 없으면 서버 기본값."""
    return read(gh, settings.signing_key) or settings.discord_webhook
