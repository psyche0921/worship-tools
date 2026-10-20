"""2단계: 쇼츠 작업 실행기, Gemini 프롬프트, 설교 대본, 팀 공용 쇼츠 기록."""
import json
import time
from datetime import datetime

import pytest

from app import gemini
from app.db import connect, now_ms, set_setting
from app.jobs import KST, shorts_title
from conftest import ADMIN, MEMBER, PLAN_REPLY, login, wait_job

VID = 'dQw4w9WgXcQ'
SKILL = '---\nname: shorts\ndescription: x\n---\n# 쇼츠 지침\n세 편을 기획한다.'


@pytest.fixture
def skill(db):
    with db:
        set_setting(db, 'skill_md', SKILL, ADMIN)


def plan(client, **kw):
    body = {'kind': 'plan', 'videoId': VID, 'start': 3750, 'end': 6310, 'sermonTitle': '은혜'}
    body.update(kw)
    return client.post('/api/jobs', json=body)


# ---------- 작업: 쇼츠 기획 ----------

def test_plan_requires_login(client):
    assert plan(client).status_code == 401


def test_plan_requires_skill(client):
    login(client, ADMIN)
    r = plan(client)
    assert r.status_code == 400 and 'SKILL.md' in r.json()['detail']


def test_plan_success(client, skill, fake_gemini, db):
    login(client, ADMIN)
    r = plan(client, extra='청년부 대상')
    assert r.status_code == 200
    job = wait_job(client, r.json()['jobId'])
    assert job['status'] == 'done', job
    item = job['shorts']
    assert item['id'] == VID and item['start'] == 3750 and item['end'] == 6310
    assert item['text'] == PLAN_REPLY  # 원문 그대로 저장(#t= 제거는 화면에서)
    assert item['title'].endswith(' 쇼츠 기획 · 은혜')
    assert item['extra'] == '청년부 대상' and item['canDelete'] is True

    model, body = fake_gemini.calls[0]
    assert model == 'gemini-3.8-flash'
    assert body['system_instruction']['parts'][0]['text'] == '# 쇼츠 지침\n세 편을 기획한다.'
    video, prompt = body['contents'][0]['parts']
    assert video == {'file_data': {'file_uri': f'https://www.youtube.com/watch?v={VID}'},
                     'video_metadata': {'start_offset': '3750s', 'end_offset': '6310s'}}
    text = prompt['text']
    assert '원본 기준 1:02:30 ~ 1:45:10' in text
    assert '잘라낸 영상의 0:00은 원본의 1:02:30입니다.' in text
    assert '추가 요청: 청년부 대상' in text
    assert body['generationConfig'] == {'mediaResolution': 'MEDIA_RESOLUTION_LOW'}


def test_plan_without_range(client, skill, fake_gemini):
    login(client, ADMIN)
    job = wait_job(client, plan(client, start=None, end=None).json()['jobId'])
    assert job['status'] == 'done'
    video, prompt = fake_gemini.calls[0][1]['contents'][0]['parts']
    assert 'video_metadata' not in video
    assert prompt['text'].startswith('첨부 영상은 주일 예배 라이브 영상입니다.')
    assert '잘라낸' not in prompt['text']
    assert job['shorts']['start'] == 0 and job['shorts']['end'] == 0


def test_model_from_settings(client, skill, fake_gemini, db):
    with db:
        set_setting(db, 'model', 'gemini-test-pro', ADMIN)
    login(client, ADMIN)
    wait_job(client, plan(client).json()['jobId'])
    assert fake_gemini.calls[0][0] == 'gemini-test-pro'


def test_gemini_error_marks_job(client, skill, fake_gemini, db):
    fake_gemini.replies = [gemini.GeminiError('요청 한도를 넘었어요.')]
    login(client, ADMIN)
    job = wait_job(client, plan(client).json()['jobId'])
    assert job == {'id': job['id'], 'kind': 'plan', 'status': 'error', 'error': '요청 한도를 넘었어요.', 'result': None}
    assert db.execute('SELECT COUNT(*) FROM shorts').fetchone()[0] == 0


def test_unexpected_error_is_generic(client, skill, fake_gemini):
    fake_gemini.replies = [RuntimeError('boom')]
    login(client, ADMIN)
    job = wait_job(client, plan(client).json()['jobId'])
    assert job['status'] == 'error' and 'boom' not in job['error']


