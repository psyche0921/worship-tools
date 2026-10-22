"""v2 서버 테스트 공통 준비.

Google ID 토큰 검증은 가짜로 바꾼다. 토큰 문자열 형식:
  'ok:<email>'        → 검증 통과, email_verified=True
  'unverified:<email>' → 검증 통과, email_verified=False
  그 밖               → 검증 실패
"""
import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import connect, now_ms
from app.main import create_app

ORIGIN = 'https://bbc.test'
CLIENT_ID = 'test-client.apps.googleusercontent.com'
ADMIN = 'admin@example.com'
MEMBER = 'member@example.com'


def fake_verifier(credential: str, client_id: str) -> dict:
    assert client_id == CLIENT_ID
    kind, _, email = credential.partition(':')
    if kind == 'ok':
        return {'email': email, 'email_verified': True, 'name': email.split('@')[0]}
    if kind == 'unverified':
        return {'email': email, 'email_verified': False}
    raise ValueError('bad token')


@pytest.fixture
def settings(tmp_path):
    return Settings(
        db_path=str(tmp_path / 'worship.db'),
        sheets_dir=str(tmp_path / 'sheets'),
        google_client_id=CLIENT_ID,
        google_picker_key='picker-key',
        admin_emails=[ADMIN],
        public_origin=ORIGIN,
    )


PLAN_REPLY = """## 쇼츠 1: 은혜의 자리
- 구간: 0:40:12 ~ 0:41:05 (#t=0h40m12s)
"""


class FakeGemini:
    """Gemini 대신 쓰는 가짜. replies에 넣은 순서대로 답하고, 예외를 넣으면 그 예외를 낸다.
    hold=True인 동안은 응답을 미뤄서 '진행 중' 상태를 만들 수 있다."""

    def __init__(self):
        self.replies: list = []
        self.calls: list[tuple[str, dict]] = []
        self.hold = False

    async def __call__(self, model: str, body: dict) -> str:
        self.calls.append((model, body))
        while self.hold:
            await asyncio.sleep(0.01)
        reply = self.replies.pop(0) if self.replies else PLAN_REPLY
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def fake_gemini():
    return FakeGemini()


@pytest.fixture
def app(settings, fake_gemini):
    return create_app(settings, verify_token=fake_verifier, gemini_call=fake_gemini)


@pytest.fixture
def client(app):
    with TestClient(app, base_url=ORIGIN, headers={'Origin': ORIGIN}) as c:
        yield c


@pytest.fixture
def db(settings, client):
    conn = connect(settings.db_path)
    yield conn
    conn.close()


@pytest.fixture
def add_member(db):
    def add(email=MEMBER, name=''):
        with db:
            db.execute("INSERT INTO users (email, name, role, created_at) VALUES (?, ?, 'member', ?)",
                       (email, name, now_ms()))
        return email
    return add


def login(client, email):
    return client.post('/api/auth/google', json={'credential': f'ok:{email}'})


def wait_job(client, job_id, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        j = client.get(f'/api/jobs/{job_id}').json()
        if j['status'] in ('done', 'error'):
            return j
        time.sleep(0.02)
    raise AssertionError(f'job {job_id} did not finish: {j}')
