"""v2 서버 테스트 공통 준비.

Google ID 토큰 검증은 가짜로 바꾼다. 토큰 문자열 형식:
  'ok:<email>'        → 검증 통과, email_verified=True
  'unverified:<email>' → 검증 통과, email_verified=False
  그 밖               → 검증 실패
"""
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
        google_client_id=CLIENT_ID,
        admin_emails=[ADMIN],
        public_origin=ORIGIN,
    )


@pytest.fixture
def app(settings):
    return create_app(settings, verify_token=fake_verifier)


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
