"""제안(행동 묶음)을 서명해서 화면에 넘기고, 승인할 때 위조되지 않았는지 확인한다.

서버가 DB 없이도 동작하도록 제안 내용은 서명된 토큰 안에 들어 있다.
화면에서 고칠 수 있는 것은 메시지 글뿐이고, 행동의 종류·대상은 바꿀 수 없다.
"""

import base64
import hashlib
import hmac
import json
import time

MAX_AGE = 6 * 3600


class ProposalError(Exception):
    pass


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign(payload: dict, key: str) -> str:
    body = json.dumps({**payload, "iat": int(time.time())}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    mac = hmac.new(key.encode(), body, hashlib.sha256).digest()
    return f"{_b64(body)}.{_b64(mac)}"


def verify(token: str, key: str, max_age: int = MAX_AGE) -> dict:
    try:
        body_s, mac_s = token.split(".", 1)
        body = _unb64(body_s)
        good = hmac.new(key.encode(), body, hashlib.sha256).digest()
        if not hmac.compare_digest(good, _unb64(mac_s)):
            raise ProposalError("제안 서명이 맞지 않아요")
        data = json.loads(body)
    except ProposalError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ProposalError("제안 형식이 올바르지 않아요") from e
    if time.time() - data.get("iat", 0) > max_age:
        raise ProposalError("오래된 제안이에요. 상황을 다시 확인해 주세요")
    return data
