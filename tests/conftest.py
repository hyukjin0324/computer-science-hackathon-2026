"""테스트는 항상 데모 저장소로만 돈다. 로컬 .env(실제 GitHub 설정)를 절대 읽지 않는다."""

import os

os.environ["BENTO_NO_DOTENV"] = "1"
for k in ("GITHUB_APP_ID", "GITHUB_APP_PRIVATE_KEY", "GITHUB_APP_PRIVATE_KEY_FILE", "BENTO_ACCESS_CODE", "BENTO_REPO", "ANTHROPIC_API_KEY", "DISCORD_WEBHOOK_URL", "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET", "BENTO_PUBLIC_URL"):
    os.environ.pop(k, None)
