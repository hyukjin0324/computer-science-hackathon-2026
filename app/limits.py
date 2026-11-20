"""AI 기능 횟수 제한 (사람별 1시간 · 서버 전체 하루). 대화·파일 읽기·웹 검색·공고 분석이 같이 쓴다.

세는 곳(hits·day)과 한도 값은 각 모듈에 그대로 두고, 세는 방법만 여기 하나로 모았다.
(서버리스에서는 인스턴스마다 따로 센다. 여러 서버가 함께 세려면 공용 저장소가 필요하다.)
"""

import time


def take(lock, hits: dict, day: dict, who: str, now: float, per_hour: int, per_day: int, too_many_today: Exception, too_often: Exception) -> None:
    """한도 안이면 한 번 센다. 하루 한도를 넘으면 too_many_today, 1시간 한도를 넘으면 too_often을 던진다."""
    today = time.strftime("%Y-%m-%d", time.gmtime(now))
    with lock:
        if day["key"] != today:
            day.update(key=today, n=0)
        if day["n"] >= per_day:
            raise too_many_today
        recent = [t for t in hits.get(who, []) if now - t < 3600]
        if len(recent) >= per_hour:
            raise too_often
        hits[who] = recent + [now]
        day["n"] += 1
