"""환경 변수에서 설정을 읽는다. GitHub App 값이 없으면 데모 모드로 동작한다."""

import os
from dataclasses import dataclass

DEFAULT_SIGNING_KEY = "dev-only-signing-key"


@dataclass(frozen=True)
class Settings:
    app_id: str | None
    private_key: str | None
    webhook_secret: str | None
    signing_key: str
    access_code: str | None
    anthropic_ready: bool
    default_repo: str
    discord_webhook: str | None
    review_wait_hours: float
    client_id: str | None = None  # GitHub App 사용자 로그인용
    client_secret: str | None = None

    @property
    def login_enabled(self) -> bool:
        """Client ID·secret이 있으면 GitHub 로그인을 쓴다. 로그인한 저장소 팀원만 실행할 수 있다."""
        return bool(self.client_id and self.client_secret)

    @property
    def live(self) -> bool:
        """GitHub App 자격 증명이 있으면 실제 저장소에 연결한다."""
        return bool(self.app_id and self.private_key)


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(ROOT, ".env")


def read_env_file(path: str = ENV_FILE) -> dict[str, str]:
    """KEY=VALUE 줄만 읽는 작은 .env 읽기. 따옴표는 벗긴다."""
    out: dict[str, str] = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def load_env_file(path: str = ENV_FILE, override: bool = False) -> None:
    """로컬 실행용. 이미 설정된 환경 변수는 덮어쓰지 않는다 (override=True면 덮어쓴다)."""
    for k, v in read_env_file(path).items():
        if override or k not in os.environ:
            os.environ[k] = v


def load_settings() -> Settings:
    if not os.getenv("BENTO_NO_DOTENV"):
        load_env_file()
    key = os.getenv("GITHUB_APP_PRIVATE_KEY")
    key_file = os.getenv("GITHUB_APP_PRIVATE_KEY_FILE")
    if key_file and not os.path.isabs(key_file):
        key_file = os.path.join(ROOT, key_file)
    if not key and key_file and os.path.exists(key_file):
        with open(key_file, encoding="utf-8") as f:
            key = f.read()
    if key:
        key = key.replace("\\n", "\n")
    return Settings(
        app_id=os.getenv("GITHUB_APP_ID") or None,
        private_key=key or None,
        webhook_secret=os.getenv("GITHUB_WEBHOOK_SECRET") or None,
        signing_key=os.getenv("BENTO_SIGNING_KEY") or DEFAULT_SIGNING_KEY,
        access_code=os.getenv("BENTO_ACCESS_CODE") or None,
        anthropic_ready=bool(os.getenv("ANTHROPIC_API_KEY")),
        default_repo=os.getenv("BENTO_REPO") or "team-matzip/knu-matzip",
        discord_webhook=os.getenv("DISCORD_WEBHOOK_URL") or None,
        review_wait_hours=float(os.getenv("BENTO_REVIEW_WAIT_HOURS") or 24),
        client_id=os.getenv("GITHUB_CLIENT_ID") or None,
        client_secret=os.getenv("GITHUB_CLIENT_SECRET") or None,
    )
