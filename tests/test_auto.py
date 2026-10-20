"""자동 쇼츠 기획: 영상 찾기(app/youtube.py)와 월요일 실행기(app/auto.py)."""
import json
from datetime import date, datetime, timedelta

import pytest

from app import youtube
from app.auto import AUTO_EMAIL, due, last_run
from app.db import now_ms, set_setting
from app.jobs import KST
from conftest import ADMIN, MEMBER, PLAN_REPLY, login

CHANNEL = 'https://www.youtube.com/@bridgechurch_Daejeon'
CID = 'UCleWBICypZpXuu9E4QNtEiQ'
VIDEO = '7jXNdzI82rk'
TITLE = '"용서라는 선물3" (빌 2:5-11) 2026.10.18 다리꿈교회 주일예배'


def channel_page(videos):
    """채널 '실시간' 탭 페이지 흉내(lockupViewModel 구조)."""
    data = {'contents': {'items': [
        {'lockupViewModel': {'contentId': v, 'contentType': 'LOCKUP_CONTENT_TYPE_VIDEO',
                             'metadata': {'lockupMetadataViewModel': {'title': {'content': t}}}}}
        for v, t in videos]}}
    return ('<html><script>var ytInitialData = ' + json.dumps(data, ensure_ascii=False)
            + ';</script><body>...</body></html>')


VIDEOS = [(VIDEO, TITLE), ('fh2CCOginac', '"함께 할수 있음에 감사" (빌 1:3-8) 2026.10.11 다리꿈교회 주일예배')]


def fake_fetch(pages):
    async def fetch(url):
        for key, body in pages.items():
            if key in url:
                return body
        raise youtube.YoutubeError(f'없는 주소: {url}')
    return fetch


# ---------- 영상 찾기 ----------

def test_parse_channel_page():
    got = youtube.parse_channel_page(channel_page(VIDEOS))
    assert [v['videoId'] for v in got] == [VIDEO, 'fh2CCOginac']
    assert got[0]['title'] == TITLE


def test_parse_channel_page_bad_html():
    with pytest.raises(youtube.YoutubeError):
        youtube.parse_channel_page('<html>그냥 페이지</html>')


@pytest.mark.parametrize('title, d, ok', [
    ('… 2026.10.18 주일예배', date(2026, 10, 18), True),
    ('… 2026.10.4 주일예배', date(2026, 10, 4), True),
    ('… 2026-10-18 주일예배', date(2026, 10, 18), True),
    ('… 26.10.18 주일예배', date(2026, 10, 18), True),
    ('2026년 10월 18일 주일', date(2026, 10, 18), True),
    ('… 2026.10.18 주일예배', date(2026, 10, 1), False),
    ('… 2026.10.11 주일예배', date(2026, 10, 18), False),
])
def test_date_re(title, d, ok):
    assert bool(youtube.date_re(d).search(title)) is ok


@pytest.mark.parametrize('channel, want', [
    ('@bridge', 'https://www.youtube.com/@bridge'),
    ('bridge', 'https://www.youtube.com/@bridge'),
    (CID, f'https://www.youtube.com/channel/{CID}'),
    ('https://www.youtube.com/@bridge/streams', 'https://www.youtube.com/@bridge'),
])
def test_channel_url(channel, want):
    assert youtube.channel_url(channel) == want
    assert youtube.channel_url(channel, 'streams') == want + '/streams'


def test_channel_url_rejects_junk():
    with pytest.raises(youtube.YoutubeError):
        youtube.channel_url('이상한 값!!')


def find(fetch, target, api_key=''):
    import asyncio
    return asyncio.run(youtube.find_service_video(fetch, CHANNEL, target, api_key))


def test_find_by_page():
    fetch = fake_fetch({'/streams': channel_page(VIDEOS)})
    assert find(fetch, date(2026, 10, 18))['videoId'] == VIDEO
    assert find(fetch, date(2026, 10, 25)) is None


