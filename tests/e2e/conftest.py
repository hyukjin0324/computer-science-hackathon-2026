"""실제 브라우저로 Bento의 핵심 사용자 흐름을 확인한다."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def app_url():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    env = os.environ.copy()
    env.update(BENTO_NO_DOTENV="1", BENTO_NO_RENDER="1", BENTO_DEMO_ROLES="0")
    for key in (
        "GITHUB_APP_ID", "GITHUB_APP_PRIVATE_KEY", "GITHUB_APP_PRIVATE_KEY_FILE",
        "BENTO_ACCESS_CODE", "BENTO_REPO", "ANTHROPIC_API_KEY", "DISCORD_WEBHOOK_URL",
        "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET", "BENTO_PUBLIC_URL",
    ):
        env.pop(key, None)

    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            if process.poll() is not None:
                pytest.fail("Bento 테스트 서버가 시작되기 전에 종료됐어요")
            try:
                if httpx.get(url + "/api/health", timeout=0.2).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.05)
        else:
            pytest.fail("Bento 테스트 서버가 5초 안에 시작되지 않았어요")
        yield url
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={"width": 393, "height": 852})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(f"pageerror: {error}"))
    page.on("console", lambda message: errors.append(f"console: {message.text}") if message.type == "error" else None)
    yield page
    context.close()
    assert not errors, "\n".join(errors)
