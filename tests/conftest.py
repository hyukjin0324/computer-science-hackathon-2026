"""테스트는 항상 데모 저장소로만 돈다. 로컬 .env(실제 GitHub 설정)를 절대 읽지 않는다."""

import os

import pytest

os.environ["BENTO_NO_DOTENV"] = "1"
os.environ["BENTO_NO_RENDER"] = "1"  # 테스트에서는 브라우저를 띄우지 않는다
os.environ["BENTO_DEMO_ROLES"] = "0"  # 예시 팀 역할표 없이 시작
for k in ("GITHUB_APP_ID", "GITHUB_APP_PRIVATE_KEY", "GITHUB_APP_PRIVATE_KEY_FILE", "BENTO_ACCESS_CODE", "BENTO_REPO", "ANTHROPIC_API_KEY", "DISCORD_WEBHOOK_URL", "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET", "BENTO_PUBLIC_URL"):
    os.environ.pop(k, None)


def pytest_collection_modifyitems(items):
    """일반 pytest와 브라우저 E2E를 분리한다. CI의 E2E 작업만 명시적으로 켠다."""
    if os.getenv("BENTO_E2E") == "1":
        return
    skip = pytest.mark.skip(reason="BENTO_E2E=1인 브라우저 테스트 작업에서 실행")
    for item in items:
        if "e2e" in item.keywords:
            item.add_marker(skip)
