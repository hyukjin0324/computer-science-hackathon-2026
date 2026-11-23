"""GitHub App으로 저장소를 읽고 쓰는 얇은 클라이언트.

Bento가 쓰는 권한: Contents 읽기, Pull requests·Issues 읽기/쓰기, Checks 읽기.
저장소 초대 확인에는 Administration 읽기, 발송에는 Administration 쓰기 권한을 쓴다.
머지·푸시에 필요한 Contents 쓰기 권한은 받지 않는다.
"""

import time

import httpx
import jwt

from .config import Settings

API = "https://api.github.com"


class GitHubError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class GitHub:
    def __init__(self, settings: Settings, repo: str, http: httpx.Client | None = None):
        self.settings = settings
        self.repo = repo
        self.owner, self.name = repo.split("/", 1)
        self._token: str | None = None
        self._token_exp = 0.0
        self.http = http or httpx.Client(
            base_url=API,
            timeout=15,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "bento-agent",
            },
        )

    # ---------- 인증 ----------
    def _app_jwt(self) -> str:
        now = int(time.time())
        return jwt.encode({"iat": now - 60, "exp": now + 540, "iss": self.settings.app_id}, self.settings.private_key, algorithm="RS256")

    def _installation_token(self) -> str:
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        headers = {"Authorization": f"Bearer {self._app_jwt()}"}
        inst = self._request("GET", f"/repos/{self.owner}/{self.name}/installation", headers=headers)
        tok = self._request("POST", f"/app/installations/{inst['id']}/access_tokens", headers=headers)
        self._token, self._token_exp = tok["token"], time.time() + 3300
        return self._token

    def _request(self, method: str, path: str, headers: dict | None = None, **kw):
        r = self.http.request(method, path, headers=headers, **kw)
        if r.status_code >= 400:
            raise GitHubError(f"{method} {path} → {r.status_code}: {r.text[:200]}", r.status_code)
        return r.json() if r.content else {}

    def api(self, method: str, path: str, **kw):
        return self._request(method, path, headers={"Authorization": f"Bearer {self._installation_token()}"}, **kw)

    def _r(self, p: str = "") -> str:
        return f"/repos/{self.owner}/{self.name}{p}"

    MAX_PAGES = 10  # 목록은 100개씩 최대 1,000개까지 읽는다 (GitHub 호출이 끝없이 늘지 않게)

    def api_all(self, path: str, params: dict | None = None) -> list:
        """목록을 끝까지 읽는다. GitHub는 한 번에 최대 100개만 주므로, 꽉 찬 페이지가 오면 다음 페이지를 더 읽는다."""
        out: list = []
        for page in range(1, self.MAX_PAGES + 1):
            batch = self.api("GET", path, params={**(params or {}), "per_page": 100, "page": page})
            out += batch
            if len(batch) < 100:
                break
        return out

    # ---------- 읽기 ----------
    def open_prs(self):
        return self.api_all(self._r("/pulls"), {"state": "open"})

    def pr_detail(self, n: int):
        return self.api("GET", self._r(f"/pulls/{n}"))

    def merged_prs(self):
        return [p for p in self.api("GET", self._r("/pulls"), params={"state": "closed", "per_page": 30, "sort": "updated", "direction": "desc"}) if p.get("merged_at")]

    def pr_reviews(self, n: int):
        return self.api("GET", self._r(f"/pulls/{n}/reviews"), params={"per_page": 100})

    def pr_files(self, n: int):
        return [f["filename"] for f in self.api("GET", self._r(f"/pulls/{n}/files"), params={"per_page": 100})]

    def timeline(self, n: int):
        return self.api("GET", self._r(f"/issues/{n}/timeline"), params={"per_page": 100})

    def check_runs(self, sha: str):
        return self.api("GET", self._r(f"/commits/{sha}/check-runs"), params={"per_page": 50}).get("check_runs", [])

    def file_authors(self, path: str) -> list[str]:
        commits = self.api("GET", self._r("/commits"), params={"path": path, "per_page": 10})
        return [c["author"]["login"] for c in commits if c.get("author")]

    def open_issues(self):
        items = self.api_all(self._r("/issues"), {"state": "open"})
        return [i for i in items if "pull_request" not in i]

    def closed_issues(self, since: str):
        """since(ISO) 뒤에 바뀐 닫힌 이슈 (홈에서 끝낸 일을 줄 그어 보여 주려고). 한 쪽(100개)만 읽는다"""
        items = self.api("GET", self._r("/issues"), params={"state": "closed", "since": since, "per_page": 100})
        return [i for i in items if "pull_request" not in i]

    def milestones(self):
        return self.api_all(self._r("/milestones"), {"state": "open"})

    def leads(self) -> list[str]:
        """팀장: 저장소 관리자(admin). 읽을 수 없으면 저장소 주인."""
        try:
            people = self.api_all(self._r("/collaborators"))
            admins = [u["login"] for u in people if (u.get("permissions") or {}).get("admin")]
            if admins:
                return admins
        except GitHubError:
            pass
        return [self.owner]

    def user_name(self, login: str) -> str | None:
        return (self.api("GET", f"/users/{login}") or {}).get("name")

    def members(self) -> list[str]:
        return [u["login"] for u in self.api_all(self._r("/assignees"))]

    def collaborator_permission(self, login: str) -> str:
        data = self.api("GET", self._r(f"/collaborators/{login}/permission"))
        return data.get("permission") or "none"

    def is_collaborator(self, login: str) -> bool:
        try:
            self.api("GET", self._r(f"/collaborators/{login}"))
        except GitHubError as e:
            if e.status == 404:
                return False
            raise
        return True

    # 팀 상태 신호 읽기: 커밋·활동·초대·파일 목록 (모두 읽기만 한다)
    def commits(self, branch: str | None = None, since: str | None = None, per_page: int = 50):
        params = {"per_page": per_page}
        if branch:
            params["sha"] = branch
        if since:
            params["since"] = since
        return self.api("GET", self._r("/commits"), params=params)

    def commit(self, sha: str):
        return self.api("GET", self._r(f"/commits/{sha}"))

    def tree(self, sha: str):
        r = self.api("GET", self._r(f"/git/trees/{sha}"), params={"recursive": "1"})
        return [t for t in r.get("tree", []) if t.get("type") == "blob"]

    def events(self):
        return self.api("GET", self._r("/events"), params={"per_page": 100})

    def invitations(self):
        return self.api_all(self._r("/invitations"))

    def pr_commits(self, n: int):
        return self.api("GET", self._r(f"/pulls/{n}/commits"), params={"per_page": 100})

    # 제출 점검용 읽기 (Contents·Metadata 읽기 권한)
    def repo_info(self):
        return self.api("GET", self._r())

    def head_sha(self, branch: str) -> str:
        return self.api("GET", self._r(f"/branches/{branch}"))["commit"]["sha"]

    def readme(self) -> str | None:
        try:
            r = self.api("GET", self._r("/readme"))
        except GitHubError as e:
            if e.status == 404:
                return None
            raise
        import base64
        return base64.b64decode(r.get("content") or "").decode("utf-8", errors="replace")

    def compare(self, base: str, head: str):
        r = self.api("GET", self._r(f"/compare/{base}...{head}"))
        return {"ahead_by": r.get("ahead_by", 0), "files": [f["filename"] for f in r.get("files", [])][:50]}

    def issue(self, n: int):
        return self.api("GET", self._r(f"/issues/{n}"))

    def issue_comments(self, n: int):
        return self.api_all(self._r(f"/issues/{n}/comments"))

    def recent_comments(self, since: str):
        """저장소 전체 이슈·PR의 최근 댓글 (since 이후). 안부 댓글·답장을 한 번에 본다. 이벤트 목록보다 늦게 반영되지 않는다."""
        return self.api("GET", self._r("/issues/comments"), params={"since": since, "sort": "created", "direction": "desc", "per_page": 100})

    # 라벨: 서버 여러 대가 함께 쓰는 잠금·기록 장소 (Issues 쓰기 권한으로 충분하다)
    def create_label(self, name: str, color: str, description: str):
        return self.api("POST", self._r("/labels"), json={"name": name, "color": color, "description": description})

    def labels(self):
        return self.api_all(self._r("/labels"))

    def get_label(self, name: str):
        return self.api("GET", self._r(f"/labels/{name}"))

    def update_label(self, name: str, description: str):
        return self.api("PATCH", self._r(f"/labels/{name}"), json={"description": description})

    def delete_label(self, name: str):
        return self.api("DELETE", self._r(f"/labels/{name}"))

    def comment_reactions(self, comment_id: int):
        return self.api("GET", self._r(f"/issues/comments/{comment_id}/reactions"), params={"per_page": 100})

    def issue_reactions(self, n: int):
        return self.api("GET", self._r(f"/issues/{n}/reactions"), params={"per_page": 100})

    # ---------- 쓰기 (승인 뒤에만 호출) ----------
    def add_collaborator(self, login: str) -> dict:
        # GitHub의 기본 역할은 쓰기이며, permission 필드는 조직 저장소에만 유효하다.
        return self.api("PUT", self._r(f"/collaborators/{login}"))

    def create_milestone(self, title: str, due_on: str | None, description: str):
        body = {"title": title, "description": description}
        if due_on:
            body["due_on"] = due_on
        return self.api("POST", self._r("/milestones"), json=body)

    def create_issue(self, title: str, body: str, assignees: list[str], milestone: int | None):
        payload = {"title": title, "body": body, "assignees": assignees}
        if milestone:
            payload["milestone"] = milestone
        return self.api("POST", self._r("/issues"), json=payload)

    def update_milestone(self, number: int, due_on: str, description: str | None = None):
        body = {"due_on": due_on} | ({"description": description} if description is not None else {})
        return self.api("PATCH", self._r(f"/milestones/{number}"), json=body)

    def update_issue(self, n: int, **fields):
        return self.api("PATCH", self._r(f"/issues/{n}"), json=fields)

    def add_assignees(self, n: int, users: list[str]):
        return self.api("POST", self._r(f"/issues/{n}/assignees"), json={"assignees": users})

    def request_reviewers(self, n: int, users: list[str]):
        return self.api("POST", self._r(f"/pulls/{n}/requested_reviewers"), json={"reviewers": users})

    def comment(self, n: int, body: str):
        return self.api("POST", self._r(f"/issues/{n}/comments"), json={"body": body})

    def html(self, path: str = "") -> str:
        return f"https://github.com/{self.repo}{path}"


