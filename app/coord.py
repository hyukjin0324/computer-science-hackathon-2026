"""팀원 여럿이 서로 다른 서버(Vercel 인스턴스)에서 동시에 승인할 때를 위한 조정.

DB 없이 GitHub 저장소 자체를 공유 장소로 쓴다.
- 잠금: 같은 이름의 라벨은 하나만 만들 수 있다(두 번째는 422). 먼저 만든 서버만 실행한다.
- 실행 기록: 최근에 실행한 제안의 키를 라벨 설명에 적어 둔다. 어느 서버든 읽을 수 있다.
"""

import time

from .github import GitHubError

LOCK = "bento-lock"
DONE = "bento-done"
COLOR = "EDEDED"
TTL = 90  # 실행 중에 서버가 죽어도 이 시간이 지나면 잠금을 풀어 준다
KEEP = 8  # 라벨 설명은 100자까지라 최근 8개만 기억한다


class Busy(Exception):
    pass


def acquire(gh, now=time.time) -> None:
    for _ in range(2):
        try:
            gh.create_label(LOCK, COLOR, f"{int(now())} Bento가 실행 중이에요 (곧 사라져요)")
            return
        except GitHubError as e:
            if e.status != 422:
                raise
        try:
            since = int((gh.get_label(LOCK).get("description") or "0").split()[0])
        except (GitHubError, ValueError, IndexError):
            since = 0
        if now() - since <= TTL:
            raise Busy()
        try:  # 오래된 잠금 = 실행하다 죽은 서버. 치우고 한 번 더 시도한다
            gh.delete_label(LOCK)
        except GitHubError:
            pass
    raise Busy()


def release(gh) -> None:
    try:
        gh.delete_label(LOCK)
    except GitHubError:
        pass


def recent(gh) -> list[str]:
    try:
        desc = gh.get_label(DONE).get("description") or ""
    except GitHubError:
        return []
    return [k for k in desc.split(",") if k]


def mark_done(gh, key: str) -> None:
    keys = [key[:10]] + [k for k in recent(gh) if k != key[:10]][: KEEP - 1]
    desc = ",".join(keys)
    try:
        gh.update_label(DONE, desc)
    except GitHubError:
        try:
            gh.create_label(DONE, COLOR, desc)
        except GitHubError:
            pass
