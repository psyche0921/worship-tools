"""3단계: 관리자 전용 API(세트리스트 기록, 설정, 팀원, v1 가져오기)."""
import json
from pathlib import Path

import pytest

from conftest import ADMIN, MEMBER, login

PID = 'PLabcdefghijklmnop'
SONGS = [{'id': 'dQw4w9WgXcQ', 'name': '주의 은혜라', 'notes': ['KEY - A', 'TEMPO - 106']},
         {'id': 'aaaaaaaaaaa', 'name': '', 'notes': []}]


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
    ('delete', '/api/members/a@b.co', None), ('post', '/api/import', {}),
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

def test_setlist_save_list_get_delete(client):
    login(client, ADMIN)
    saved = client.post('/api/setlists', json=setlist_body()).json()
    assert saved['key'] == PID and saved['songs'] == SONGS and saved['rawMessage'].startswith('Setlist')
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


# ---------- v1 기록 가져오기 ----------

V1_EXPORT = {
    'app': 'worship-tools', 'version': 1,
    'setlist': [
        {'key': PID, 'title': 'Setlist 261011', 'url': f'https://www.youtube.com/playlist?list={PID}',
         'at': 1760000000000, 'ok': 2, 'total': 2, 'songs': SONGS},
        {'key': 'bad', 'songs': SONGS},
        'garbage',
    ],
    'shorts': [
        {'key': 's1760100000000', 'id': 'dQw4w9WgXcQ', 'start': 3750, 'end': 6310, 'text': '## 쇼츠 1',
         'at': 1760100000000, 'title': '251012 쇼츠 기획', 'extra': ''},
        {'key': 's1', 'id': 'dQw4w9WgXcQ', 'text': '', 'at': 1},
    ],
    'skillText': '# 지침',
}


def test_import_v1(client):
    login(client, ADMIN)
    r = client.post('/api/import', json=V1_EXPORT).json()
    assert r == {'setlist': {'added': 1, 'skipped': 2}, 'shorts': {'added': 1, 'skipped': 1}, 'skill': '# 지침'}
    # 같은 파일을 다시 가져와도 중복되지 않는다
    r = client.post('/api/import', json=V1_EXPORT).json()
    assert r['setlist']['added'] == 0 and r['shorts']['added'] == 0

    sl = client.get(f'/api/setlists/{PID}').json()
    assert sl['at'] == 1760000000000 and sl['songs'] == SONGS
    [sh] = client.get('/api/shorts').json()['items']
    assert sh['at'] == 1760100000000 and sh['title'] == '251012 쇼츠 기획' and sh['start'] == 3750
    assert client.get(f"/api/shorts/{sh['key']}").json()['text'] == '## 쇼츠 1'
    # SKILL.md는 자동으로 바꾸지 않는다(화면에서 물어본 뒤 PUT /api/settings)
    assert client.get('/api/settings').json()['skill'] == ''


def test_import_fixture_shorts_response(client):
    """실제 쇼츠 응답 샘플(#t= 표기 포함)도 원문 그대로 들어간다."""
    text = (Path(__file__).parent / 'fixtures' / 'shorts-response.md').read_text(encoding='utf-8')
    login(client, ADMIN)
    data = {'shorts': [{'key': 's1760200000000', 'id': 'dQw4w9WgXcQ', 'text': text, 'at': 1760200000000}]}
    assert client.post('/api/import', json=data).json()['shorts']['added'] == 1
    [sh] = client.get('/api/shorts').json()['items']
    assert client.get(f"/api/shorts/{sh['key']}").json()['text'] == text


def test_import_empty(client):
    login(client, ADMIN)
    r = client.post('/api/import', json={}).json()
    assert r == {'setlist': {'added': 0, 'skipped': 0}, 'shorts': {'added': 0, 'skipped': 0}, 'skill': ''}
