"""심사 기준 채점: 공고에서 흔한 형식을 읽는지, 항목 종류를 맞게 나누는지, 팀원 커밋을 끝까지 보는지."""

import httpx
import pytest

from app import criteria as crit
from app import main
from app.github import GitHub
from app.notice import Fact


def _items(text):
    return [(i["name"], i["weight"]) for i in crit.parse([Fact(label="심사 기준", value=text, quote=text)])["items"]]


@pytest.mark.parametrize("text,expect", [
    ("기술 완성도(30) · 창의성(25) · 실용성(25) · 발표(20)", [("기술 완성도", 30), ("창의성", 25), ("실용성", 25), ("발표", 20)]),  # 전에는 못 읽음
    ("기술 완성도 (30점), 창의성 (30점), 사업성 (40점)", [("기술 완성도", 30), ("창의성", 30), ("사업성", 40)]),                  # 전에는 못 읽음
    ("① 기술성 40 ② 창의성 30 ③ 활용성 30", [("기술성", 40), ("창의성", 30), ("활용성", 30)]),                                  # 전에는 못 읽음
    ("UI/UX 디자인 20, 기술성 40, 창의성 40", [("UI/UX 디자인", 20), ("기술성", 40), ("창의성", 40)]),                           # 전에는 'UX 디자인'
    ("기술성30/창의성30/발표40", [("기술성", 30), ("창의성", 30), ("발표", 40)]),
    # 원래 읽던 것은 그대로
    ("기술성 30%, 창의성 30%, 실용성 20%, 발표 20%", [("기술성", 30), ("창의성", 30), ("실용성", 20), ("발표", 20)]),
    ("기술성 : 30 / 창의성 : 30 / 실현 가능성 : 40", [("기술성", 30), ("창의성", 30), ("실현 가능성", 40)]),
    ("기술 완성도 30 · 창의성 25 · 실용성 25 · 발표 및 협업 20 (세부 기준은 별첨)", [("기술 완성도", 30), ("창의성", 25), ("실용성", 25), ("발표 및 협업", 20)]),
])
def test_criteria_formats(text, expect):
    assert _items(text) == expect


@pytest.mark.parametrize("name,kind", [
    ("기술의 완성도와 시연", "tech"),  # 전에는 '시연' 때문에 발표·협업
    ("발표 및 협업", "team"), ("기획력", "idea"), ("실현 가능성", "value"), ("UI/UX 디자인", "tech"),  # 뒤의 셋은 전에는 '기타'
    ("기술 완성도", "tech"), ("창의성", "idea"), ("실용성", "value"), ("심사위원 재량", "other"),
])
def test_criteria_kind(name, kind):
    assert crit.kind_of(name) == kind


def test_everyone_committed_looks_past_the_latest_100_commits():
    """커밋이 많은 저장소에서 초반에만 기여한 팀원도 '커밋 있음'으로 본다."""
    def handler(req: httpx.Request):
        page = int(req.url.params.get("page", 1))
        if not req.url.path.endswith("/commits"):
            return httpx.Response(404, json={})
        if page == 1:
            return httpx.Response(200, json=[{"author": {"login": "lead"}, "commit": {"message": "x"}}] * 100)
        return httpx.Response(200, json=[{"author": {"login": "early-member"}, "commit": {"message": "첫 화면 (#3)"}}] * 30)
    gh = GitHub(main.settings, "team/repo", http=httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler)))
    gh._token, gh._token_exp = "t", 10 ** 12
    gh.repo_info = lambda: {"default_branch": "main"}
    gh.head_sha = lambda b: "abc1234"
    gh.readme = lambda: ""
    gh.tree = lambda sha: []
    state = type("S", (), {"members": ["lead", "early-member"], "roles": {}, "prs": []})()
    d = crit.gather(gh, state, {}, probe_fn=lambda url: {"ok": False})
    assert {"lead", "early-member"} <= d["authors"]  # 전에는 최근 100개만 봐서 early-member를 놓쳤다
