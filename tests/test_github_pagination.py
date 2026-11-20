"""GitHub 목록을 첫 페이지만이 아니라 끝까지 읽는지 (가짜 GitHub 서버로)."""

import httpx

from app import main
from app.github import GitHub


def _fake_github(counts: dict[str, int], seen: list):
    """경로마다 counts개의 항목을 100개씩 나눠 주는 가짜 GitHub."""
    def handler(req: httpx.Request):
        seen.append((req.url.path, dict(req.url.params)))
        page, per = int(req.url.params.get("page", 1)), int(req.url.params.get("per_page", 30))
        key = next(k for k in counts if req.url.path.endswith(k))
        n = counts[key]
        items = list(range((page - 1) * per, min(page * per, n)))
        if key == "/milestones":
            data = [{"number": i + 1, "title": f"계획 · {i}", "due_on": None, "description": "", "open_issues": 0, "closed_issues": 0} for i in items]
        elif key == "/issues":
            data = [{"number": i + 1, "title": f"이슈 {i}", "body": "", "assignees": [], "milestone": None, "user": {"login": "a"}} for i in items]
        elif key == "/labels":
            data = [{"name": f"label-{i}", "description": ""} for i in items]
        elif key == "/comments":
            data = [{"id": i, "body": f"댓글 {i}"} for i in items]
        else:
            data = [{"login": f"user{i}"} for i in items]
        return httpx.Response(200, json=data)
    gh = GitHub(main.settings, "team/repo", http=httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler)))
    gh._token, gh._token_exp = "t", 10 ** 12  # 설치 토큰은 이미 있다고 친다
    return gh


def test_lists_are_read_past_the_first_page():
    seen = []
    gh = _fake_github({"/milestones": 45, "/issues": 230, "/labels": 120, "/comments": 150, "/assignees": 101}, seen)
    assert len(gh.milestones()) == 45  # 전에는 30개까지만
    assert len(gh.open_issues()) == 230  # 전에는 100개까지만
    assert len(gh.labels()) == 120
    assert len(gh.issue_comments(1)) == 150  # 오래된 것부터 오므로, 전에는 최근 댓글 50개를 못 봤다
    assert len(gh.members()) == 101


def test_small_lists_still_take_one_request_and_reading_is_capped():
    seen = []
    gh = _fake_github({"/milestones": 5, "/labels": 5000}, seen)
    assert len(gh.milestones()) == 5 and len([s for s in seen if s[0].endswith("/milestones")]) == 1  # 적으면 한 번만 묻는다
    assert len(gh.labels()) == 1000  # 아주 많아도 1,000개(10페이지)에서 멈춘다
