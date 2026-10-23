"""세트리스트와 공유 페이지. 재생목록은 더 만들지 않고 결과물은 공유 링크다."""
import pytest

from app.setlistmeta import parse
from conftest import ADMIN, MEMBER, login

SONGS = [{'id': 'dQw4w9WgXcQ', 'name': '주의 은혜라',
          'notes': ['(KEY - A | TIME - 4/4 | TEMPO - 106)', 'Intro(8) - V*4 - PreC - C'], 'sheets': []},
         {'id': 'aaaaaaaaaaa', 'name': '나로부터 & 기대', 'notes': [], 'sheets': []}]

PDF = b'%PDF-1.4\n% fake sheet\n'


def body(**kw):
    out = {'title': 'Setlist 261018', 'songs': SONGS, 'rawMessage': 'Setlist 261018\n...'}
    out.update(kw)
    return out


def make(client, **kw):
    r = client.post('/api/setlists', json=body(**kw))
    assert r.status_code == 200, r.text
    return r.json()


def stored(songs):
    """상세에 덧붙는 악보 정보를 빼고 저장된 값만 본다."""
    return [{k: s[k] for k in ('id', 'name', 'notes')} for s in songs]


# ---------- 만들기 ----------

def test_create_returns_share_link(client):
    login(client, ADMIN)
    it = make(client)
    assert it['shareToken'] and len(it['shareToken']) >= 16
    assert it['key'] == it['shareToken']
    assert it['shareUrl'].endswith('/s/' + it['shareToken'])
    assert it['total'] == 2 and stored(it['songs']) == stored(SONGS)
    assert it['rawMessage'].startswith('Setlist')
    # 유튜브 재생목록은 만들지 않는다
    assert it['playlistId'] == '' and it['url'] == ''


def test_each_create_is_a_new_setlist(client):
    login(client, ADMIN)
    a, b = make(client), make(client, title='Setlist 261025')
    assert a['shareToken'] != b['shareToken']
    assert len(client.get('/api/setlists').json()['items']) == 2


def test_member_can_create_and_delete_own(client, add_member):
    add_member()
    login(client, MEMBER)
    it = make(client)
    assert it['canDelete'] is True
    assert 'rawMessage' not in it   # 원본 메시지는 관리자에게만
    assert client.delete(f"/api/setlists/{it['shareToken']}").status_code == 200


def test_member_cannot_delete_others(client, add_member):
    login(client, ADMIN)
    it = make(client)
    client.post('/api/auth/logout')
    add_member()
    login(client, MEMBER)
    assert client.get(f"/api/setlists/{it['shareToken']}").json()['canDelete'] is False
    assert client.delete(f"/api/setlists/{it['shareToken']}").status_code == 403


def test_admin_can_delete_any(client, add_member):
    add_member()
    login(client, MEMBER)
    it = make(client)
    client.post('/api/auth/logout')
    login(client, ADMIN)
    assert client.delete(f"/api/setlists/{it['shareToken']}").status_code == 200


@pytest.mark.parametrize('kw', [{'title': ''}, {'songs': []}, {'songs': [{'id': 'short'}]}])
def test_validation(client, kw):
    login(client, ADMIN)
    assert client.post('/api/setlists', json=body(**kw)).status_code == 422


def test_login_required(client):
    assert client.post('/api/setlists', json=body()).status_code == 401
    assert client.get('/api/setlists').status_code == 401


# ---------- 메모에서 조성·박자·BPM·구조 뽑기 ----------

@pytest.mark.parametrize('notes, want', [
    (['(KEY - A | TIME - 4/4 | TEMPO - 106)'], {'key': 'A', 'timeSignature': '4/4', 'tempo': 106}),
    (['(KEY - Bb  | TIME - 4/4 | TEMPO - 104)'], {'key': 'Bb', 'timeSignature': '4/4', 'tempo': 104}),
    (['KEY - Em'], {'key': 'Em', 'timeSignature': '', 'tempo': None}),
    ([], {'key': '', 'timeSignature': '', 'tempo': None}),
])
def test_parse_memo(notes, want):
    got = parse(notes)
    assert {k: got[k] for k in want} == want


