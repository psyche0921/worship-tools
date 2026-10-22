"""악보: 곡 제목 정규화, 메들리 분리, 후보 찾기, 올리기·받기·zip, 권한."""
import hashlib
import io
import json
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


# ---------- 드라이브 목록과 후보 ----------

def index(client, *names):
    """관리자가 Picker에서 고른 악보를 목록에 넣는다. 파일 ID는 이름마다 하나로 정한다."""
    files = [{'id': 'drive-' + hashlib.sha1(n.encode()).hexdigest()[:12], 'name': n,
              'mimeType': 'application/pdf', 'size': '12345',
              'modifiedTime': '2026-10-01T03:04:05.000Z'} for n in names]
    files.append({'id': 'folder1', 'name': '지난 주', 'mimeType': 'application/vnd.google-apps.folder'})
    return client.put('/api/drive/index', json={'files': files})


def test_drive_index_adds_and_skips_folders(client):
    """drive.file은 고른 파일에만 권한을 주므로, 목록은 여러 번에 걸쳐 덧붙인다."""
    login(client, ADMIN)
    r = index(client, '주의 은혜라.pdf', '은혜.pdf').json()
    assert r['added'] == 2 and r['count'] == 2  # 폴더는 빠진다
    r = index(client, '하나만.pdf').json()
    assert r['added'] == 1 and r['count'] == 3  # 덧붙인다(통째로 바꾸지 않는다)
    assert client.get('/api/settings').json()['driveIndexCount'] == 3
    # 같은 파일을 다시 고르면 늘어나지 않는다
    assert index(client, '하나만.pdf').json()['count'] == 3


def test_drive_index_replace(client):
    login(client, ADMIN)
    index(client, '주의 은혜라.pdf', '은혜.pdf')
    r = client.put('/api/drive/index', json={'replace': True, 'files': [
        {'id': 'only', 'name': '하나만.pdf', 'mimeType': 'application/pdf'}]}).json()
    assert r['added'] == 1 and r['count'] == 1


def test_match_finds_by_contains(client):
    login(client, ADMIN)
    index(client, '01_새힘얻으리_A키.pdf', '전혀 다른 곡.pdf')
    songs = client.post('/api/sheets/match', json={'names': ['새 힘 얻으리']}).json()['items'][0]['songs']
    assert len(songs) == 1 and songs[0]['sheet'] is None
    assert [c['name'] for c in songs[0]['candidates']] == ['01_새힘얻으리_A키.pdf']


def test_match_falls_back_to_similar_names(client):
    login(client, ADMIN)
    index(client, '내가 소망하는 아버지나라.pdf')  # 띄어쓰기가 다르고 '나라'가 붙어 있다
    songs = client.post('/api/sheets/match', json={'names': ['내가 소망하는 아버지 나라!']}).json()['items'][0]['songs']
    # 포함으로는 못 걸리지만 비슷한 이름으로 후보에 오른다
    assert [c['name'] for c in songs[0]['candidates']] == ['내가 소망하는 아버지나라.pdf']


def test_match_splits_medley(client):
    login(client, ADMIN)
    index(client, '나로부터.pdf', '기대.pdf')
    item = client.post('/api/sheets/match', json={'names': ['나로부터 & 기대']}).json()['items'][0]
    assert [s['title'] for s in item['songs']] == ['나로부터', '기대']
    assert [s['candidates'][0]['name'] for s in item['songs']] == ['나로부터.pdf', '기대.pdf']


def test_match_prefers_exact_name(client):
    login(client, ADMIN)
    index(client, '주의 은혜라 - 피아노 반주용.pdf', '주의 은혜라.pdf')
    songs = client.post('/api/sheets/match', json={'names': ['주의 은혜라']}).json()['items'][0]['songs']
    assert songs[0]['candidates'][0]['name'] == '주의 은혜라.pdf'


# ---------- 올리기 ----------

def upload(client, title, data=PDF, name='sheet.pdf', **form):
    return client.post('/api/sheets', data={'title': title, **form},
                       files={'file': (name, io.BytesIO(data), 'application/pdf')})


