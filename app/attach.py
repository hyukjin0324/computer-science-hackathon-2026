"""포스터·첨부파일로 나온 공고를 글로 바꾼다. 바꾼 글은 기존 공고 분석(원문 인용 대조 포함)에 그대로 들어간다.

- HWP(한글 5.x), HWPX: 서버에서 글자만 뽑는다. AI를 쓰지 않는다.
- 이미지(포스터·캡처), PDF: Claude가 보이는 글자를 '그대로 옮겨 적기'만 한다. 요약·해석은 하지 않고,
  다음 단계 분석은 이 옮긴 글을 원문으로 삼아 인용을 대조한다.
- 사용자가 직접 올린 파일만 다룬다. 파일은 저장하지 않는다.
"""

import base64
import io
import json
import re
import struct
import threading
import time
import zipfile
import zlib
from xml.etree import ElementTree

import anthropic

MODEL = "claude-haiku-5-5"
PRICE = (0.10, 0.50)  # USD / 100만 토큰
MAX_BYTES = 3_000_000  # 배포 서버가 한 번에 받는 크기(약 4.5MB)를 base64로 넘지 않게
MAX_CHARS = 20_000
MAX_UNPACKED = 20_000_000  # 한글 파일 압축을 풀었을 때 모두 합쳐 이 크기까지만 (압축 폭탄으로 서버 메모리가 터지지 않게)
TOO_BIG = "압축을 풀면 너무 큰 파일이에요. 공고 부분만 남겨서 다시 올리거나 글을 붙여넣어 주세요"
BROKEN = "파일을 읽지 못했어요. 깨졌거나 지원하지 않는 형식이에요. PDF로 저장해서 올리거나 공고 글을 붙여넣어 주세요"
PER_HOUR = 10
PER_DAY_ALL = 200

IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
USAGE = {"calls": 0, "est_usd": 0.0}
_lock = threading.Lock()
_hits: dict[str, list[float]] = {}
_day = {"key": "", "n": 0}

TRANSCRIBE = """이 대회(공모전·해커톤) 공고의 글자를 보이는 그대로 옮겨 적어 주세요.
- 요약하거나 고치거나 설명을 붙이지 마세요. 공고에 있는 글자만 쓰세요.
- 위에서 아래, 왼쪽에서 오른쪽 순서로 쓰고, 표는 한 칸씩 줄을 바꿔 쓰세요.
- 날짜·요일·시간·금액·인원 같은 숫자는 특히 정확히 옮기세요.
- 읽을 수 없는 글자는 [?]로 쓰세요."""


class AttachError(Exception):
    def __init__(self, msg: str, status: int = 422):
        super().__init__(msg)
        self.status = status


def kind_of(name: str, mime: str | None) -> str:
    n = (name or "").lower()
    m = (mime or "").lower()
    if n.endswith(".hwpx"):
        return "hwpx"
    if n.endswith(".hwp"):
        return "hwp"
    if n.endswith(".pdf") or m == "application/pdf":
        return "pdf"
    if m in IMAGE_TYPES or re.search(r"\.(jpe?g|png|webp|gif)$", n):
        return "image"
    raise AttachError("HWP·HWPX·PDF·사진(JPG·PNG) 파일만 읽을 수 있어요")


# ---------- 한글 (HWP 5.x) ----------
_INLINE = set(range(1, 10)) | {11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23}  # 16바이트(8글자)를 차지하는 제어 문자
HWPTAG_PARA_TEXT = 67


def _para_text(b: bytes) -> str:
    out, i, n = [], 0, len(b) // 2
    while i < n:
        c = struct.unpack_from("<H", b, i * 2)[0]
        if c in _INLINE:
            i += 8
            continue
        if c in (10, 13):
            out.append("\n")
        elif c == 24:
            out.append("-")  # 하이픈
        elif c in (30, 31):
            out.append(" ")  # 묶음 빈칸·고정폭 빈칸
        elif c >= 32:
            out.append(chr(c))
        i += 1
    # 이모지처럼 두 글자로 나뉜 문자를 다시 붙인다
    return "".join(out).encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")


def hwp_records_text(data: bytes) -> list[str]:
    """BodyText 한 구역(압축을 푼 바이트)에서 문단 글자를 차례로 꺼낸다. 표 안 칸의 문단도 같은 레코드라 함께 나온다."""
    paras, p = [], 0
    while p + 4 <= len(data):
        head = struct.unpack_from("<I", data, p)[0]
        tag, size = head & 0x3FF, (head >> 20) & 0xFFF
        p += 4
        if size == 0xFFF:
            size = struct.unpack_from("<I", data, p)[0]
            p += 4
        if tag == HWPTAG_PARA_TEXT:
            t = _para_text(data[p:p + size]).strip()
            if t:
                paras.append(t)
        p += size
    return paras


def _inflate(data: bytes, budget: int) -> bytes:
    """HWP 본문(raw deflate)을 푼다. budget보다 커지면 더 풀지 않고 멈춘다."""
    d = zlib.decompressobj(-15)
    out = d.decompress(data, budget + 1)
    if len(out) > budget or d.unconsumed_tail:
        raise AttachError(TOO_BIG, 413)
    return out


