"""3단계: 관리자 전용 API(세트리스트 기록, 설정, 팀원)."""
import json

import pytest

from conftest import ADMIN, MEMBER, login

PID = 'PLabcdefghijklmnop'
SONGS = [{'id': 'dQw4w9WgXcQ', 'name': '주의 은혜라', 'notes': ['KEY - A', 'TEMPO - 106'], 'sheets': []},
         {'id': 'aaaaaaaaaaa', 'name': '', 'notes': [], 'sheets': []}]


def setlist_body(**kw):
    body = {'playlistId': PID, 'title': 'Setlist 261018', 'url': f'https://www.youtube.com/playlist?list={PID}',
            'ok': 2, 'total': 2, 'songs': SONGS, 'rawMessage': 'Setlist 261018\n...'}
    body.update(kw)
    return body


ADMIN_ONLY = [
    ('get', '/api/setlists', None), ('get', f'/api/setlists/{PID}', None),
    ('post', '/api/setlists', setlist_body()), ('delete', f'/api/setlists/{PID}', None),
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


# ---------- 세트리스트 ----------

def stored(songs):
    """서버가 상세에 덧붙이는 악보 정보(sheets·needSheets)를 빼고 저장된 값만 본다."""
    return [{k: s[k] for k in ('id', 'name', 'notes')} for s in songs]


def test_setlist_save_list_get_delete(client):
    login(client, ADMIN)
    saved = client.post('/api/setlists', json=setlist_body()).json()
    assert saved['key'] == PID and saved['rawMessage'].startswith('Setlist')
    assert stored(saved['songs']) == stored(SONGS)
    items = client.get('/api/setlists').json()['items']
    assert len(items) == 1 and items[0]['title'] == 'Setlist 261018' and 'rawMessage' not in items[0]
    assert client.delete(f'/api/setlists/{PID}').status_code == 200
    assert client.get(f'/api/setlists/{PID}').status_code == 404
    assert client.delete(f'/api/setlists/{PID}').status_code == 404


def test_setlist_resave_keeps_created_at(client):
    login(client, ADMIN)
    first = client.post('/api/setlists', json=setlist_body(ok=1)).json()
    again = client.post('/api/setlists', json=setlist_body(ok=2, title='Setlist 261025')).json()
    assert again['at'] == first['at'] and again['ok'] == 2 and again['title'] == 'Setlist 261025'
    assert len(client.get('/api/setlists').json()['items']) == 1


@pytest.mark.parametrize('kw', [{'playlistId': 'x'}, {'title': ''}, {'songs': [{'id': 'short'}]}, {'ok': -1}])
def test_setlist_validation(client, kw):
    login(client, ADMIN)
    assert client.post('/api/setlists', json=setlist_body(**kw)).status_code == 422


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