def test_upload_links_and_matches_next_time(client):
    login(client, ADMIN)
    sheet = upload(client, '새 힘 얻으리', driveFileId='drive9', driveName='01_새힘얻으리_A키.pdf').json()
    assert sheet['sync'] == 'drive' and sheet['hasFile'] and sheet['size'] == len(PDF)

    # 다음에는 띄어쓰기가 달라도 바로 붙는다.
    songs = client.post('/api/sheets/match', json={'names': ['새힘얻으리']}).json()['items'][0]['songs']
    assert songs[0]['sheet']['id'] == sheet['id'] and songs[0]['candidates'] == []

    # 고른 파일 이름으로도 걸린다(별칭).
    songs = client.post('/api/sheets/match', json={'names': ['01_새힘얻으리_A키']}).json()['items'][0]['songs']
    assert songs[0]['sheet']['id'] == sheet['id']


def test_upload_replaces_same_song(client):
    login(client, ADMIN)
    first = upload(client, '주의 은혜라').json()
    again = upload(client, '주의 은혜라', data=PNG, name='sheet.png').json()
    assert again['id'] == first['id'] and again['mime'] == 'image/png'
    assert len(client.get('/api/sheets').json()['items']) == 1


def test_member_upload_is_pending(client, add_member):
    add_member()
    login(client, MEMBER)
    sheet = upload(client, '팀원이 올린 곡').json()
    assert sheet['sync'] == 'pending' and sheet['driveFileId'] == ''
    # 팀원은 드라이브 연결을 할 수 없다.
    assert upload(client, '다른 곡', driveFileId='drive1').status_code == 403

    login(client, ADMIN)
    assert [s['id'] for s in client.get('/api/sheets/pending').json()['items']] == [sheet['id']]
    done = client.post(f"/api/sheets/{sheet['id']}/drive",
                       json={'driveFileId': 'drive5', 'driveName': '팀원이 올린 곡.pdf'}).json()
    assert done['sync'] == 'drive' and done['driveFileId'] == 'drive5'
    assert client.get('/api/sheets/pending').json()['items'] == []


@pytest.mark.parametrize('data, name', [(b'not a sheet at all', 'x.pdf'), (b'', 'empty.pdf')])
def test_upload_rejects_other_files(client, data, name):
    login(client, ADMIN)
    assert upload(client, '아무 곡', data=data, name=name).status_code == 422


def test_upload_rejects_too_big(client, app):
    app.state.settings.max_sheet_mb = 1
    login(client, ADMIN)
    big = PDF + b'\0' * (1024 * 1024)
    assert upload(client, '큰 악보', data=big).status_code == 413


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


def test_member_can_download(client, add_member):
    login(client, ADMIN)
    sheet = upload(client, '주의 은혜라').json()
    client.post('/api/auth/logout')
    add_member()
    login(client, MEMBER)
    assert client.get(f"/api/sheets/{sheet['id']}/file").status_code == 200


def test_download_missing_file(client, settings):
    login(client, ADMIN)
    sheet = upload(client, '주의 은혜라').json()
    for p in (__import__('pathlib').Path(settings.sheets_dir)).glob('*'):
        p.unlink()
    assert client.get(f"/api/sheets/{sheet['id']}/file").status_code == 404


# ---------- 세트리스트와 함께 ----------

def save_setlist(client, songs):
    body = {'playlistId': PID, 'title': 'Setlist 261018',
            'url': f'https://www.youtube.com/playlist?list={PID}',
            'ok': len(songs), 'total': len(songs), 'songs': songs, 'rawMessage': ''}
    return client.post('/api/setlists', json=body)


