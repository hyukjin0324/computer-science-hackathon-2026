"""심사 기준 자가 채점.

공고에서 심사 기준(항목과 배점)을 읽고, 우리 저장소에서 기계가 확인할 수 있는 근거(테스트·README·배포 링크·팀원 커밋 …)로
항목별 '근거 점수'를 낸다. 실제 심사 점수가 아니라, 제출 전에 비어 있는 근거를 찾기 위한 점검이다.

- 항목 이름이 어떤 종류인지는 낱말로 가른다 (기술 / 창의 / 가치 / 발표·협업). 모르는 항목은 점수 없이 '사람이 판단'으로 둔다.
- 기계가 판단할 수 없는 것은 점수에 넣지 않고 '사람 확인'으로 따로 보여 준다.
- AI는 쓰지 않는다. 같은 입력이면 항상 같은 결과가 나온다.
"""

import re

from .fetch import probe
from .notice import Fact

LABEL = re.compile(r"심사|평가")
SKIP_LABEL = re.compile(r"시상|상금|일정|방법|절차")
CHUNK = re.compile(r"^(?P<name>[가-힣A-Za-z][가-힣A-Za-z0-9 ·/&]*?)\s*[:：]?\s*(?P<w>\d{1,3})\s*(?:%|점|퍼센트)?$")

KINDS = [  # 항목 이름에서 가장 먼저 나오는 낱말의 종류 ('기술의 완성도와 시연'은 기술, '발표 및 협업'은 발표·협업)
    ("team", re.compile(r"발표|협업|팀|전달|소통|시연|역할")),
    ("idea", re.compile(r"창의|참신|독창|혁신|차별|아이디어|기획")),
    ("tech", re.compile(r"기술|완성|구현|안정|품질|성능|개발|디자인|UI|UX|사용성", re.I)),
    ("value", re.compile(r"실용|유용|활용|효용|필요|문제|사업|시장|확장|가치|타당|공감|실현|가능성|파급|기대\s*효과")),
]
KIND_NAME = {"tech": "기술", "idea": "창의", "value": "가치", "team": "발표·협업", "other": "기타"}

TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]+\.\w+$|[._]test\.\w+$|[._]spec\.\w+$|_test\.\w+$", re.I)
IMAGE_PATH = re.compile(r"\.(png|jpe?g|gif|webp|svg)$", re.I)
WORKFLOW = re.compile(r"^\.github/workflows/.+\.ya?ml$")


def parse(facts: list[Fact] | list[dict], text: str | None = None) -> dict:
    """공고 facts(또는 사용자가 붙여 넣은 문장)에서 [{name, weight}]를 읽는다."""
    sources: list[tuple[str, str]] = []
    if text and text.strip():
        sources.append(("붙여 넣은 문장", text.strip()))
    for f in facts or []:
        label = f.label if hasattr(f, "label") else f.get("label", "")
        value = f.value if hasattr(f, "value") else f.get("value", "")
        quote = f.quote if hasattr(f, "quote") else f.get("quote", "")
        if LABEL.search(label or "") and not SKIP_LABEL.search(label or ""):
            sources.append((quote or "", value or ""))
            if quote and quote != value:
                sources.append((quote, quote))
    for quote, raw in sources:
        items = _items(raw)
        if len(items) >= 2:
            return {"items": items, "quote": quote if quote and quote != "붙여 넣은 문장" else raw[:200]}
    return {"items": [], "quote": ""}


WEIGHT_IN_PARENS = re.compile(r"[(（\[]\s*(\d{1,3})\s*(?:%|점|퍼센트)?\s*[)）\]]")  # '기술 완성도(30)', '창의성 (30점)'
# 항목 나누는 곳: 쉼표·가운뎃점·줄바꿈·①② 같은 번호. '/'는 앞뒤에 빈칸이 있거나 배점 바로 뒤일 때만 ('UI/UX'는 자르지 않는다)
SPLIT = re.compile(r"[·,;，、\n|①-⑳]+|\s+/\s*|/\s+|(?<=[\d%점])\s*/")


def _items(raw: str) -> list[dict]:
    s = WEIGHT_IN_PARENS.sub(r" \1", raw or "")  # 괄호 속 배점은 살리고
    s = re.sub(r"\([^)]*\)|（[^）]*）|\[[^\]]*\]", " ", s)  # 나머지 괄호 속 설명은 지운다
    out, seen = [], set()
    for part in SPLIT.split(s):
        part = re.sub(r"\s+", " ", part).strip(" -•*")
        m = CHUNK.match(part)
        if not m:
            continue
        name, w = m.group("name").strip(), int(m.group("w"))
        if not 1 <= w <= 100 or name in seen:
            continue
        seen.add(name)
        out.append({"name": name, "weight": w})
    total = sum(i["weight"] for i in out)
    return out if 2 <= len(out) <= 10 and 50 <= total <= 130 else []