def test_api_first_then_page():
    api_body = json.dumps({'items': [
        {'snippet': {'title': TITLE, 'resourceId': {'videoId': VIDEO}, 'publishedAt': '2026-10-18T02:00:00Z'}}]})
    pages = {'/streams': channel_page([('zzzzzzzzzzz', TITLE)]),   # 앞에서부터 맞는 주소를 쓴다
             'googleapis.com': api_body, 'youtube.com/@': f'"externalId":"{CID}"'}
    assert find(fake_fetch(pages), date(2026, 10, 18), 'KEY')['videoId'] == VIDEO  # API 결과를 먼저

    # API가 막히면 채널 페이지로 넘어간다
    pages['googleapis.com'] = json.dumps({'error': {'message': 'quota'}})
    assert find(fake_fetch(pages), date(2026, 10, 18), 'KEY')['videoId'] == 'zzzzzzzzzzz'


# ---------- 언제 돌리나 ----------

def at(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=KST)


MON, TUE, SUN = at(2026, 10, 19, 13), at(2026, 10, 20, 13), at(2026, 10, 18, 13)


@pytest.mark.parametrize('now, last, want', [
    (MON, {}, True),                                                   # 월요일 1시
    (at(2026, 10, 19, 12, 59), {}, False),                             # 아직 이름
    (at(2026, 10, 19, 17, 59), {}, True),                              # 저녁 6시 전까지
    (at(2026, 10, 19, 18), {}, False),
    (TUE, {}, False), (SUN, {}, False),                                # 월요일만
    (MON, {'week': '2026-10-19', 'status': 'done', 'at': 0}, False),   # 이번 주 끝냈으면
    (MON, {'week': '2026-10-19', 'status': 'skip', 'at': 0}, False),
    (MON, {'week': '2026-10-12', 'status': 'done', 'at': 0}, True),    # 지난주 것은 상관없다
    (MON, {'week': '2026-10-19', 'status': 'none', 'at': 0}, True),    # 영상이 없었으면 다시
    (MON, {'week': '2026-10-19', 'status': 'error', 'at': 0}, True),
])
def test_due(now, last, want):
    assert due(now, last) is want


def test_due_waits_between_retries():
    now = at(2026, 10, 19, 14)
    ms = now.timestamp() * 1000
    last = {'week': '2026-10-19', 'status': 'none', 'at': ms - 60_000}
    assert due(now, last) is False          # 1분 전에 봤으면 기다린다
    last['at'] = ms - 31 * 60_000
    assert due(now, last) is True           # 30분 지났으면 다시 본다


# ---------- 실행 ----------

@pytest.fixture
def auto_app(settings, fake_gemini):
    from app.main import create_app
    from conftest import fake_verifier
    app = create_app(settings, verify_token=fake_verifier, gemini_call=fake_gemini,
                     yt_fetch=fake_fetch({'/streams': channel_page(VIDEOS)}))
    return app


@pytest.fixture
def auto_client(auto_app):
    from fastapi.testclient import TestClient
    from conftest import ORIGIN
    with TestClient(auto_app, base_url=ORIGIN, headers={'Origin': ORIGIN}) as c:
        yield c


@pytest.fixture
def ready(auto_client, settings):
    """지난 주일 영상이 있고, 지침과 채널이 설정된 상태."""
    from app.db import connect
    conn = connect(settings.db_path)
    with conn:
        set_setting(conn, 'skill_md', '# 지침', ADMIN)
        set_setting(conn, 'yt_channel', CHANNEL, ADMIN)
        set_setting(conn, 'auto_enabled', '1', ADMIN)
    yield conn
    conn.close()


def run_now(client):
    login(client, ADMIN)
    return client.post('/api/auto/run').json()


