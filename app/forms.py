"""신청·제출 폼을 읽고, 값이 미리 채워진 링크를 만든다.

Bento는 폼을 제출하지 않는다. 사람이 실제 폼을 열어 확인하고 제출 버튼을 누른다.
개인정보 칸(학번·연락처 등)은 미리 채우지 않고 사람에게 남긴다.
"""

import json
import re
from urllib.parse import urlencode, urlparse

from .fetch import FetchError, fetch_page

SENSITIVE = re.compile(r"학번|전화|연락처|휴대|이메일|e-?mail|생년|주민|계좌|주소|서명|동의", re.I)
# 우리가 아는 값 → 폼 질문에서 찾을 말
KEYS = [
    ("repo", re.compile(r"저장소|github|깃허브|깃헙|레포|repo|소스\s*코드", re.I)),
    ("deploy", re.compile(r"배포|서비스\s*(링크|주소|url)|데모\s*(링크|주소)|웹\s*주소|url", re.I)),
    ("video", re.compile(r"영상|시연|youtube|유튜브", re.I)),
    ("slides", re.compile(r"발표|ppt|슬라이드|자료|pdf", re.I)),
    ("team", re.compile(r"팀\s*(명|이름)", re.I)),
    ("members", re.compile(r"팀원|구성원|멤버|참가자\s*(명단|이름)", re.I)),
    ("idea", re.compile(r"아이디어|소개|설명|서비스\s*명|프로젝트\s*(명|이름)|주제", re.I)),
]
TYPES = {0: "짧은 답", 1: "긴 답", 2: "객관식", 3: "드롭다운", 4: "체크박스", 5: "선형 배율", 7: "그리드", 9: "날짜", 10: "시간", 13: "파일"}
PREFILLABLE = {0, 1}


class FormError(Exception):
    pass


def _is_google_form(url: str) -> bool:
    u = urlparse(url)
    return (u.hostname or "") in ("docs.google.com", "forms.gle") and (u.hostname == "forms.gle" or u.path.startswith("/forms/"))


def parse_google_form(html: str) -> list[dict]:
    m = re.search(r"FB_PUBLIC_LOAD_DATA_\s*=\s*(\[.*?\]);\s*</script>", html, re.S)
    if not m:
        raise FormError("폼 질문을 읽지 못했어요")
    data = json.loads(m.group(1))
    out = []
    for item in (data[1][1] or []):
        title, kind, inner = item[1], item[3], item[4] if len(item) > 4 else None
        if not inner or not inner[0]:
            continue  # 제목·설명·섹션
        out.append({"title": (title or "").strip(), "entry": int(inner[0][0]), "type": TYPES.get(kind, "기타"),
                    "required": bool(inner[0][2]) if len(inner[0]) > 2 else False, "prefill": kind in PREFILLABLE})
    return out


def suggest(questions: list[dict], values: dict[str, str]) -> list[dict]:
    """질문마다 채울 값을 고른다. 개인정보 칸과 고르는 칸은 사람에게 남긴다."""
    out = []
    for q in questions:
        row = dict(q, key=None, value="", who="human", why="")
        if SENSITIVE.search(q["title"]):
            row["why"] = "개인정보라 Bento가 채우지 않아요"
        elif not q["prefill"]:
            row["why"] = f"{q['type']} 칸은 직접 골라 주세요"
        else:
            key = next((k for k, rx in KEYS if rx.search(q["title"]) and values.get(k)), None)
            if key:
                row.update(key=key, value=values[key], who="bento", why="Bento가 채웠어요 · 열어서 확인해 주세요")
            else:
                row["why"] = "맞는 값을 찾지 못했어요"
        out.append(row)
    return out


def prefill_url(base: str, rows: list[dict]) -> str:
    q = [(f"entry.{r['entry']}", r["value"]) for r in rows if r.get("who") == "bento" and r.get("value")]
    return base + "?" + urlencode([("usp", "pp_url"), *q])


def inspect_form(url: str, values: dict[str, str]) -> dict:
    if not _is_google_form(url):
        return {"kind": "other", "rows": [], "note": "구글 폼이 아니라서 칸마다 복사 버튼으로 도와 드릴게요"}
    try:
        final, _, html = fetch_page(url, max_bytes=3_000_000)
    except FetchError as e:
        raise FormError(str(e)) from e
    if "accounts.google.com" in final:
        return {"kind": "login", "rows": [], "note": "학교 계정 로그인이 필요한 폼이라 읽지 못했어요. 칸마다 복사 버튼으로 도와 드릴게요"}
    base = re.sub(r"/(viewform|formResponse)?(\?.*)?$", "", final.split("#")[0]) + "/viewform"
    rows = suggest(parse_google_form(html), values)
    return {"kind": "google", "base": base, "rows": rows, "url": prefill_url(base, rows)}