def kind_of(name: str) -> str:
    hits = [(m.start(), i, k) for i, (k, rx) in enumerate(KINDS) if (m := rx.search(name))]
    return min(hits)[2] if hits else "other"


def _sig(id, text, ok, w=1, detail="", evidence=None, fix=None):
    return {"id": id, "text": text, "ok": ok, "w": w, "detail": detail, "evidence": evidence or [], "fix": fix}


def gather(gh, state, links: dict, probe_fn=None) -> dict:
    """저장소에서 채점에 쓸 사실을 한 번만 읽는다."""
    probe_fn = probe_fn or probe
    info = gh.repo_info()
    branch = info.get("default_branch") or "main"
    head = gh.head_sha(branch)
    readme = gh.readme() or ""
    try:
        paths = [t.get("path", "") for t in gh.tree(head)]
    except Exception:  # noqa: BLE001 - 파일 목록을 못 읽어도 나머지는 채점한다
        paths = []
    try:  # 최근 100개만 보면 초반에만 기여한 팀원이 '커밋 없음'으로 나와서, 목록을 끝까지(최대 1,000개) 읽는다
        commits = gh.api_all(gh._r("/commits"), {"sha": branch}) if hasattr(gh, "api_all") else gh.commits(branch=branch, per_page=100)
    except Exception:  # noqa: BLE001
        commits = []
    authors = set()
    prs_merged = 0
    for c in commits:
        a = c.get("author") or {}
        if a.get("login"):
            authors.add(a["login"].lower())
        if re.search(r"\(#\d+\)|Merge pull request #\d+", (c.get("commit") or {}).get("message", "")):
            prs_merged += 1
    deploy = links.get("deploy") or info.get("homepage") or ""
    if not deploy and readme:
        deploy = next((u for u in re.findall(r"https?://[^\s)<>\]\"']+", readme) if "github.com" not in u and "shields.io" not in u), "")
    opened = None
    if deploy:
        try:
            opened = bool(probe_fn(deploy).get("ok"))
        except Exception:  # noqa: BLE001
            opened = False
    video = links.get("video") or ""
    slides = links.get("slides") or ""
    return {"info": info, "branch": branch, "head": head, "readme": readme, "paths": paths, "authors": authors, "pr_commits": prs_merged,
            "deploy": deploy, "deploy_ok": opened, "video": video, "slides": slides,
            "members": [m.lower() for m in state.members], "roles": state.roles or {}, "prs": state.prs, "repo_url": gh.html()}


