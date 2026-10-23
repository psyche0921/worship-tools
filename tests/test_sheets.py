"""악보: 곡 제목 정규화, 메들리 분리, 후보 찾기, 올리기·받기·zip, 권한."""
import hashlib
import io
import json
import pathlib
import zipfile

import pytest

from app.sheets import song_key, split_songs
from conftest import ADMIN, MEMBER, login

PID = 'PLsheet0000000001'

PDF = b'%PDF-1.4\n% fake sheet\n'
PNG = b'\x89PNG\r\n\x1a\n' + b'\0' * 32
JPG = b'\xff\xd8\xff\xe0' + b'\0' * 32


# ---------- 곡 제목 ----------

@pytest.mark.parametrize('a, b', [
    ('새 힘 얻으리', '새힘얻으리'),          # 띄어쓰기 차이를 흡수한다
    ('내가 소망하는 아버지 나라', '내가소망하는아버지나라'),
    ('주 품에 (Live)', '주품에'),            # 괄호 안은 곡 이름이 아니다
    ('[찬양] 은혜', '은혜'),
    ('Holy Forever', 'holy forever'),       # 대소문자
    ('주의 은혜라.pdf', '주의 은혜라'),       # 파일 확장자
    ('01_새힘얻으리_A키.pdf', '01새힘얻으리a키'),
])
def test_song_key_same(a, b):
    assert song_key(a) == song_key(b)


def test_song_key_different_songs():
    assert song_key('주의 은혜라') != song_key('주의 사랑이')


def test_song_key_all_in_brackets():
    # 제목이 통째로 괄호 안에 있으면 괄호만 떼고 쓴다(빈 키가 되면 안 된다).
    assert song_key('(주 품에)') == song_key('주 품에')


@pytest.mark.parametrize('name, parts', [
    ('나로부터 & 기대', ['나로부터', '기대']),
    ('비전 + Holy Forever', ['비전', 'Holy Forever']),
    ('마음의 예배 & 내 사람의 이유라', ['마음의 예배', '내 사람의 이유라']),
    ('주 품에 / 은혜', ['주 품에', '은혜']),
    ('새힘얻으리', ['새힘얻으리']),
    ('1. 주의 은혜라', ['주의 은혜라']),     # 줄머리 번호는 뗀다
    ('R&B 찬양', ['R&B 찬양']),             # 조각이 너무 짧으면 쪼개지 않는다
    ('', []),
])
def test_split_songs(name, parts):
    assert split_songs(name) == parts


# ---------- 후보 찾기 (이미 올라온 악보에서) ----------

def index(client, *names):
    """드라이브에서 가져올 목록(한시적). 파일 ID는 이름마다 하나로 정한다."""
    files = [{'id': 'drive-' + hashlib.sha1(n.encode()).hexdigest()[:12], 'name': n,
              'mimeType': 'application/pdf', 'size': '12345',
              'modifiedTime': '2026-10-01T03:04:05.000Z'} for n in names]
    files.append({'id': 'folder1', 'name': '지난 주', 'mimeType': 'application/vnd.google-apps.folder'})
    return client.put('/api/drive/index', json={'files': files})


def test_same_song_again_attaches_by_itself(client):
    """이미 했던 곡이 다시 나오면 띄어쓰기가 달라도 그대로 붙는다."""
    login(client, ADMIN)
    sheet = upload(client, '새 힘 얻으리').json()
    songs = client.post('/api/sheets/match', json={'names': ['새힘얻으리']}).json()['items'][0]['songs']
    assert songs[0]['sheet']['id'] == sheet['id'] and songs[0]['candidates'] == []


def test_similar_title_becomes_a_candidate(client):
    """악보 제목에 다른 말이 섞여 있어도 후보로 찾아낸다(실제 파일 이름이 그렇다)."""
    login(client, ADMIN)
    sheet = upload(client, '기프티드 나는 보기원하네 리드시트C').json()
    songs = client.post('/api/sheets/match',
                        json={'names': ['나는 보기 원하네']}).json()['items'][0]['songs']
    assert songs[0]['sheet'] is None
    assert [c['sheetId'] for c in songs[0]['candidates']] == [sheet['id']]


def test_candidate_is_not_the_same_song(client):
    """자기 자신은 후보로 돌려주지 않는다(이미 sheet로 붙는다)."""
    login(client, ADMIN)
    upload(client, '주의 은혜라')
    songs = client.post('/api/sheets/match', json={'names': ['주의 은혜라']}).json()['items'][0]['songs']
    assert songs[0]['sheet'] is not None and songs[0]['candidates'] == []


def test_alias_links_an_existing_sheet(client):
    """제목이 조금 다른 같은 곡 → 기존 악보를 이어 주면 다음부터 자동으로 붙는다."""
    login(client, ADMIN)
    sheet = upload(client, '주의 은혜라').json()
    assert client.post(f"/api/sheets/{sheet['id']}/alias",
                       json={'title': '주의 은혜라 (키 올림)'}).status_code == 200
    songs = client.post('/api/sheets/match',
                        json={'names': ['주의 은혜라 (키 올림)']}).json()['items'][0]['songs']
    assert songs[0]['sheet']['id'] == sheet['id']