def test_run_makes_a_plan(auto_client, ready, fake_gemini, monkeypatch):
    fake_gemini.replies = ['{"start":"0:33:41","end":"1:03:47","title":"너희 안에 이 사고방식을 품으라"}',
                           PLAN_REPLY, '[0:33:41] 함께 봉독합니다.']
    r = run_now(auto_client)
    assert r['status'] == 'done', r
    assert r['videoId'] == VIDEO and r['shortsId']

    item = auto_client.get(f"/api/shorts/{r['shortsId']}").json()
    assert item['id'] == VIDEO and item['start'] == 2021 and item['end'] == 3827
    assert item['text'] == PLAN_REPLY and item['byName'] == '자동'
    assert item['title'].endswith('· 너희 안에 이 사고방식을 품으라')
    assert item['transcript'] == '[0:33:41] 함께 봉독합니다.'
    assert last_run(ready)['status'] == 'done'

    # 두 번째는 이미 있는 영상이라 건너뛴다(자동 실행일 때)
    from app.auto import AutoRunner  # noqa: F401
    assert ready.execute('SELECT COUNT(*) FROM shorts').fetchone()[0] == 1


def test_skip_when_already_planned(auto_client, ready, fake_gemini):
    with ready:
        ready.execute("INSERT INTO shorts (video_id, text, created_by, created_at) VALUES (?, 't', ?, ?)",
                      (VIDEO, ADMIN, now_ms()))
    import app.auto as auto
    orig = auto.AutoRunner._run
    r = auto_client.app.state.auto  # 자동 실행(force=False)과 같은 길
    import asyncio
    out = asyncio.run(orig(r, ready, False))
    assert out['status'] == 'skip' and not fake_gemini.calls


def test_no_video_yet(auto_client, ready):
    with ready:
        set_setting(ready, 'yt_channel', CHANNEL, ADMIN)
    import app.auto as auto
    import asyncio
    # 지난 주일이 아닌 날짜의 영상만 있는 채널
    auto_client.app.state.yt_fetch = fake_fetch({'/streams': channel_page([('aaaaaaaaaaa', '옛날 영상 2020.01.05')])})
    out = asyncio.run(auto.AutoRunner._run(auto_client.app.state.auto, ready, False))
    assert out['status'] == 'none' and '아직' in out['message']


def test_needs_channel_and_skill(auto_client, settings):
    from app.db import connect
    conn = connect(settings.db_path)
    import app.auto as auto
    import asyncio
    out = asyncio.run(auto.AutoRunner._run(auto_client.app.state.auto, conn, True))
    assert out['status'] == 'error' and '채널' in out['message']
    with conn:
        set_setting(conn, 'yt_channel', CHANNEL, ADMIN)
    out = asyncio.run(auto.AutoRunner._run(auto_client.app.state.auto, conn, True))
    assert out['status'] == 'error' and 'SKILL.md' in out['message']
    conn.close()


def test_off_unless_enabled(auto_client, ready):
    with ready:
        set_setting(ready, 'auto_enabled', '', ADMIN)
    import app.auto as auto
    import asyncio
    out = asyncio.run(auto.AutoRunner._run(auto_client.app.state.auto, ready, False))
    assert out['status'] == 'off'


# ---------- 설정 API ----------

def test_settings_and_run_are_admin_only(client, add_member):
    add_member()
    login(client, MEMBER)
    assert client.post('/api/auto/run').status_code == 403
    assert client.put('/api/settings', json={'channel': CHANNEL}).status_code == 403


def test_settings_roundtrip(client):
    login(client, ADMIN)
    st = client.put('/api/settings', json={'channel': CHANNEL, 'autoEnabled': True}).json()
    assert st['channel'] == CHANNEL and st['autoEnabled'] is True and st['autoLast'] == {}
    assert client.get('/api/settings').json()['autoEnabled'] is True
    assert client.put('/api/settings', json={'autoEnabled': False}).json()['autoEnabled'] is False
    bad = client.put('/api/settings', json={'channel': '이상한 값!!'})
    assert bad.status_code == 400 and '채널' in bad.json()['detail']


def test_auto_account_hidden_from_members(client):
    login(client, ADMIN)
    assert AUTO_EMAIL not in [m['email'] for m in client.get('/api/members').json()]