def test_parse_structure():
    notes = ['(KEY - A | TIME - 4/4 | TEMPO - 106)',
             'Intro(8) - V*4 - PreC - C - V*2 - PreC*2 - C - PecC*3 - C*2 - 새 힘 얻으리 *4']
    got = parse(notes)
    assert got['structure'].startswith('Intro(8) - V*4 - PreC')
    assert got['key'] == 'A'


def test_parse_ignores_key_line_as_structure():
    # KEY 줄에도 '-'가 많다. 구조로 오해하면 안 된다.
    assert parse(['(KEY - A | TIME - 4/4 | TEMPO - 106)'])['structure'] == ''


# ---------- 공유 페이지 (로그인 없이) ----------

def upload(client, title, data=PDF, name='sheet.pdf'):
    import io
    return client.post('/api/sheets', data={'title': title},
                       files={'file': (name, io.BytesIO(data), 'application/pdf')}).json()


def test_share_json_is_public(client):
    login(client, ADMIN)
    it = make(client)
    token = it['shareToken']
    client.post('/api/auth/logout')

    d = client.get(f'/api/share/{token}')
    assert d.status_code == 200
    d = d.json()
    assert d['title'] == 'Setlist 261018' and d['token'] == token
    assert 'rawMessage' not in d            # 단톡방 원문은 내보내지 않는다
    assert d['weekLabel'] and d['date']
    assert d['watchAllUrl'] == ('https://www.youtube.com/watch_videos?video_ids='
                                'dQw4w9WgXcQ,aaaaaaaaaaa')
    first = d['songs'][0]
    assert first['videoId'] == 'dQw4w9WgXcQ'
    assert first['youtubeUrl'] == 'https://youtu.be/dQw4w9WgXcQ'
    assert first['key'] == 'A' and first['tempo'] == 106 and first['timeSignature'] == '4/4'
    assert first['structure'].startswith('Intro(8)')
    assert first['titles'] == ['주의 은혜라']
    # 메들리는 곡마다 쪼개 둔다(악보 하나가 곡 하나)
    assert d['songs'][1]['titles'] == ['나로부터', '기대']
    assert d['sheetsZipUrl'] == ''          # 악보가 없으면 빈 값


def test_share_unknown_token(client):
    assert client.get('/api/share/nope').status_code == 404


def test_share_sheet_download_without_login(client):
    login(client, ADMIN)
    sheet = upload(client, '주의 은혜라')
    it = make(client)
    token = it['shareToken']
    client.post('/api/auth/logout')

    d = client.get(f'/api/share/{token}').json()
    url = d['songs'][0]['sheets'][0]['url']
    assert url == f"/api/share/{token}/sheet/{sheet['id']}"
    r = client.get(url)
    assert r.status_code == 200 and r.content == PDF
    assert d['sheetsZipUrl'] == f'/api/share/{token}/sheets.zip'
    assert client.get(d['sheetsZipUrl']).status_code == 200


def test_share_cannot_reach_other_sheets(client):
    """공유 토큰으로 그 세트리스트에 없는 악보를 엿볼 수 없다."""
    login(client, ADMIN)
    mine = upload(client, '주의 은혜라')
    other = upload(client, '전혀 다른 곡')
    it = make(client)
    client.post('/api/auth/logout')
    token = it['shareToken']
    assert client.get(f"/api/share/{token}/sheet/{mine['id']}").status_code == 200
    assert client.get(f"/api/share/{token}/sheet/{other['id']}").status_code == 404
    # 로그인하지 않은 사람은 원래 경로로도 못 받는다
    assert client.get(f"/api/sheets/{other['id']}/file").status_code == 401