def test_alias_refuses_to_shadow_another_song(client):
    login(client, ADMIN)
    a = upload(client, '주의 은혜라').json()
    upload(client, '다른 곡')
    assert client.post(f"/api/sheets/{a['id']}/alias", json={'title': '다른 곡'}).status_code == 409


def test_match_splits_medley(client):
    login(client, ADMIN)
    upload(client, '나로부터')
    upload(client, '기대')
    item = client.post('/api/sheets/match', json={'names': ['나로부터 & 기대']}).json()['items'][0]
    assert [s['title'] for s in item['songs']] == ['나로부터', '기대']
    assert all(s['sheet'] for s in item['songs'])


# ---------- 올리기 ----------

def upload(client, title, data=PDF, name='sheet.pdf', **form):
    return client.post('/api/sheets', data={'title': title, **form},
                       files={'file': (name, io.BytesIO(data), 'application/pdf')})


def test_upload_and_file_name_alias(client):
    login(client, ADMIN)
    sheet = upload(client, '새 힘 얻으리', name='01_새힘얻으리_A키.pdf').json()
    assert sheet['hasFile'] and sheet['size'] == len(PDF) and sheet['name'] == '01_새힘얻으리_A키.pdf'
    # 올린 파일 이름으로도 걸린다(별칭)
    songs = client.post('/api/sheets/match',
                        json={'names': ['01_새힘얻으리_A키']}).json()['items'][0]['songs']
    assert songs[0]['sheet']['id'] == sheet['id']


def test_upload_replaces_same_song(client):
    login(client, ADMIN)
    first = upload(client, '주의 은혜라').json()
    again = upload(client, '주의 은혜라', data=PNG, name='sheet.png').json()
    assert again['id'] == first['id'] and again['mime'] == 'image/png'
    assert len(client.get('/api/sheets').json()['items']) == 1


def test_member_can_upload(client, add_member):
    add_member()
    login(client, MEMBER)
    sheet = upload(client, '팀원이 올린 곡').json()
    assert sheet['hasFile'] and sheet['by'] == MEMBER
    assert client.get(f"/api/sheets/{sheet['id']}/file").status_code == 200


@pytest.mark.parametrize('data, name', [(b'not a sheet at all', 'x.pdf'), (b'', 'empty.pdf')])
def test_upload_rejects_other_files(client, data, name):
    login(client, ADMIN)
    assert upload(client, '아무 곡', data=data, name=name).status_code == 422


def test_upload_rejects_too_big(client, app):
    app.state.settings.max_sheet_mb = 1
    login(client, ADMIN)
    assert upload(client, '큰 악보', data=PDF + b'\x00' * (1024 * 1024)).status_code == 413


def test_upload_needs_title(client):
    login(client, ADMIN)
    assert upload(client, '()').status_code == 422


# ---------- 받기 ----------

def test_download_uses_song_title(client):
    login(client, ADMIN)
    sheet = upload(client, '주의 은혜라', data=JPG, name='weird name.jpg').json()
    r = client.get(f"/api/sheets/{sheet['id']}/file")
    assert r.status_code == 200 and r.content == JPG
    assert r.headers['content-type'] == 'image/jpeg'
    assert '%EC%A3%BC' in r.headers['content-disposition']  # 한글 제목이 들어간다
    assert '.jpg' in r.headers['content-disposition']


def test_download_missing_file(client, settings):
    login(client, ADMIN)
    sheet = upload(client, '주의 은혜라').json()
    for f in pathlib.Path(settings.sheets_dir).glob('*'):
        f.unlink()
    assert client.get(f"/api/sheets/{sheet['id']}/file").status_code == 404


# ---------- 세트리스트와 함께 ----------

def save_setlist(client, songs, title='Setlist 261018'):
    r = client.post('/api/setlists', json={'title': title, 'songs': songs, 'rawMessage': ''})
    assert r.status_code == 200, r.text
    return r.json()


