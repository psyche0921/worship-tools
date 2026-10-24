"""BBC Score(찬양팀 악보 필기 앱) 연동: 악보 버전, 찬양팀 명단, 서버끼리 읽는 길."""
import dataclasses
import io

import pytest
from fastapi.testclient import TestClient

from app.db import connect
from app.main import create_app
from app.sheets import fill_sheet_hashes
from conftest import ADMIN, MEMBER, ORIGIN, fake_verifier, login

KEY = 'score-secret'
SCORE = 'https://score.test'
PDF = b'%PDF-1.4\n% fake sheet\n'
PDF2 = b'%PDF-1.4\n% another version\n'


@pytest.fixture
def client(settings, fake_gemini):
    s = dataclasses.replace(settings, score_api_key=KEY, score_origin=SCORE)
    app = create_app(s, verify_token=fake_verifier, gemini_call=fake_gemini)
    with TestClient(app, base_url=ORIGIN, headers={'Origin': ORIGIN}) as c:
        yield c


def upload(client, title, data=PDF):
    r = client.post('/api/sheets', data={'title': title},
                    files={'file': (f'{title}.pdf', io.BytesIO(data), 'application/pdf')})
    assert r.status_code == 200, r.text
    return r.json()


def make_setlist(client, title='Setlist 261025', name='기대'):
    r = client.post('/api/setlists', json={'title': title, 'songs': [
        {'id': 'dQw4w9WgXcQ', 'name': name, 'notes': ['(KEY - E | TIME - 4/4 | TEMPO - 64)'], 'sheets': []}]})
    assert r.status_code == 200, r.text
    return r.json()


# ---------- 악보 버전 ----------

def test_sheet_version_follows_file_content(client):
    login(client, ADMIN)
    a = upload(client, '기대')
    assert len(a['version']) == 16
    same = upload(client, '기대')           # 같은 파일을 다시 올리면 버전이 그대로
    assert same['id'] == a['id'] and same['version'] == a['version']
    changed = upload(client, '기대', PDF2)  # 내용이 바뀌면 버전이 바뀐다
    assert changed['id'] == a['id'] and changed['version'] != a['version']


def test_share_json_has_sheet_id_version_and_score_url(client):
    login(client, ADMIN)
    sheet = upload(client, '기대')
    token = make_setlist(client)['shareToken']
    client.post('/api/auth/logout')
    d = client.get(f'/api/share/{token}').json()
    s = d['songs'][0]['sheets'][0]
    assert s['id'] == sheet['id'] and s['version'] == sheet['version']
    assert s['url'] == f"/api/share/{token}/sheet/{sheet['id']}"
    assert d['scoreUrl'] == f'{SCORE}/?s={token}'


def test_repeated_song_keeps_sheet_id(client):
    """다른 주에 같은 곡이 나오면 같은 악보 id·version — BBC Score가 필기를 이어 붙이는 근거."""
    login(client, ADMIN)
    upload(client, '새 힘 얻으리')
    t1 = make_setlist(client, 'Setlist 261018', '새 힘 얻으리')['shareToken']
    t2 = make_setlist(client, 'Setlist 261025', '새힘얻으리')['shareToken']
    s1 = client.get(f'/api/share/{t1}').json()['songs'][0]['sheets'][0]
    s2 = client.get(f'/api/share/{t2}').json()['songs'][0]['sheets'][0]
    assert (s1['id'], s1['version']) == (s2['id'], s2['version'])


def test_fill_hashes_for_old_sheets(client, settings):
    login(client, ADMIN)
    sheet = upload(client, '기대')
    conn = connect(settings.db_path)
    try:
        with conn:
            conn.execute("UPDATE sheets SET sha256 = '' WHERE id = ?", (sheet['id'],))
        assert client.get('/api/sheets').json()['items'][0]['version'].startswith('t')
        assert fill_sheet_hashes(conn) == 1
        assert fill_sheet_hashes(conn) == 0
    finally:
        conn.close()
    assert client.get('/api/sheets').json()['items'][0]['version'] == sheet['version']


# ---------- 찬양팀 명단 ----------

def test_admin_manages_team(client, add_member):
    login(client, ADMIN)
    r = client.post('/api/team', json={'email': ' Singer@Example.com ', 'name': '김찬양'})
    assert r.status_code == 200
    t = r.json()
    assert t['linked'] is True and t['scoreOrigin'] == SCORE
    assert t['items'] == [{'email': 'singer@example.com', 'name': '김찬양',
                           'createdAt': t['items'][0]['createdAt'], 'siteRole': ''}]
    assert client.post('/api/team', json={'email': 'singer@example.com'}).status_code == 409
    assert client.post('/api/team', json={'email': 'not-an-email'}).status_code == 400
    # 사이트 팀원과는 따로다. 찬양팀원이라고 사이트에 로그인할 수 있는 것은 아니다.
    client.post('/api/auth/logout')
    assert login(client, 'singer@example.com').status_code == 403
    login(client, ADMIN)
    assert client.delete('/api/team/singer@example.com').json()['items'] == []
    assert client.delete('/api/team/singer@example.com').status_code == 404


def test_team_is_admin_only(client, add_member):
    add_member()
    login(client, MEMBER)
    assert client.get('/api/team').status_code == 403
    assert client.post('/api/team', json={'email': 'x@example.com'}).status_code == 403


# ---------- 서버끼리 (X-Score-Key) ----------

def test_integration_needs_key(client):
    assert client.get('/api/integration/setlists').status_code == 401
    assert client.get('/api/integration/setlists', headers={'X-Score-Key': 'wrong'}).status_code == 401
    assert client.get('/api/integration/setlists', headers={'X-Score-Key': KEY}).status_code == 200


def test_integration_off_without_key(settings, fake_gemini):
    app = create_app(settings, verify_token=fake_verifier, gemini_call=fake_gemini)
    with TestClient(app, base_url=ORIGIN) as c:
        assert c.get('/api/integration/team/a@b.c', headers={'X-Score-Key': ''}).status_code == 404


def test_integration_team_check(client):
    login(client, ADMIN)
    client.post('/api/team', json={'email': 'singer@example.com', 'name': '김찬양'})
    client.post('/api/auth/logout')
    h = {'X-Score-Key': KEY}
    assert client.get('/api/integration/team/Singer@Example.com', headers=h).json() == {
        'member': True, 'email': 'singer@example.com', 'name': '김찬양'}
    assert client.get('/api/integration/team/other@example.com', headers=h).json()['member'] is False


def test_integration_setlists_by_service_date(client):
    login(client, ADMIN)
    make_setlist(client, 'Setlist 261025')
    make_setlist(client, 'Setlist 261018')   # 나중에 만들었지만 예배 날짜는 더 이르다
    client.post('/api/auth/logout')
    items = client.get('/api/integration/setlists', headers={'X-Score-Key': KEY}).json()['items']
    assert [it['title'] for it in items] == ['Setlist 261025', 'Setlist 261018']
    assert items[0]['date'] == '2026-10-25' and items[0]['songCount'] == 1
    assert items[0]['songNames'] == ['기대'] and items[0]['token']