def hwp_text(raw: bytes) -> str:
    import olefile
    try:
        f = olefile.OleFileIO(io.BytesIO(raw))
    except OSError as e:
        raise AttachError("한글(HWP) 파일을 열 수 없어요. 파일이 깨졌는지 확인해 주세요") from e
    with f:
        if not f.exists("FileHeader"):
            raise AttachError("한글 5.0 이상(HWP) 파일만 읽을 수 있어요")
        flags = struct.unpack_from("<I", f.openstream("FileHeader").read(), 36)[0]
        if flags & 2:
            raise AttachError("암호가 걸린 한글 파일이에요. 암호를 풀고 다시 올려 주세요")
        if flags & 4:
            raise AttachError("배포용 한글 파일이라 글자를 꺼낼 수 없어요. PDF로 저장해서 올려 주세요")
        sections = sorted((e for e in f.listdir() if len(e) == 2 and e[0] == "BodyText" and e[1].startswith("Section")),
                          key=lambda e: int(e[1][7:] or 0))
        paras: list[str] = []
        budget = MAX_UNPACKED
        for e in sections:
            data = f.openstream(e).read()
            if flags & 1:
                data = _inflate(data, budget)
            budget -= len(data)
            if budget < 0:
                raise AttachError(TOO_BIG, 413)
            paras += hwp_records_text(data)
    return "\n".join(paras)


# ---------- 한글 (HWPX) ----------
def hwpx_text(raw: bytes) -> str:
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as e:
        raise AttachError("HWPX 파일을 열 수 없어요") from e
    names = sorted((n for n in z.namelist() if re.match(r"Contents/section\d+\.xml$", n)), key=lambda n: int(re.findall(r"\d+", n)[-1]))
    paras: list[str] = []
    budget = MAX_UNPACKED
    for n in names:
        with z.open(n) as fp:  # 압축 파일에 적힌 크기는 속일 수 있어서, 실제로 풀면서 센다
            data = fp.read(budget + 1)
        budget -= len(data)
        if budget < 0:
            raise AttachError(TOO_BIG, 413)
        root = ElementTree.fromstring(data)
        for el in root.iter():
            if el.tag.endswith("}p"):
                # 이 문단 바로 아래 글자만 (표 안 문단은 따로 나온다)
                t = "".join("".join(x.itertext()) for run in el if run.tag.endswith("}run") for x in run if x.tag.endswith("}t")).strip()
                if t:
                    paras.append(t)
    return "\n".join(paras)


# ---------- 이미지·PDF: AI가 옮겨 적기 ----------
def _allow(who: str, now: float) -> None:
    from .limits import take
    take(_lock, _hits, _day, who, now, PER_HOUR, PER_DAY_ALL,
         AttachError("오늘 쓸 수 있는 파일 읽기를 다 썼어요. 공고 글을 붙여넣어 주세요", 429),
         AttachError("파일을 너무 자주 올렸어요. 조금 뒤에 다시 해 주세요", 429))


def transcribe(kind: str, mime: str, raw: bytes, who: str = "anon", client=None) -> str:
    _allow(who, time.time())
    client = client or anthropic.Anthropic(timeout=60, max_retries=1)
    b64 = base64.standard_b64encode(raw).decode()
    if kind == "pdf":
        block = {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": b64}}
    else:
        block = {"type": "image", "source": {"type": "base64", "media_type": mime if mime in IMAGE_TYPES else "image/jpeg", "data": b64}}
    try:
        resp = client.messages.create(model=MODEL, max_tokens=4000,
                                      messages=[{"role": "user", "content": [block, {"type": "text", "text": TRANSCRIBE}]}])
    except anthropic.APIError as e:
        raise AttachError("AI가 파일을 잠깐 못 읽어요. 조금 뒤에 다시 해 주세요", 502) from e
    u = resp.usage
    usd = round((u.input_tokens or 0) / 1e6 * PRICE[0] + (u.output_tokens or 0) / 1e6 * PRICE[1], 5)
    with _lock:
        USAGE["calls"] += 1
        USAGE["est_usd"] = round(USAGE["est_usd"] + usd, 5)
    print(json.dumps({"event": "bento_ai_usage", "feature": "notice_file", "kind": kind, "model": resp.model, "est_usd": usd}), flush=True)
    return "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text").strip()


def read_file(name: str, mime: str | None, raw: bytes, who: str = "anon", ai_ready: bool = False, client=None) -> dict:
    """{text, kind, note}. 글자를 하나도 못 꺼내면 AttachError."""
    if not raw:
        raise AttachError("빈 파일이에요")
    if len(raw) > MAX_BYTES:
        raise AttachError("파일이 너무 커요(3MB까지). 사진은 화면 캡처로, 문서는 공고 부분만 올려 주세요", 413)
    kind = kind_of(name, mime)
    note = ""
    if kind in ("hwp", "hwpx"):
        try:
            text = hwp_text(raw) if kind == "hwp" else hwpx_text(raw)
        except AttachError:
            raise
        except (struct.error, zlib.error, ElementTree.ParseError, zipfile.BadZipFile, zipfile.LargeZipFile,
                KeyError, ValueError, IndexError, OSError, EOFError, RuntimeError, NotImplementedError) as e:
            raise AttachError(BROKEN) from e  # 깨진 파일은 '서버 오류(500)' 대신 무엇을 하면 되는지 알려 준다
    else:
        if not ai_ready and client is None:
            raise AttachError("사진·PDF는 AI로 읽는데, 지금은 AI가 꺼져 있어요. 공고 글을 붙여넣어 주세요", 503)
        text = transcribe(kind, (mime or "").lower(), raw, who=who, client=client)
        note = "AI가 파일의 글자를 옮겨 적었어요. 날짜와 시간은 원본과 한 번 맞춰 봐 주세요."
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(re.sub(r"\s", "", text)) < 20:
        raise AttachError("파일에서 글자를 거의 찾지 못했어요. 공고 글을 붙여넣어 주세요")
    if len(text) > MAX_CHARS:
        text, note = text[:MAX_CHARS], (note + " 길어서 앞부분만 읽었어요.").strip()
    return {"text": text, "kind": kind, "note": note}