def test_setlist_keeps_sheets_and_zips_them(client, add_member):
    login(client, ADMIN)
    a = upload(client, '나로부터').json()
    b = upload(client, '기대').json()
    saved = save_setlist(client, [
        {'id': 'dQw4w9WgXcQ', 'name': '나로부터 & 기대', 'notes': [],
         'sheets': [{'sheetId': a['id'], 'title': '나로부터'}, {'sheetId': b['id'], 'title': '기대'}]},
        {'id': 'aaaaaaaaaaa', 'name': '악보 없는 곡', 'notes': [], 'sheets': []},
    ])
    token = saved['shareToken']
    assert [s['title'] for s in saved['songs'][0]['sheets']] == ['나로부터', '기대']
    assert saved['songs'][0]['sheets'][0]['hasFile'] is True

    week = client.get('/api/records?kind=setlist').json()['groups'][0]['items'][0]
    assert week['key'] == token
    assert week['sheetsLinked'] == 2 and week['sheetsTotal'] == 3  # 메들리 2곡 + 1곡

    # 팀원도 상세와 zip을 쓸 수 있다.
    client.post('/api/auth/logout')
    add_member()
    login(client, MEMBER)
    detail = client.get(f'/api/records/setlist/{token}').json()
    assert [s['title'] for s in detail['songs'][0]['sheets']] == ['나로부터', '기대']
    r = client.get(f'/api/sheets/zip/{token}')
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert sorted(zf.namelist()) == ['기대.pdf', '나로부터.pdf']
    assert '261018' in r.headers['content-disposition']


def test_zip_without_sheets_is_404(client):
    login(client, ADMIN)
    it = save_setlist(client, [{'id': 'dQw4w9WgXcQ', 'name': '곡', 'notes': [], 'sheets': []}])
    assert client.get(f"/api/sheets/zip/{it['shareToken']}").status_code == 404
    assert client.get('/api/sheets/zip/nope').status_code == 404


def test_sheet_uploaded_later_shows_in_past_week(client):
    """악보를 나중에 올려도 그 곡이 나왔던 주의 기록에 보여야 한다."""
    login(client, ADMIN)
    it = save_setlist(client, [{'id': 'dQw4w9WgXcQ', 'name': '나로부터 & 기대', 'notes': [], 'sheets': []}])
    token = it['shareToken']
    assert client.get(f'/api/records/setlist/{token}').json()['songs'][0]['sheets'] == []

    upload(client, '기대')
    got = client.get(f'/api/records/setlist/{token}').json()['songs'][0]['sheets']
    assert [s['title'] for s in got] == ['기대'] and got[0]['auto'] is True
    assert client.get('/api/records?kind=setlist').json()['groups'][0]['items'][0]['sheetsLinked'] == 1
    with zipfile.ZipFile(io.BytesIO(client.get(f'/api/sheets/zip/{token}').content)) as zf:
        assert zf.namelist() == ['기대.pdf']


def test_deleted_sheet_shows_as_missing(client):
    login(client, ADMIN)
    a = upload(client, '나로부터').json()
    it = save_setlist(client, [{'id': 'dQw4w9WgXcQ', 'name': '나로부터', 'notes': [],
                                'sheets': [{'sheetId': a['id'], 'title': '나로부터'}]}])
    assert client.delete(f"/api/sheets/{a['id']}").status_code == 200
    detail = client.get(f"/api/records/setlist/{it['shareToken']}").json()
    assert detail['songs'][0]['sheets'][0]['missing'] is True
    assert client.get(f"/api/sheets/zip/{it['shareToken']}").status_code == 404


# ---------- 권한 ----------

ADMIN_ONLY = [
    ('put', '/api/drive/index', {'files': []}),
    ('get', '/api/drive/todo', None),
    ('delete', '/api/sheets/1', None),
]


@pytest.mark.parametrize('method, url, body', ADMIN_ONLY)
def test_admin_only(client, add_member, method, url, body):
    kw = {'json': body} if body is not None else {}
    assert client.request(method, url, **kw).status_code == 401
    add_member()
    login(client, MEMBER)
    assert client.request(method, url, **kw).status_code == 403


MEMBER_OK = [('post', '/api/sheets/match', {'names': []}), ('get', '/api/sheets', None)]


@pytest.mark.parametrize('method, url, body', MEMBER_OK)
def test_member_allowed(client, add_member, method, url, body):
    kw = {'json': body} if body is not None else {}
    assert client.request(method, url, **kw).status_code == 401
    add_member()
    login(client, MEMBER)
    assert client.request(method, url, **kw).status_code == 200


# ---------- 드라이브에서 가져오기 (한 번만) ----------

def test_drive_todo_shrinks_as_sheets_arrive(client):
    login(client, ADMIN)
    assert index(client, '주의 은혜라.pdf', '은혜.pdf').json()['count'] == 2  # 폴더는 빠진다
    todo = client.get('/api/drive/todo').json()
    assert [t['name'] for t in todo['items']] == ['은혜.pdf', '주의 은혜라.pdf'] and todo['total'] == 2
    upload(client, '주의 은혜라')
    todo = client.get('/api/drive/todo').json()
    assert [t['name'] for t in todo['items']] == ['은혜.pdf']
    assert client.get('/api/settings').json()['driveIndexCount'] == 2


def test_drive_index_adds_without_wiping(client):
    login(client, ADMIN)
    assert index(client, '주의 은혜라.pdf').json()['count'] == 1
    assert index(client, '하나만.pdf').json()['count'] == 2
    assert index(client, '하나만.pdf').json()['count'] == 2   # 같은 파일은 늘지 않는다