_INSTALLED: dict = {"at": 0.0, "repos": []}


def installed_repos(settings: Settings, max_age: float = 60) -> list[str]:
    """이 앱이 설치된 저장소 목록. 화면을 열 때마다 부르므로 잠깐 기억해 둔다."""
    if time.time() - _INSTALLED["at"] < max_age:
        return list(_INSTALLED["repos"])
    gh = GitHub(settings, "x/y")
    app_auth = {"Authorization": f"Bearer {gh._app_jwt()}"}
    repos: list[str] = []
    for inst in gh._request("GET", "/app/installations", headers=app_auth):
        tok = gh._request("POST", f"/app/installations/{inst['id']}/access_tokens", headers=app_auth)["token"]
        data = gh._request("GET", "/installation/repositories", headers={"Authorization": f"Bearer {tok}"}, params={"per_page": 100})
        repos += [r["full_name"] for r in data.get("repositories", [])]
    _INSTALLED.update(at=time.time(), repos=repos)
    return repos


def installation_repos(settings: Settings, installation_id: int) -> list[str]:
    """방금 설치(또는 설치 변경)한 곳의 저장소들. 이 앱의 설치가 아니면 GitHub가 404를 준다."""
    gh = GitHub(settings, "x/y")
    app_auth = {"Authorization": f"Bearer {gh._app_jwt()}"}
    tok = gh._request("POST", f"/app/installations/{int(installation_id)}/access_tokens", headers=app_auth)["token"]
    data = gh._request("GET", "/installation/repositories", headers={"Authorization": f"Bearer {tok}"}, params={"per_page": 100})
    _INSTALLED["at"] = 0  # 설치 목록이 바뀌었으니 다음에 새로 읽는다
    return [r["full_name"] for r in data.get("repositories", [])]
