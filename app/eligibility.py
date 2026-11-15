"""참가 자격 확인: 공고마다 필요한 질문만 묻고, 조건별로 판정한다.

- 질문은 공고의 '참가 자격' 문장과 팀 인원 조건에서만 만든다 (개인 참가면 팀 인원을 묻지 않는다).
- 판정은 규칙으로 한다. 부족하면 "팀원 1명이 더 필요해요"처럼 다음에 할 일을 말한다.
- 규칙으로 읽지 못한 조건은 판정하지 않고 '주최 측 확인 필요'로 남긴다.
- 모든 판정 옆에 공고 원문 문장을 붙인다.
"""

import re

from .notice import NoticeExtract

STATUS_OPTS = ["모두 재학 중", "휴학생 있음", "졸업 예정자 있음", "졸업생 있음"]
_STATUS_KEY = {"휴학생 있음": "휴학", "졸업 예정자 있음": "졸업예정", "졸업생 있음": "졸업"}
_KNOWN = re.compile(r"학년|재학|휴학|졸업|학부|학과|대학|학생|인\s*(?:이상|이하|팀|까지)|명|팀|개인|누구나|제한\s*없")


def _quotes(ex: NoticeExtract) -> list[str]:
    return [f.quote for f in ex.facts if f.label == "참가 자격" and f.quote]


def _text(ex: NoticeExtract) -> str:
    return " ".join([f.quote + " " + f.value for f in ex.facts if f.label == "참가 자격"])


def _find(quotes: list[str], pat: str) -> str:
    return next((q for q in quotes if re.search(pat, q)), quotes[0] if quotes else "")


def rules(ex: NoticeExtract) -> dict:
    """공고에서 읽은 조건. 질문을 만들고 판정할 때 같이 쓴다."""
    t, q = _text(ex), _quotes(ex)
    r: dict = {}
    if m := re.search(r"([1-4])\s*[~∼\-]\s*([1-4])\s*학년", t):
        r["grade"] = {"min": int(m.group(1)), "max": int(m.group(2)), "quote": _find(q, r"학년")}
    elif m := re.search(r"([1-4])\s*학년\s*이상", t):
        r["grade"] = {"min": int(m.group(1)), "max": None, "quote": _find(q, r"학년")}
    elif m := re.search(r"([1-4])\s*학년\s*이하", t):
        r["grade"] = {"min": None, "max": int(m.group(1)), "quote": _find(q, r"학년")}
    if "재학" in t:
        allow = {"재학"}
        if re.search(r"휴학생?\s*(?:도|포함|가능|참여\s*가능)", t):
            allow.add("휴학")
        if re.search(r"졸업\s*예정자?\s*(?:도|포함|가능)", t):
            allow.add("졸업예정")
        if re.search(r"졸업생\s*(?:도|포함|가능)", t):
            allow.add("졸업")
        r["status"] = {"allow": sorted(allow), "quote": _find(q, r"재학")}
    if m := re.search(r"([가-힣]+(?:학부|학과|대학))\s*(?:재학생|학생|소속|학부생)", t):
        r["dept"] = {"name": m.group(1), "quote": _find(q, m.group(1))}
    solo_ok = bool(re.search(r"개인\s*(?:또는|혹은|및|/|·|,)|개인\s*참가|1인\s*(?:또는|이상)|1\s*[~∼\-]", t))
    lo, hi = ex.team_min, ex.team_max
    exact = [1, lo] if solo_ok and lo and lo > 1 and lo == hi else None  # '개인 또는 3인 팀' = 1명 또는 3명만
    if solo_ok and lo and lo > 1:
        lo = 1
    if not (hi == 1 and (lo in (None, 1))) and (lo is not None or hi is not None):
        r["team"] = {"min": lo, "max": hi, "solo": solo_ok, "exact": exact, "quote": _find(q, r"인|명|팀")}
    # 규칙으로 읽지 못한 조건 문장 → 주최 측 확인 필요
    unclear = []
    for quote in q:
        for chunk in re.split(r"[,·/]|\s및\s", quote):
            c = chunk.strip(" .()")
            if len(c) >= 4 and not _KNOWN.search(c):
                unclear.append(c)
    r["unclear"] = unclear[:3]
    r["has_rules"] = bool(q) or ex.team_min is not None or ex.team_max is not None
    return r


def questions(ex: NoticeExtract) -> list[dict]:
    """이 공고를 판정하는 데 필요한 질문만."""
    r, out = rules(ex), []
    if "team" in r:
        out.append({"key": "team", "label": "함께 신청하는 인원 (나 포함)", "type": "number"})
    if "grade" in r:
        out.append({"key": "grade", "label": "팀원 중 가장 낮은 학년" if "team" in r else "학년", "type": "choice", "options": ["1", "2", "3", "4"]})
    if "status" in r:
        out.append({"key": "status", "label": "팀원 학적", "type": "choice", "options": STATUS_OPTS})
    if "dept" in r:
        out.append({"key": "dept", "label": f"모두 {r['dept']['name']} 소속인가요?", "type": "choice", "options": ["네", "아니요"]})
    return out


