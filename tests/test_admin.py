"""3단계: 관리자 전용 API(설정, 팀원). 세트리스트는 tests/test_setlists.py."""

import pytest

from conftest import ADMIN, MEMBER, login


ADMIN_ONLY = [
    ('get', '/api/settings', None), ('put', '/api/settings', {'model': 'x'}),
    ('get', '/api/members', None), ('post', '/api/members', {'email': 'a@b.co'}),
    ('delete', '/api/members/a@b.co', None),
]


@pytest.mark.parametrize('method, url, body', ADMIN_ONLY)
def test_admin_only_endpoints(client, add_member, method, url, body):
    kw = {'json': body} if body is not None else {}
    assert client.request(method, url, **kw).status_code == 401
    add_member()
    login(client, MEMBER)
    r = client.request(method, url, **kw)
    assert r.status_code == 403 and r.json()['detail'] == '관리자만 사용할 수 있어요.'


# ---------- 설정 ----------

def test_settings_roundtrip(client):
    login(client, ADMIN)
    s = client.get('/api/settings').json()
    assert s['skill'] == '' and s['model'] == '' and s['defaultModel'] == 'gemini-3.8-flash'
    assert s['geminiKeySet'] is False
    s = client.put('/api/settings', json={'skill': '# 지침', 'model': 'gemini-test'}).json()
    assert s['skill'] == '# 지침' and s['model'] == 'gemini-test' and s['skillUpdatedBy'] == ADMIN
    # 모델만 바꾸면 SKILL.md는 그대로
    s = client.put('/api/settings', json={'model': ''}).json()
    assert s['skill'] == '# 지침' and s['model'] == ''


def test_settings_bad_model(client):
    login(client, ADMIN)
    r = client.put('/api/settings', json={'model': 'models/../x y'})
    assert r.status_code == 400


# ---------- 팀원 ----------

def test_members_add_list_remove(client, db):
    login(client, ADMIN)
    members = client.post('/api/members', json={'email': ' Kim@Example.com ', 'name': '김집사'}).json()
    assert [(m['email'], m['role']) for m in members] == [(ADMIN, 'admin'), ('kim@example.com', 'member')]
    assert client.post('/api/members', json={'email': 'kim@example.com'}).status_code == 409
    assert client.post('/api/members', json={'email': 'not-an-email'}).status_code == 400

    # 등록한 팀원이 로그인하면 설정한 이름을 유지한다
    login(client, 'kim@example.com')
    assert client.get('/api/me').json() == {'email': 'kim@example.com', 'name': '김집사', 'role': 'member'}

    login(client, ADMIN)
    members = client.delete('/api/members/kim@example.com').json()
    assert [m['email'] for m in members] == [ADMIN]
    assert db.execute("SELECT COUNT(*) FROM sessions WHERE email = 'kim@example.com'").fetchone()[0] == 0
    assert client.delete('/api/members/kim@example.com').status_code == 404


def test_cannot_remove_admin(client):
    login(client, ADMIN)
    r = client.delete(f'/api/members/{ADMIN}')
    assert r.status_code == 400 and '관리자' in r.json()['detail']


def test_removed_member_is_logged_out(client, add_member):
    from fastapi.testclient import TestClient

    add_member()
    login(client, ADMIN)
    with TestClient(client.app, base_url='https://bbc.test', headers={'Origin': 'https://bbc.test'}) as member:
        login(member, MEMBER)
        assert member.get('/api/me').status_code == 200
        client.delete(f'/api/members/{MEMBER}')
        assert member.get('/api/me').status_code == 401

