"""1단계: 로그인, 세션, 역할, Origin 검사."""
from app import auth
from app.db import now_ms
from conftest import ADMIN, CLIENT_ID, MEMBER, login


def test_health_and_config(client):
    assert client.get('/api/health').json() == {'ok': True}
    assert client.get('/api/config').json() == {'clientId': CLIENT_ID}


def test_me_requires_login(client):
    r = client.get('/api/me')
    assert r.status_code == 401
    assert r.json()['detail'] == '로그인이 필요해요.'


def test_admin_seeded_and_logs_in(client):
    r = login(client, ADMIN)
    assert r.status_code == 200
    assert r.json()['role'] == 'admin'
    me = client.get('/api/me').json()
    assert me == {'email': ADMIN, 'name': 'admin', 'role': 'admin'}


def test_cookie_flags(client):
    r = login(client, ADMIN)
    cookie = r.headers['set-cookie']
    assert cookie.startswith(auth.COOKIE + '=')
    for flag in ('HttpOnly', 'Secure', 'SameSite=lax', 'Max-Age=2592000', 'Path=/'):
        assert flag.lower() in cookie.lower()


def test_session_token_stored_hashed(client, db):
    login(client, ADMIN)
    token = client.cookies.get(auth.COOKIE)
    stored = [r[0] for r in db.execute('SELECT token_hash FROM sessions')]
    assert token not in stored
    assert stored == [auth._hash(token)]


def test_email_is_case_insensitive(client):
    r = client.post('/api/auth/google', json={'credential': 'ok:Admin@Example.com'})
    assert r.status_code == 200 and r.json()['email'] == ADMIN


def test_unknown_email_rejected(client, db):
    r = login(client, 'stranger@example.com')
    assert r.status_code == 403
    assert '등록되지 않은 계정' in r.json()['detail']
    assert db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] == 0
    assert client.get('/api/me').status_code == 401


def test_bad_token_rejected(client):
    r = client.post('/api/auth/google', json={'credential': 'garbage'})
    assert r.status_code == 401


def test_unverified_email_rejected(client):
    r = client.post('/api/auth/google', json={'credential': f'unverified:{ADMIN}'})
    assert r.status_code == 401


def test_member_role(client, add_member):
    add_member()
    r = login(client, MEMBER)
    assert r.status_code == 200 and r.json()['role'] == 'member'


def test_name_from_google_only_when_empty(client, add_member):
    add_member(name='찬양팀 홍길동')
    assert login(client, MEMBER).json()['name'] == '찬양팀 홍길동'
    assert client.get('/api/me').json()['name'] == '찬양팀 홍길동'


def test_logout_invalidates_session(client, db):
    login(client, ADMIN)
    token = client.cookies.get(auth.COOKIE)
    assert client.post('/api/auth/logout').status_code == 200
    assert db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] == 0
    # 쿠키를 그대로 다시 보내도 통하지 않아야 한다.
    client.cookies.set(auth.COOKIE, token, domain='bbc.test')
    assert client.get('/api/me').status_code == 401


def test_expired_session_rejected(client, db):
    login(client, ADMIN)
    with db:
        db.execute('UPDATE sessions SET expires_at = ?', (now_ms() - 1,))
    assert client.get('/api/me').status_code == 401


def test_removed_user_loses_session(client, db, add_member):
    add_member()
    login(client, MEMBER)
    with db:
        db.execute('DELETE FROM users WHERE email = ?', (MEMBER,))
    assert client.get('/api/me').status_code == 401


def test_admin_seed_promotes_existing_member(settings, add_member, app):
    from fastapi.testclient import TestClient

    add_member('second@example.com')
    settings.admin_emails.append('second@example.com')  # 서버 재시작 시 ADMIN_EMAILS에 추가된 상황
    with TestClient(app, base_url=settings.public_origin, headers={'Origin': settings.public_origin}) as c:
        assert login(c, 'second@example.com').json()['role'] == 'admin'


def test_origin_required_for_writes(client):
    for headers in ({'Origin': 'https://evil.example'}, {'Origin': ''}):
        r = client.post('/api/auth/google', json={'credential': f'ok:{ADMIN}'}, headers=headers)
        assert r.status_code == 403
    # Origin 헤더가 아예 없는 경우
    del client.headers['Origin']
    r = client.post('/api/auth/google', json={'credential': f'ok:{ADMIN}'})
    assert r.status_code == 403


def test_reads_do_not_need_origin(client):
    del client.headers['Origin']
    assert client.get('/api/config').status_code == 200


def test_require_admin(client, add_member):
    add_member()
    assert client.get('/api/members').status_code == 401
    login(client, MEMBER)
    r = client.get('/api/members')
    assert r.status_code == 403 and r.json()['detail'] == '관리자만 사용할 수 있어요.'
    login(client, ADMIN)
    assert client.get('/api/members').status_code == 200


def test_schema_version(db):
    assert db.execute('PRAGMA user_version').fetchone()[0] == 1
    assert db.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