@pytest.mark.parametrize('kw, msg', [
    ({'videoId': 'https://youtu'}, '유튜브 영상 링크'),
    ({'start': 100, 'end': 100}, '늦어야'),
])
def test_plan_validation(client, skill, kw, msg):
    login(client, ADMIN)
    r = plan(client, **kw)
    assert r.status_code == 400 and msg in r.json()['detail']


def test_negative_time_rejected(client, skill):
    login(client, ADMIN)
    assert plan(client, start=-1).status_code == 422


def test_one_job_at_a_time(client, skill, fake_gemini):
    login(client, ADMIN)
    fake_gemini.hold = True
    first = plan(client).json()['jobId']
    r = plan(client)
    assert r.status_code == 409
    fake_gemini.hold = False
    assert wait_job(client, first)['status'] == 'done'
    assert plan(client).status_code == 200


def test_daily_limit_for_members(client, skill, settings, add_member):
    settings.daily_plan_limit = 1
    add_member()
    login(client, MEMBER)
    wait_job(client, plan(client).json()['jobId'])
    r = plan(client)
    assert r.status_code == 429 and '1회' in r.json()['detail']
    # 관리자는 제한하지 않는다
    login(client, ADMIN)
    for _ in range(2):
        assert wait_job(client, plan(client).json()['jobId'])['status'] == 'done'


def test_failed_jobs_do_not_count(client, skill, settings, add_member, fake_gemini):
    settings.daily_plan_limit = 1
    add_member()
    login(client, MEMBER)
    fake_gemini.replies = [gemini.GeminiError('x')]
    wait_job(client, plan(client).json()['jobId'])
    assert plan(client).status_code == 200


def test_job_visible_only_to_creator(client, skill, add_member):
    add_member()
    login(client, ADMIN)
    job_id = plan(client).json()['jobId']
    wait_job(client, job_id)
    login(client, MEMBER)
    assert client.get(f'/api/jobs/{job_id}').status_code == 404


def test_restart_marks_running_jobs(settings, app, client, db):
    from fastapi.testclient import TestClient

    with db:
        db.execute("INSERT INTO jobs (id, kind, status, input_json, created_by, created_at) "
                   "VALUES ('j1', 'plan', 'running', '{}', ?, ?)", (ADMIN, now_ms()))
    with TestClient(app, base_url=settings.public_origin):
        pass
    row = db.execute("SELECT status, error FROM jobs WHERE id = 'j1'").fetchone()
    assert row['status'] == 'error' and '재시작' in row['error']


# ---------- 설교 대본 ----------

SCRIPT = '[1:02:35] 오늘 본문은 출애굽기 14장입니다.\n\n[1:03:10] 두려움 앞에서 이스라엘은 멈춰 섰습니다.'