def signals(kind: str, d: dict) -> list[dict]:
    r, paths = d["readme"], d["paths"]
    rd = {"label": "README 보기", "url": f"{d['repo_url']}/blob/{d['branch']}/README.md"}
    has = lambda rx: bool(re.search(rx, r, re.I))  # noqa: E731
    deploy_sig = _sig("deploy", "배포·데모 링크가 열려요", d["deploy_ok"], 2,
                      "링크가 없어요" if not d["deploy"] else ("열려요" if d["deploy_ok"] else "열리지 않아요"),
                      [{"label": "배포 링크", "url": d["deploy"]}] if d["deploy"] else [],
                      {"label": "README나 제출 정보에 배포 링크를 넣어 주세요"} if not d["deploy_ok"] else None)
    video_sig = _sig("video", "시연 영상 링크가 있어요", bool(d["video"]), 1, "있어요" if d["video"] else "아직 없어요",
                     [{"label": "영상", "url": d["video"]}] if d["video"] else [], None if d["video"] else {"label": "시연 영상을 올리고 링크를 넣어 주세요"})
    if kind == "tech":
        tests = [p for p in paths if TEST_PATH.search(p)]
        ci = [p for p in paths if WORKFLOW.match(p)]
        failing = [p for p in d["prs"] if p.ci == "fail"]
        return [
            _sig("tests", "자동 테스트가 있어요", bool(tests), 2, f"테스트 파일 {len(tests)}개" if tests else "테스트 파일을 찾지 못했어요", [],
                 None if tests else {"label": "핵심 흐름 테스트 2~3개만이라도 추가해 주세요"}),
            _sig("ci", "CI가 있고 실패한 PR이 없어요", bool(ci) and not failing, 1,
                 "CI 설정이 없어요" if not ci else (f"PR {len(failing)}개 실패" if failing else "통과"), [], None if ci and not failing else {"label": "CI 설정을 넣거나 실패한 PR을 고쳐 주세요"}),
            _sig("readme_run", "README에 실행 방법이 있어요", has(r"실행|설치|시작하기|run|install|getting started|npm |pip |docker"), 1, "", [rd],
                 {"label": "README에 실행 방법을 적어 주세요"}),
            deploy_sig,
            _sig("public", "저장소가 공개예요", not d["info"].get("private"), 1, "", [], {"label": "저장소를 공개로 바꿔 주세요"}),
        ]
    if kind == "idea":
        imgs = [p for p in paths if IMAGE_PATH.search(p)]
        return [
            _sig("diff", "README에 기존 방식과 다른 점을 적었어요", has(r"차별|기존|비교|대신|다른 점|처음|새로운"), 2, "", [rd], {"label": "README에 '기존 방식과 무엇이 다른지' 한 문단을 넣어 주세요"}),
            _sig("problem", "README에 문제 정의가 있어요", has(r"왜 만들|문제|배경|불편"), 1, "", [rd], {"label": "README 첫 부분에 어떤 문제를 푸는지 써 주세요"}),
            _sig("visual", "화면·시안 이미지가 저장소에 있어요", bool(imgs), 1, f"이미지 {len(imgs)}개" if imgs else "이미지를 찾지 못했어요", [], {"label": "표지나 화면 이미지를 저장소에 넣어 주세요"}),
            _sig("human_idea", "첫 30초 안에 차별점 한 문장이 전달돼요", None, 0, "발표로 확인해야 해요"),
        ]
    if kind == "value":
        return [
            _sig("user", "README에 누가 쓰는지가 적혀 있어요", has(r"사용자|대상|학생|팀원|팀플"), 1, "", [rd], {"label": "README에 누구를 위한 서비스인지 적어 주세요"}),
            _sig("proof", "써 본 사람의 근거(설문·인터뷰·후기)가 있어요", has(r"설문|인터뷰|후기|피드백|사용해 봤|써 봤|검증|파일럿"), 2, "", [rd], {"label": "설문 5~10명이나 우리 팀이 직접 쓴 기록을 README에 넣어 주세요"}),
            deploy_sig,
            video_sig,
        ]
    if kind == "team":
        members, authors = d["members"], d["authors"]
        missing = [m for m in members if m not in authors]
        return [
            _sig("everyone", "팀원 모두 커밋이 있어요", (not missing) if members and authors else None, 2,
                 ("모두 있어요" if not missing else f"커밋이 없는 팀원: {', '.join(missing)}") if members and authors else "커밋 작성자를 읽지 못했어요",
                 [], None if not missing else {"label": "맡은 일을 작게라도 PR로 올려 기록을 남겨 주세요"}),
            _sig("pr", "PR로 협업한 흔적이 있어요", d["pr_commits"] >= 3, 1, f"PR로 합쳐진 커밋 {d['pr_commits']}개", [], {"label": "main에 바로 올리지 말고 PR로 합쳐 주세요"}),
            _sig("roles", "팀 역할표가 있어요", bool(d["roles"]), 1, "", [], {"label": "마이페이지에서 팀 역할을 정해 주세요"}),
            _sig("slides", "발표 자료 링크가 있어요", bool(d["slides"]), 2, "있어요" if d["slides"] else "아직 없어요",
                 [{"label": "발표 자료", "url": d["slides"]}] if d["slides"] else [], None if d["slides"] else {"label": "발표 자료를 올리고 링크를 넣어 주세요"}),
            video_sig,
            _sig("human_rehearsal", "발표 리허설을 해 봤어요", None, 0, "팀이 직접 확인해야 해요"),
        ]
    return [_sig("human_other", "이 항목은 사람이 판단해요", None, 0, "저장소로는 확인할 수 없어요")]


def score(items: list[dict], data: dict) -> dict:
    rows = []
    for it in items:
        kind = kind_of(it["name"])
        sigs = signals(kind, data)
        checked = [s for s in sigs if s["ok"] is not None and s["w"] > 0]
        got = sum(s["w"] for s in checked if s["ok"])
        base = sum(s["w"] for s in checked)
        pts = round(it["weight"] * got / base, 1) if base else None
        tips = sorted((s for s in checked if not s["ok"] and s["fix"]), key=lambda s: -s["w"])
        rows.append({"name": it["name"], "weight": it["weight"], "kind": kind, "kind_name": KIND_NAME[kind], "points": pts,
                     "got": got, "of": base, "signals": [s for s in sigs if s["ok"] is not None], "human": [s for s in sigs if s["ok"] is None],
                     "tips": [{"text": s["text"], "fix": s["fix"]["label"], "worth": round(it["weight"] * s["w"] / base, 1)} for s in tips[:3]] if base else []})
    scored = [r for r in rows if r["points"] is not None]
    return {"rows": rows, "points": round(sum(r["points"] for r in scored), 1), "max": sum(r["weight"] for r in scored),
            "total": sum(r["weight"] for r in rows)}


def run(gh, state, facts, text: str | None, links: dict, probe_fn=None) -> dict:
    got = parse(facts, text)
    if not got["items"]:
        return {"found": False, "criteria": [], "quote": "", "points": 0, "max": 0, "total": 0}
    data = gather(gh, state, links, probe_fn)
    res = score(got["items"], data)
    return {"found": True, "quote": got["quote"], "criteria": res["rows"], "points": res["points"], "max": res["max"], "total": res["total"],
            "head": data["head"][:7], "note": "자동으로 확인한 근거만 센 점수예요. 실제 심사 점수가 아니라 비어 있는 근거를 찾는 용도예요."}