def _ev(quote: str, url: str | None) -> list[dict]:
    if not quote:
        return []
    e = {"label": f"공고: {quote}"}
    if url:
        e["url"] = url
    return [e]


def judge(ex: NoticeExtract, answers: dict, url: str | None = None) -> list[dict]:
    """조건별 판정. status: ok(충족) · fail(미충족, 다음 할 일 포함) · warn(아직 모름 · 확인 필요)."""
    from .submission import _row
    r, rows = rules(ex), []
    a = {k: (str(v).strip() if v is not None else "") for k, v in (answers or {}).items()}

    if "team" in r:
        lo, hi = r["team"]["min"], r["team"]["max"]
        cond = f"{lo or 1}~{hi}명" if hi else f"{lo}명 이상"
        try:
            n = int(a.get("team", ""))
        except ValueError:
            n = 0
        exact = r["team"].get("exact")
        if exact:
            cond = " 또는 ".join(f"{x}명" for x in exact)
        if n <= 0:
            rows.append(_row("팀 인원", "warn", f"인원을 알려 주면 판정해요 · 조건 {cond}", _ev(r["team"]["quote"], url)))
        elif exact and n not in exact:
            rows.append(_row("팀 인원", "fail", f"{cond}만 신청할 수 있어요 · 지금 {n}명", _ev(r["team"]["quote"], url),
                             {"label": f"{exact[-1]}명으로 맞추거나 개인으로 신청하기"}))
        elif lo and n < lo:
            rows.append(_row("팀 인원", "fail", f"팀원 {lo - n}명이 더 필요해요 · 지금 {n}명, 조건 {cond}", _ev(r["team"]["quote"], url),
                             {"label": f"팀원 {lo - n}명 더 구하기"}))
        elif hi and n > hi:
            rows.append(_row("팀 인원", "fail", f"{n - hi}명이 많아요 · 지금 {n}명, 조건 {cond}", _ev(r["team"]["quote"], url),
                             {"label": f"{hi}명 이하로 맞추기"}))
        else:
            rows.append(_row("팀 인원", "ok", f"지금 {n}명 · 조건 {cond}", _ev(r["team"]["quote"], url)))

    if "grade" in r:
        lo, hi = r["grade"]["min"], r["grade"]["max"]
        cond = f"{lo}~{hi}학년" if lo and hi else f"{lo}학년 이상" if lo else f"{hi}학년 이하"
        g = int(a["grade"]) if a.get("grade", "").isdigit() else 0
        if not g:
            rows.append(_row("학년", "warn", f"학년을 알려 주면 판정해요 · 조건 {cond}", _ev(r["grade"]["quote"], url)))
        elif (lo and g < lo) or (hi and g > hi):
            rows.append(_row("학년", "fail", f"{cond}만 신청할 수 있어요 · 지금 {g}학년", _ev(r["grade"]["quote"], url),
                             {"label": f"{cond} 팀원으로 구성하기"}))
        else:
            rows.append(_row("학년", "ok", f"{g}학년 · 조건 {cond}", _ev(r["grade"]["quote"], url)))

    if "status" in r:
        allow = r["status"]["allow"]
        s = a.get("status", "")
        if not s:
            rows.append(_row("학적", "warn", "팀원 학적을 알려 주면 판정해요 · 조건 재학생", _ev(r["status"]["quote"], url)))
        elif s == STATUS_OPTS[0] or _STATUS_KEY.get(s) in allow:
            rows.append(_row("학적", "ok", f"{s} · 신청할 수 있어요", _ev(r["status"]["quote"], url)))
        else:
            who = s.replace(" 있음", "")
            rows.append(_row("학적", "fail", f"{who}은(는) 공고에 신청 가능하다고 나와 있지 않아요", _ev(r["status"]["quote"], url),
                             {"label": "주최 측에 문의하거나 재학생으로 구성하기"}))

    if "dept" in r:
        d = a.get("dept", "")
        name = r["dept"]["name"]
        if not d:
            rows.append(_row("소속", "warn", f"소속을 알려 주면 판정해요 · 조건 {name}", _ev(r["dept"]["quote"], url)))
        elif d == "네":
            rows.append(_row("소속", "ok", f"모두 {name} 소속", _ev(r["dept"]["quote"], url)))
        else:
            rows.append(_row("소속", "fail", f"{name} 소속이 아닌 팀원이 있어요", _ev(r["dept"]["quote"], url),
                             {"label": "주최 측에 다른 학과 참여가 되는지 문의하기"}))

    for c in r["unclear"]:
        rows.append(_row("주최 측 확인 필요", "warn", f"'{c}' · 공고만으로는 판단하기 어려워요", _ev(c, url),
                         {"label": "주최 측 문의처에 확인하기"}))
    if not r["has_rules"]:
        rows.append(_row("참가 자격", "warn", "공고에서 참가 자격을 찾지 못했어요 · 원문에서 직접 확인하거나 주최 측에 물어봐 주세요",
                         [{"label": "공고 원문 보기", "url": url}] if url else [], {"label": "주최 측 문의처에 확인하기"}))
    return rows