def test_setlist_keeps_sheets_and_zips_them(client, add_member):
    login(client, ADMIN)
    a = upload(client, '나로부터').json()
    b = upload(client, '기대').json()
    saved = save_setlist(client, [
        {'id': 'dQw4w9WgXcQ', 'name': '나로부터 & 기대', 'notes': [],
         'sheets': [{'sheetId': a['id'], 'title': '나로부터'}, {'sheetId': b['id'], 'title': '기대'}]},
        {'id': 'aaaaaaaaaaa', 'name': '악보 없는 곡', 'notes': [], 'sheets': []},
    ]).json()
    assert [s['title'] for s in saved['songs'][0]['sheets']] == ['나로부터', '기대']
    assert saved['songs'][0]['sheets'][0]['hasFile'] is True

    week = client.get('/api/records?kind=setlist').json()['groups'][0]['items'][0]
    assert week['sheetsLinked'] == 2 and week['sheetsTotal'] == 3  # 메들리 2곡 + 1곡

    # 팀원도 상세와 zip을 쓸 수 있다.
    client.post('/api/auth/logout')
    add_member()
    login(client, MEMBER)
    detail = client.get(f'/api/records/setlist/{PID}').json()
    assert [s['title'] for s in detail['songs'][0]['sheets']] == ['나로부터', '기대']
    r = client.get(f'/api/sheets/zip/{PID}')
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert sorted(zf.namelist()) == ['기대.pdf', '나로부터.pdf']
    assert '261018' in r.headers['content-disposition']


def test_zip_without_sheets_is_404(client):
    login(client, ADMIN)
    save_setlist(client, [{'id': 'dQw4w9WgXcQ', 'name': '곡', 'notes': [], 'sheets': []}])
    assert client.get(f'/api/sheets/zip/{PID}').status_code == 404
    assert client.get('/api/sheets/zip/PLnope000000000').status_code == 404


def test_sheet_uploaded_later_shows_in_past_week(client):
    """악보를 나중에 올려도 그 곡이 나왔던 주의 기록에 보여야 한다."""
    login(client, ADMIN)
    save_setlist(client, [{'id': 'dQw4w9WgXcQ', 'name': '나로부터 & 기대', 'notes': [], 'sheets': []}])
    assert client.get(f'/api/records/setlist/{PID}').json()['songs'][0]['sheets'] == []

    upload(client, '기대')
    sheets = client.get(f'/api/records/setlist/{PID}').json()['songs'][0]['sheets']
    assert [s['title'] for s in sheets] == ['기대'] and sheets[0]['auto'] is True
    assert client.get('/api/records?kind=setlist').json()['groups'][0]['items'][0]['sheetsLinked'] == 1
    with zipfile.ZipFile(io.BytesIO(client.get(f'/api/sheets/zip/{PID}').content)) as zf:
        assert zf.namelist() == ['기대.pdf']


def test_deleted_sheet_shows_as_missing(client):
    login(client, ADMIN)
    a = upload(client, '나로부터').json()
    save_setlist(client, [{'id': 'dQw4w9WgXcQ', 'name': '나로부터', 'notes': [],
                           'sheets': [{'sheetId': a['id'], 'title': '나로부터'}]}])
    assert client.delete(f"/api/sheets/{a['id']}").status_code == 200
    detail = client.get(f'/api/records/setlist/{PID}').json()
    assert detail['songs'][0]['sheets'][0]['missing'] is True
    assert client.get(f'/api/sheets/zip/{PID}').status_code == 404


# ---------- 권한 ----------

ADMIN_ONLY = [
    ('put', '/api/drive/index', {'files': []}),
    ('get', '/api/sheets/pending', None),
    ('delete', '/api/sheets/1', None),
    ('post', '/api/sheets/1/drive', {'driveFileId': 'x'}),
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


def test_settings_folder_round_trip(client):
    login(client, ADMIN)
    index(client, '주의 은혜라.pdf')
    st = client.put('/api/settings', json={'sheetFolderId': 'F1', 'sheetFolderName': '악보'}).json()
    assert st['sheetFolderId'] == 'F1' and st['sheetFolderName'] == '악보'
    # Picker에는 API 키와 프로젝트 번호(appId)가 둘 다 필요하다(drive.file 범위).
    assert st['pickerKey'] == 'picker-key' and st['pickerAppId'] == '123456789'
    assert st['driveIndexCount'] == 1
    # 폴더를 비우면 그 폴더에서 읽어 둔 목록도 버린다.
    st = client.put('/api/settings', json={'sheetFolderId': ''}).json()
    assert st['driveIndexCount'] == 0 and st['driveIndexAt'] is None
