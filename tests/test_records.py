"""작업 기록 보기 API(/api/records): 팀원도 보기, 주일 묶음, 검색(주 단위로 펼침), 페이지."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.db import now_ms
from conftest import ADMIN, MEMBER, login

KST = timezone(timedelta(hours=9))
PID = 'PLrecord000000001'


def ms(y, m, d, hh=10):
    return int(datetime(y, m, d, hh, tzinfo=KST).timestamp() * 1000)


def add_setlist(db, key, title, at, songs=('주의 은혜라', '여기에 모인 우리'), raw=''):
    with db:
        db.execute(
            'INSERT INTO setlists (playlist_id, title, url, ok, total, songs_json, raw_message, created_by, '
            'created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (key, title, f'https://www.youtube.com/playlist?list={key}', len(songs), len(songs),
             json.dumps([{'id': 'dQw4w9WgXcQ', 'name': n, 'notes': ['KEY - A']} for n in songs],
                        ensure_ascii=False),
             raw, ADMIN, at))


def add_shorts(db, title, at, text='## 쇼츠 1\n본문', sermon='', by=ADMIN, transcript=''):
    with db:
        cur = db.execute(
            'INSERT INTO shorts (video_id, start_sec, end_sec, title, sermon_title, extra, text, created_by, '
            "created_at, transcript) VALUES ('re4fR9uW0Po', 2334, 4800, ?, ?, '', ?, ?, ?, ?)",
            (title, sermon, text, by, at, transcript))
    return cur.lastrowid


def listing(client, **params):
    r = client.get('/api/records', params=params)
    assert r.status_code == 200, r.text
    return r.json()


def rows(client, **params):
    return [it for g in listing(client, **params)['groups'] for it in g['items']]


def keys(client, **params):
    return [it['key'] for it in rows(client, **params)]


# ---------- 권한 ----------

def test_login_required(client):
    assert client.get('/api/records').status_code == 401
    assert client.get(f'/api/records/setlist/{PID}').status_code == 401


def test_member_can_read_setlist_records(client, db, add_member):
    add_setlist(db, PID, 'Setlist 261115', ms(2026, 11, 11))
    add_member()
    login(client, MEMBER)

    [it] = rows(client)
    assert it['kind'] == 'setlist' and it['canDelete'] is False
    assert it['songs'] == ['주의 은혜라', '여기에 모인 우리']

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


# ---------- 주일 묶음, 페이지 ----------

def test_weekly_grouping(client, db):
    add_setlist(db, PID, 'Setlist 261115', ms(2026, 11, 11))
    add_shorts(db, '261115 쇼츠 기획 · 두려움을 이기는 믿음', ms(2026, 11, 16), sermon='두려움을 이기는 믿음')
    add_setlist(db, 'PLrecord000000002', 'Setlist 261122', ms(2026, 11, 18))
    login(client, ADMIN)

    r = listing(client)
    assert r['total'] == 3 and r['weeks'] == 2 and len(r['groups']) == 2
    assert [g['week'] for g in r['groups']] == ['2026-11-22', '2026-11-15']
    assert r['groups'][0]['label'] == '2026년 11월 22일 주일'
    same_week = r['groups'][1]['items']
    assert [it['kind'] for it in same_week] == ['setlist', 'shorts']  # 세트리스트 먼저
    assert same_week[0]['songs'] and same_week[1]['topic'] == '두려움을 이기는 믿음'


def test_paging_counts_weeks(client, db):
    for i in range(1, 13):  # 목요일마다 → 서로 다른 12개 주일
        at = ms(2026, 1, 1) + i * 7 * 86400_000
        add_setlist(db, f'PLweek{i:012d}', '세트리스트', at)
        add_shorts(db, '쇼츠 기획', at + 4 * 86400_000)  # 그 다음 월요일 → 같은 주일
    login(client, ADMIN)

    first = listing(client)
    assert first['total'] == 24 and first['weeks'] == 12 and first['pages'] == 2
    assert len(first['groups']) == 10 and all(len(g['items']) == 2 for g in first['groups'])
    assert first['counts'] == {'setlist': 12, 'shorts': 12}
    assert first['groups'][0]['week'] > first['groups'][-1]['week']  # 최근 주일부터

    last = listing(client, page=2)
    assert len(last['groups']) == 2
    assert listing(client, page=9)['page'] == 2  # 넘치는 쪽은 마지막 쪽
    assert not ({g['week'] for g in first['groups']} & {g['week'] for g in last['groups']})


def test_kind_filter(client, db):
    add_setlist(db, PID, 'Setlist 261115', ms(2026, 11, 11))
    add_shorts(db, '261115 쇼츠 기획 · 두려움을 이기는 믿음', ms(2026, 11, 16), sermon='두려움을 이기는 믿음')
    login(client, ADMIN)

    assert listing(client)['counts'] == {'setlist': 1, 'shorts': 1}
    only = listing(client, kind='shorts')
    assert [it['kind'] for g in only['groups'] for it in g['items']] == ['shorts'] and only['total'] == 1
    assert listing(client, kind='setlist')['total'] == 1
    assert client.get('/api/records?kind=nope').status_code == 422


def test_empty(client):
    login(client, ADMIN)
    assert listing(client) == {'page': 1, 'pages': 1, 'total': 0, 'weeks': 0,
                               'counts': {'setlist': 0, 'shorts': 0}, 'groups': []}


# ---------- 검색 ----------

def test_search_shows_the_whole_week(client, db):
    """찬양 제목으로 찾으면 그 주에 기획한 쇼츠도 함께 나온다."""
    add_setlist(db, PID, 'Setlist 261115', ms(2026, 11, 11), songs=('주의 은혜라', '거룩하신 주'))
    shorts_id = add_shorts(db, '261115 쇼츠 기획 · 두려움을 이기는 믿음', ms(2026, 11, 16),
                           sermon='두려움을 이기는 믿음')
    add_setlist(db, 'PLrecord000000002', 'Setlist 261122', ms(2026, 11, 18), songs=('나의 피난처',))
    add_shorts(db, '261122 쇼츠 기획 · 기다림', ms(2026, 11, 23), sermon='기다림')
    login(client, ADMIN)

    r = listing(client, q='거룩하신')
    assert len(r['groups']) == 1 and r['groups'][0]['week'] == '2026-11-15'
    assert [it['key'] for it in r['groups'][0]['items']] == [PID, str(shorts_id)]

    # 설교 주제로 찾아도 같은 주의 세트리스트가 함께 나온다
    assert set(keys(client, q='두려움')) == {PID, str(shorts_id)}
    # 종류를 걸러 두면 그 종류만
    assert keys(client, q='거룩하신', kind='shorts') == []


def test_search_by_keyword_and_date(client, db):
    add_setlist(db, PID, 'Setlist 261115', ms(2026, 11, 11), songs=('주의 은혜라',))
    add_setlist(db, 'PLrecord000000002', 'Setlist 261122', ms(2026, 11, 18), songs=('나의 피난처',))
    sid = add_shorts(db, '261129 쇼츠 기획 · 광야', ms(2026, 11, 30), sermon='광야',
                     text='## 쇼츠 1\n믿음은 광야에서 자랍니다.', transcript='[0:40:12] 오늘 본문은 출애굽기입니다.')
    login(client, ADMIN)

    assert keys(client, q='은혜라') == [PID]
    assert keys(client, q='피난처') == ['PLrecord000000002']
    assert keys(client, q='광야') == [str(sid)]              # 쇼츠 본문 안의 키워드
    assert keys(client, q='출애굽기') == [str(sid)]          # 설교 대본 안의 키워드
    assert keys(client, q='261115') == [PID]                 # 날짜
    assert keys(client, q='2026-11-15') == [PID]
    assert len(keys(client, q='2026')) == 3
    assert keys(client, q='261129 광야') == [str(sid)]       # 조각은 모두 맞아야 한다(AND)
    assert keys(client, q='없는말') == []


def test_search_is_case_insensitive(client, db):
    add_setlist(db, PID, 'Setlist 261115', ms(2026, 11, 11))
    login(client, ADMIN)
    assert keys(client, q='SETLIST') == [PID]


def test_has_transcript_flag(client, db):
    a = add_shorts(db, '261115 쇼츠 기획', ms(2026, 11, 16), transcript='[0:40:12] 대본')
    b = add_shorts(db, '261122 쇼츠 기획', ms(2026, 11, 23))
    login(client, ADMIN)
    flags = {it['key']: it['hasTranscript'] for it in rows(client)}
    assert flags == {str(a): True, str(b): False}


def test_shorts_author_can_delete_only_own(client, db, add_member):
    add_member()
    add_shorts(db, '쇼츠 기획', now_ms(), by=MEMBER)
    add_shorts(db, '쇼츠 기획', now_ms(), by=ADMIN)
    login(client, MEMBER)
    assert {it['by']: it['canDelete'] for it in rows(client, kind='shorts')} == {MEMBER: True, ADMIN: False}