def wait_transcript(client, key, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        item = client.get(f'/api/shorts/{key}').json()
        if item['transcriptStatus'] != 'running':
            return item
        time.sleep(0.02)
    raise AssertionError(f'대본이 끝나지 않음: {item}')


def test_plan_also_writes_transcript(client, skill, fake_gemini):
    fake_gemini.replies = [PLAN_REPLY, SCRIPT]
    login(client, ADMIN)
    job = wait_job(client, plan(client).json()['jobId'])
    # 기획 결과는 대본을 기다리지 않고 끝난다(가짜 Gemini는 즉시 답해서 이미 끝나 있을 수도 있다)
    assert job['status'] == 'done' and job['shorts']['transcriptStatus'] in ('running', 'done')

    item = wait_transcript(client, job['shorts']['key'])
    assert item['transcriptStatus'] == 'done' and item['transcript'] == SCRIPT
    assert item['transcriptError'] is None
    assert item['text'] == PLAN_REPLY  # 기획 원문은 그대로

    body = fake_gemini.calls[1][1]
    video, prompt = body['contents'][0]['parts']
    assert video['video_metadata'] == {'start_offset': '3750s', 'end_offset': '6310s'}
    assert 'system_instruction' not in body  # 대본은 SKILL.md와 상관없다
    assert '그대로 받아써서' in prompt['text']
    assert '잘라낸 영상의 0:00은 원본의 1:02:30입니다.' in prompt['text']


def test_transcript_error_keeps_plan(client, skill, fake_gemini):
    fake_gemini.replies = [PLAN_REPLY, gemini.GeminiError('요청 한도를 넘었어요.')]
    login(client, ADMIN)
    job = wait_job(client, plan(client).json()['jobId'])
    assert job['status'] == 'done'
    item = wait_transcript(client, job['shorts']['key'])
    assert item['transcriptStatus'] == 'error' and item['transcriptError'] == '요청 한도를 넘었어요.'
    assert item['text'] == PLAN_REPLY


def test_make_transcript_again(client, skill, fake_gemini, add_member):
    fake_gemini.replies = [PLAN_REPLY, gemini.GeminiError('x')]
    login(client, ADMIN)
    key = wait_job(client, plan(client).json()['jobId'])['shorts']['key']
    wait_transcript(client, key)

    add_member()
    login(client, MEMBER)  # 팀원도 다시 만들 수 있다
    fake_gemini.replies = [SCRIPT]
    assert client.post(f'/api/shorts/{key}/transcript').json() == {'transcriptStatus': 'running'}
    assert wait_transcript(client, key)['transcript'] == SCRIPT


def test_make_transcript_requires_record_and_login(client):
    assert client.post('/api/shorts/9999/transcript').status_code == 401
    login(client, ADMIN)
    assert client.post('/api/shorts/9999/transcript').status_code == 404


def test_transcript_not_run_twice(client, skill, fake_gemini, db):
    login(client, ADMIN)
    with db:
        db.execute("INSERT INTO shorts (video_id, text, created_by, created_at, transcript_status) "
                   "VALUES (?, 't', ?, ?, 'running')", (VID, ADMIN, now_ms()))
        shorts_id = db.execute('SELECT MAX(id) FROM shorts').fetchone()[0]
    r = client.post(f'/api/shorts/{shorts_id}/transcript')
    assert r.status_code == 409 and '이미' in r.json()['detail']


def test_restart_marks_running_transcript(settings, app, client, db):
    from fastapi.testclient import TestClient

    with db:
        db.execute("INSERT INTO shorts (video_id, text, created_by, created_at, transcript_status) "
                   "VALUES (?, 't', ?, ?, 'running')", (VID, ADMIN, now_ms()))
    with TestClient(app, base_url=settings.public_origin):
        pass
    row = db.execute('SELECT transcript_status, transcript_error FROM shorts').fetchone()
    assert row['transcript_status'] == 'error' and '재시작' in row['transcript_error']


# ---------- 작업: 설교 구간 찾기 ----------

def test_detect(client, fake_gemini):
    fake_gemini.replies = ['```json\n{"start":"1:02:30","end":"1:45:10","title":"은혜의 자리"}\n```']
    login(client, ADMIN)  # SKILL.md 없이도 된다
    job = wait_job(client, client.post('/api/jobs', json={'kind': 'detect', 'videoId': VID}).json()['jobId'])
    assert job['status'] == 'done'
    assert job['result'] == {'start': 3750, 'end': 6310, 'title': '은혜의 자리'}
    body = fake_gemini.calls[0][1]
    assert body['generationConfig']['responseMimeType'] == 'application/json'
    assert 'video_metadata' not in body['contents'][0]['parts'][0]


@pytest.mark.parametrize('reply', ['not json', '{"start":"1:00:00","end":"0:59:00"}', '{"start":"", "end":"1:00"}'])
def test_detect_bad_reply(client, fake_gemini, reply):
    fake_gemini.replies = [reply]
    login(client, ADMIN)
    job = wait_job(client, client.post('/api/jobs', json={'kind': 'detect', 'videoId': VID}).json()['jobId'])
    assert job['status'] == 'error' and '직접 입력' in job['error']


def test_detect_does_not_count_or_save(client, settings, add_member, fake_gemini, db):
    settings.daily_plan_limit = 0
    add_member()
    login(client, MEMBER)
    fake_gemini.replies = ['{"start":"10:00","end":"40:00"}']
    job = wait_job(client, client.post('/api/jobs', json={'kind': 'detect', 'videoId': VID}).json()['jobId'])
    assert job['status'] == 'done'
    assert db.execute('SELECT COUNT(*) FROM shorts').fetchone()[0] == 0


# ---------- 쇼츠 기록 (팀 공용) ----------

def make_shorts(client, email, n=1):
    login(client, email)
    return [wait_job(client, plan(client).json()['jobId'])['shorts'] for _ in range(n)]


def test_shorts_shared_with_team(client, skill, add_member):
    add_member(name='홍길동')
    [mine] = make_shorts(client, MEMBER)
    [admins] = make_shorts(client, ADMIN)

    login(client, MEMBER)
    items = client.get('/api/shorts').json()['items']
    assert [i['key'] for i in items] == [admins['key'], mine['key']]
    assert 'text' not in items[0]
    by = {i['key']: (i['byName'], i['canDelete']) for i in items}
    assert by[mine['key']] == ('홍길동', True)
    assert by[admins['key']] == ('admin', False)
    # 상세는 원문 포함
    assert client.get(f"/api/shorts/{admins['key']}").json()['text'] == PLAN_REPLY


def test_member_deletes_only_own(client, skill, add_member):
    add_member()
    [mine] = make_shorts(client, MEMBER)
    [admins] = make_shorts(client, ADMIN)
    login(client, MEMBER)
    r = client.delete(f"/api/shorts/{admins['key']}")
    assert r.status_code == 403 and '본인' in r.json()['detail']
    assert client.delete(f"/api/shorts/{mine['key']}").status_code == 200
    assert client.get(f"/api/shorts/{mine['key']}").status_code == 404


def test_admin_deletes_any(client, skill, add_member):
    add_member()
    [mine] = make_shorts(client, MEMBER)
    login(client, ADMIN)
    assert client.delete(f"/api/shorts/{mine['key']}").status_code == 200
    assert client.delete(f"/api/shorts/{mine['key']}").status_code == 404


def test_shorts_pagination(client, skill, db):
    login(client, ADMIN)
    with db:
        for i in range(5):
            db.execute("INSERT INTO shorts (video_id, text, created_by, created_at) VALUES (?, 't', ?, ?)",
                       (VID, ADMIN, 1000 + i))
    page = client.get('/api/shorts?limit=2').json()
    assert [i['at'] for i in page['items']] == [1004, 1003] and page['more'] is True
    page = client.get('/api/shorts?limit=2&before=1003').json()
    assert [i['at'] for i in page['items']] == [1002, 1001] and page['more'] is True
    page = client.get('/api/shorts?limit=2&before=1001').json()
    assert [i['at'] for i in page['items']] == [1000] and page['more'] is False


def test_shorts_list_requires_login(client):
    assert client.get('/api/shorts').status_code == 401


# ---------- 단위 ----------

def kst_ms(*args):
    return int(datetime(*args, tzinfo=KST).timestamp() * 1000)


def test_shorts_title_uses_week_sunday():
    assert shorts_title(kst_ms(2026, 10, 19, 9), '') == '261018 쇼츠 기획'          # 월요일
    assert shorts_title(kst_ms(2026, 10, 18, 23, 59), '설교') == '261018 쇼츠 기획 · 설교'  # 일요일
    assert shorts_title(kst_ms(2026, 10, 24, 12), '') == '261018 쇼츠 기획'          # 토요일


def test_fmt_and_parse_time():
    assert gemini.fmt_time(3750) == '1:02:30'
    assert gemini.fmt_time(65) == '1:05'
    assert gemini.parse_hms('1:02:30') == 3750
    assert gemini.parse_hms('62:30') == 3750
    assert gemini.parse_hms('') is None


def test_extract_text():
    ok = {'candidates': [{'content': {'parts': [{'text': '생각', 'thought': True}, {'text': 'A'}, {'text': 'B'}]}}]}
    assert gemini.extract_text(200, ok) == 'AB'
    with pytest.raises(gemini.GeminiError, match='요청 한도'):
        gemini.extract_text(429, {'error': {'message': 'quota'}})
    with pytest.raises(gemini.GeminiError, match='SAFETY'):
        gemini.extract_text(200, {'promptFeedback': {'blockReason': 'SAFETY'}})
    with pytest.raises(gemini.GeminiError, match='bad key'):
        gemini.extract_text(400, {'error': {'message': 'bad key'}})


def test_strip_frontmatter():
    assert gemini.strip_frontmatter('﻿---\r\nname: a\r\n---\r\n본문') == '본문'
    assert gemini.strip_frontmatter('본문만') == '본문만'


def test_real_client_without_key():
    import asyncio
    call = gemini.make_client('')
    with pytest.raises(gemini.GeminiError, match='API 키'):
        asyncio.run(call('m', {}))
