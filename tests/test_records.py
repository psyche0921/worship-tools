"""작업 기록 보기 API(/api/records): 팀원도 보기, 검색, 페이지, 주일 묶음."""
from datetime import datetime, timedelta, timezone

import pytest

from app.db import now_ms
from conftest import ADMIN, MEMBER, login

KST = timezone(timedelta(hours=9))
PID = 'PLrecord000000001'


def ms(y, m, d, hh=10):
    return int(datetime(y, m, d, hh, tzinfo=KST).timestamp() * 1000)


def add_setlist(db, key, title, at, songs=('주의 은혜라', '여기에 모인 우리'), raw=''):
    import json
    with db:
        db.execute(
            'INSERT INTO setlists (playlist_id, title, url, ok, total, songs_json, raw_message, created_by, '
            'created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (key, title, f'https://www.youtube.com/playlist?list={key}', len(songs), len(songs),
             json.dumps([{'id': 'dQw4w9WgXcQ', 'name': n, 'notes': ['KEY - A']} for n in songs],
                        ensure_ascii=False),
             raw, ADMIN, at))


def add_shorts(db, title, at, text='## 쇼츠 1\n본문', sermon='', by=ADMIN):
    with db:
        cur = db.execute(
            'INSERT INTO shorts (video_id, start_sec, end_sec, title, sermon_title, extra, text, created_by, '
            "created_at) VALUES ('re4fR9uW0Po', 2334, 4800, ?, ?, '', ?, ?, ?)",
            (title, sermon, text, by, at))
    return cur.lastrowid


# ---------- 권한 ----------

def test_login_required(client):
    assert client.get('/api/records').status_code == 401
    assert client.get(f'/api/records/setlist/{PID}').status_code == 401


def test_member_can_read_setlist_records(client, db, add_member):
    add_setlist(db, PID, 'Setlist 261115', ms(2026, 11, 11))
    add_member()
    login(client, MEMBER)

    r = client.get('/api/records').json()
    assert [it['kind'] for it in r['items']] == ['setlist']
    assert r['items'][0]['canDelete'] is False
    assert r['items'][0]['songs'] == ['주의 은혜라', '여기에 모인 우리']

    one = client.get(f'/api/records/setlist/{PID}').json()
    assert one['title'] == 'Setlist 261115' and len(one['songs']) == 2
    assert 'rawMessage' not in one  # 원본 메시지는 관리자만
    assert client.get('/api/setlists').status_code == 403  # 관리자 전용 API는 그대로 막힌다


def test_admin_detail_has_raw_message(client, db):
    add_setlist(db, PID, 'Setlist 261115', ms(2026, 11, 11), raw='원본 메시지')
    login(client, ADMIN)
    one = client.get(f'/api/records/setlist/{PID}').json()
    assert one['rawMessage'] == '원본 메시지' and one['canDelete'] is True


def test_missing_setlist(client):
    login(client, ADMIN)
    assert client.get('/api/records/setlist/PLnope0000000001').status_code == 404


# ---------- 예배 날짜 ----------

@pytest.mark.parametrize('kind, title, at, expected', [
    ('setlist', 'Setlist 261115', ms(2026, 11, 11), '2026-11-15'),   # 제목의 날짜
    ('setlist', '261115 Setlist', ms(2026, 11, 11), '2026-11-15'),
    ('setlist', '세트리스트', ms(2026, 11, 11), '2026-11-15'),        # 날짜가 없으면 돌아오는 일요일
    ('shorts', '261115 쇼츠 기획 · 믿음', ms(2026, 11, 16), '2026-11-15'),
    ('shorts', '쇼츠 기획', ms(2026, 11, 16), '2026-11-15'),          # 날짜가 없으면 지난 일요일
    ('shorts', '쇼츠 기획', ms(2026, 11, 15), '2026-11-15'),          # 일요일에 만들면 그날
])
def test_service_date(kind, title, at, expected):
    from app.routes.records import service_date
    assert service_date(kind, title, at).isoformat() == expected


# ---------- 목록, 페이지 ----------

def test_paging_ten_per_page(client, db):
    for i in range(1, 13):  # 26.01.04 ~ 26.03.22 주일 12개
        at = ms(2026, 1, 1) + i * 7 * 86400_000
        add_setlist(db, f'PLpage{i:012d}', '세트리스트', at)
    login(client, ADMIN)

    first = client.get('/api/records').json()
    assert first['total'] == 12 and first['pages'] == 2 and len(first['items']) == 10
    assert first['counts'] == {'setlist': 12, 'shorts': 0}
    assert first['items'][0]['date'] > first['items'][-1]['date']  # 최신 예배 날짜부터

    last = client.get('/api/records?page=2').json()
    assert len(last['items']) == 2
    assert client.get('/api/records?page=9').json()['page'] == 2  # 넘치는 쪽은 마지막 쪽
    assert {it['key'] for it in first['items']} & {it['key'] for it in last['items']} == set()


def test_kind_filter(client, db):
    add_setlist(db, PID, 'Setlist 261115', ms(2026, 11, 11))
    add_shorts(db, '261115 쇼츠 기획 · 두려움을 이기는 믿음', ms(2026, 11, 16), sermon='두려움을 이기는 믿음')
    login(client, ADMIN)

    assert client.get('/api/records').json()['counts'] == {'setlist': 1, 'shorts': 1}
    only = client.get('/api/records?kind=shorts').json()
    assert [it['kind'] for it in only['items']] == ['shorts'] and only['total'] == 1
    assert only['items'][0]['topic'] == '두려움을 이기는 믿음'
    assert client.get('/api/records?kind=setlist').json()['total'] == 1
    assert client.get('/api/records?kind=nope').status_code == 422


# ---------- 검색 ----------

def test_search_by_keyword_and_date(client, db):
    add_setlist(db, PID, 'Setlist 261115', ms(2026, 11, 11), songs=('주의 은혜라', '거룩하신 주'))
    add_setlist(db, 'PLrecord000000002', 'Setlist 261122', ms(2026, 11, 18), songs=('나의 피난처',))
    add_shorts(db, '261115 쇼츠 기획 · 두려움을 이기는 믿음', ms(2026, 11, 16),
               sermon='두려움을 이기는 믿음', text='## 쇼츠 1: 광야에서\n믿음은 광야에서 자랍니다.')
    login(client, ADMIN)

    def keys(q):
        return [it['key'] for it in client.get('/api/records', params={'q': q}).json()['items']]

    assert keys('은혜라') == [PID]                      # 곡 제목
    assert keys('피난처') == ['PLrecord000000002']
    assert keys('광야') == ['1']                        # 쇼츠 본문 안의 키워드
    assert keys('믿음') == ['1']                        # 설교 주제
    assert set(keys('261115')) == {PID, '1'}            # 같은 주일의 두 기록
    assert set(keys('2026-11-15')) == {PID, '1'}
    assert len(keys('2026')) == 3
    assert keys('261115 광야') == ['1']                 # 조각은 모두 맞아야 한다(AND)
    assert keys('없는말') == []


def test_search_is_case_insensitive(client, db):
    add_setlist(db, PID, 'Setlist 261115', ms(2026, 11, 11))
    login(client, ADMIN)
    assert len(client.get('/api/records', params={'q': 'SETLIST'}).json()['items']) == 1


# ---------- 주일 묶음 ----------

def test_weekly_grouping(client, db):
    add_setlist(db, PID, 'Setlist 261115', ms(2026, 11, 11))
    add_shorts(db, '261115 쇼츠 기획 · 두려움을 이기는 믿음', ms(2026, 11, 16), sermon='두려움을 이기는 믿음')
    add_setlist(db, 'PLrecord000000002', 'Setlist 261122', ms(2026, 11, 18))
    login(client, ADMIN)

    r = client.get('/api/records?weekly=true').json()
    assert r['weekly'] is True and r['total'] == 3 and len(r['groups']) == 2
    assert [g['week'] for g in r['groups']] == ['2026-11-22', '2026-11-15']
    assert r['groups'][0]['label'] == '2026년 11월 22일 주일'
    same_week = r['groups'][1]['items']
    assert [it['kind'] for it in same_week] == ['setlist', 'shorts']  # 세트리스트 먼저
    assert same_week[0]['songs'] and same_week[1]['topic'] == '두려움을 이기는 믿음'


def test_weekly_paging_counts_weeks(client, db):
    for i in range(1, 13):
        at = ms(2026, 1, 1) + i * 7 * 86400_000
        add_setlist(db, f'PLweek{i:012d}', '세트리스트', at)        # 목요일 → 돌아오는 일요일
        add_shorts(db, '쇼츠 기획', at + 4 * 86400_000)             # 그 다음 월요일 → 같은 일요일
    login(client, ADMIN)

    r = client.get('/api/records?weekly=true').json()
    assert r['total'] == 24 and r['pages'] == 2 and len(r['groups']) == 10
    assert all(len(g['items']) == 2 for g in r['groups'])
    assert len(client.get('/api/records?weekly=true&page=2').json()['groups']) == 2


def test_weekly_with_search(client, db):
    add_setlist(db, PID, 'Setlist 261115', ms(2026, 11, 11), songs=('주의 은혜라',))
    add_setlist(db, 'PLrecord000000002', 'Setlist 261122', ms(2026, 11, 18), songs=('나의 피난처',))
    login(client, ADMIN)
    r = client.get('/api/records', params={'weekly': 'true', 'q': '피난처'}).json()
    assert len(r['groups']) == 1 and r['groups'][0]['week'] == '2026-11-22'


def test_empty(client):
    login(client, ADMIN)
    r = client.get('/api/records').json()
    assert r == {'page': 1, 'pages': 1, 'total': 0, 'counts': {'setlist': 0, 'shorts': 0},
                 'weekly': False, 'items': []}


def test_shorts_author_can_delete_only_own(client, db, add_member):
    add_member()
    add_shorts(db, '쇼츠 기획', now_ms(), by=MEMBER)
    add_shorts(db, '쇼츠 기획', now_ms(), by=ADMIN)
    login(client, MEMBER)
    items = client.get('/api/records?kind=shorts').json()['items']
    assert {it['by']: it['canDelete'] for it in items} == {MEMBER: True, ADMIN: False}
