"""
v2 브라우저 통합 테스트 (web/index.html + FastAPI 서버).

실제 서버(uvicorn)를 임시 DB로 띄우고, Google 로그인·ID 토큰 검증·Gemini·YouTube는 가짜로 바꿔서
관리자/팀원/미등록 사용자 흐름, 작업 기록(검색·페이지·주일 묶음), 작업 폴링(새로고침 후 이어 받기),
#t= 표기 제거, PDF를 확인합니다.

    .venv/Scripts/python -m pip install -r server/requirements-dev.txt playwright pypdf
    .venv/Scripts/python tests/e2e_v2.py
"""
import asyncio
import datetime
import io
import json
import pathlib
import socket
import sys
import tempfile
import threading
import time

import uvicorn
from playwright.sync_api import sync_playwright
from pypdf import PdfReader

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'server'))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

FIX = ROOT / 'tests' / 'fixtures'
SETLIST = (FIX / 'setlist-sample.txt').read_text(encoding='utf-8')
SHORTS = (FIX / 'shorts-response.md').read_text(encoding='utf-8')
VIDEO = 're4fR9uW0Po'
ADMIN, MEMBER, STRANGER = 'admin@example.com', 'member@example.com', 'stranger@example.com'
SKILL = '---\nname: t\n---\n쇼츠 지침'
SCRIPT = ('[0:38:54] 오늘 본문은 출애굽기 14장입니다.\n\n'
          '[0:40:12] 두려움 앞에서 이스라엘 백성은 멈춰 섰습니다.')

# 사이트 로그인(google.accounts.id)과 YouTube 토큰(google.accounts.oauth2)을 흉내 낸다.
# 로그인 버튼을 누르면 window.__cred 를 ID 토큰으로 넘긴다.
FAKE_GSI = """window.google={accounts:{
 id:{initialize(o){window.__gsiCb=o.callback},
     renderButton(el){el.innerHTML='<button id="fakeGsi">Google로 로그인</button>';
       el.querySelector('button').onclick=()=>window.__gsiCb({credential:window.__cred})},
     disableAutoSelect(){}},
 oauth2:{initTokenClient:(o)=>{const c={callback:o.callback,
   requestAccessToken(){setTimeout(()=>c.callback({access_token:'tok',expires_in:3600}),10)}};return c;}}}};"""


def fake_verifier(credential, client_id):
    kind, _, email = credential.partition(':')
    if kind != 'ok':
        raise ValueError('bad token')
    return {'email': email, 'email_verified': True, 'name': email.split('@')[0]}


class FakeGemini:
    def __init__(self):
        self.delay = 0.0
        self.calls = []

    async def __call__(self, model, body):
        self.calls.append(body)
        await asyncio.sleep(self.delay)
        if body['generationConfig'].get('responseMimeType'):
            return json.dumps({'start': '0:38:54', 'end': '1:20:00', 'title': '두려움을 이기는 믿음'}, ensure_ascii=False)
        assert body['contents'][0]['parts'][0]['video_metadata'] == {'start_offset': '2334s', 'end_offset': '4800s'}
        if '그대로 받아써서' in body['contents'][0]['parts'][1]['text']:   # 설교 대본
            assert 'system_instruction' not in body
            return SCRIPT
        assert body['system_instruction']['parts'][0]['text'] == '쇼츠 지침'
        return SHORTS


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def start_v2(db_path, gem):
    port = free_port()
    settings = Settings(db_path=db_path, google_client_id='test.apps.googleusercontent.com',
                        admin_emails=[ADMIN], public_origin=f'http://127.0.0.1:{port}')
    app = create_app(settings, verify_token=fake_verifier, gemini_call=gem)
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning'))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    return server, f'http://127.0.0.1:{port}'


def youtube_route(counter):
    def handle(route):
        if 'playlists?' in route.request.url:
            assert json.loads(route.request.post_data)['status']['privacyStatus'] == 'unlisted'
            counter['n'] += 1
            route.fulfill(status=200, content_type='application/json',
                          body=json.dumps({'id': f"PLtestplaylist{counter['n']}"}))
        else:
            route.fulfill(status=200, content_type='application/json', body='{}')
    return handle


def new_page(browser, cred, errors, counter):
    ctx = browser.new_context(viewport={'width': 390, 'height': 844}, accept_downloads=True)
    ctx.add_init_script(f'window.__cred = {json.dumps(cred)};')
    pg = ctx.new_page()
    pg.on('pageerror', lambda e: errors.append(str(e)))
    pg.route('**/accounts.google.com/**', lambda r: r.fulfill(status=200, content_type='text/javascript', body=FAKE_GSI))
    pg.route('**/www.googleapis.com/youtube/**', youtube_route(counter))
    pg.route('**/i.ytimg.com/**', lambda r: r.abort())
    pg.route('**/generativelanguage.googleapis.com/**', lambda r: r.abort())  # 브라우저는 Gemini를 부르면 안 됨
    return ctx, pg


def login(pg, base):
    pg.goto(base + '/')
    pg.wait_for_selector('#fakeGsi')
    pg.click('#fakeGsi')
    pg.wait_for_selector('#app:not([hidden])')


def api(pg, path, method='GET'):
    return pg.evaluate("""async ([p, m]) => { const r = await fetch(p, {method: m});
        return {status: r.status, body: await r.json().catch(() => null)}; }""", [path, method])


def main():
    tmp = tempfile.mkdtemp()
    gem = FakeGemini()
    server, base = start_v2(str(pathlib.Path(tmp) / 'worship.db'), gem)
    errors, counter = [], {'n': 0}

    with sync_playwright() as p:
        browser = p.chromium.launch()

        # ---------- 미등록 계정 ----------
        ctx, pg = new_page(browser, f'ok:{STRANGER}', errors, counter)
        pg.goto(base + '/')
        pg.wait_for_selector('#fakeGsi')
        pg.click('#fakeGsi')
        pg.wait_for_function("document.querySelector('#loginMsg').textContent.includes('등록되지 않은 계정')")
        assert pg.is_hidden('#app')
        ctx.close()

        # ---------- 관리자 ----------
        ctx, pg = new_page(browser, f'ok:{ADMIN}', errors, counter)
        login(pg, base)
        assert '관리자' in pg.inner_text('#whoami')
        assert pg.is_visible('#tabs') and pg.is_visible('#tab-setlist')

        # 설정: SKILL.md, 팀원 추가
        pg.click('.tabs button[data-tab=admin]')
        pg.wait_for_function("document.querySelector('#skillMeta').textContent.includes('아직 등록된')")
        pg.fill('#skillText', SKILL)
        pg.click('#saveSettings')
        pg.wait_for_function("document.querySelector('#settingsMsg').textContent === '저장했어요.'")
        assert '마지막 수정' in pg.inner_text('#skillMeta')
        # 자동 쇼츠 기획: 체크하면 바로 저장되고, 다른 카드의 저장에도 풀리지 않는다
        pg.fill('#ytChannel', 'https://www.youtube.com/@bridgechurch_Daejeon/streams')
        pg.click('#saveAuto')
        pg.wait_for_function("document.querySelector('#autoMsg').textContent === '저장했어요.'")
        pg.check('#autoEnabled')
        pg.wait_for_function("document.querySelector('#autoMsg').textContent.startsWith('켰어요')")
        assert '켜짐' in pg.inner_text('#autoState')
        pg.click('#saveSettings')   # 위쪽 카드를 저장해도
        pg.wait_for_function("document.querySelector('#settingsMsg').textContent === '저장했어요.'")
        assert pg.is_checked('#autoEnabled') and '켜짐' in pg.inner_text('#autoState')
        st = api(pg, '/api/settings')['body']
        assert st['autoEnabled'] is True and st['channel'].endswith('@bridgechurch_Daejeon/streams')
        pg.uncheck('#autoEnabled')
        pg.wait_for_function("document.querySelector('#autoMsg').textContent === '껐어요.'")
        assert api(pg, '/api/settings')['body']['autoEnabled'] is False

        pg.fill('#mEmail', MEMBER)
        pg.fill('#mName', '홍길동')
        pg.click('#addMember')
        pg.wait_for_function("document.querySelectorAll('#memberList .mrow').length === 2")
        assert pg.locator('#memberList .mrow').nth(0).locator('button').count() == 0  # 관리자는 삭제 버튼 없음

        # 세트리스트 → 재생목록 2개
        pg.click('.tabs button[data-tab=setlist]')
        pg.fill('#input', SETLIST)
        pg.click('#parseBtn')
        assert pg.locator('#blocks > .card').count() == 4
        pg.locator('#blocks .create').nth(0).click()
        pg.wait_for_selector('#blocks .card >> nth=0 >> .result')
        pg.locator('#blocks .create').nth(1).click()
        pg.wait_for_selector('#blocks .card >> nth=1 >> .result')
        pg.wait_for_function("""async () => (await (await fetch('/api/setlists')).json()).items.length === 3""")
        saved = api(pg, '/api/setlists/PLtestplaylist1')
        assert saved['status'] == 200 and saved['body']['rawMessage'] == SETLIST.replace('\r', ''), saved['status']
        assert saved['body']['title'] == 'Setlist 261118'

        # 쇼츠 기획 (서버 작업 + 폴링)
        pg.click('.tabs button[data-tab=shorts]')
        pg.fill('#liveUrl', f'https://www.youtube.com/live/{VIDEO}?si=abc')
        pg.click('#detectBtn')
        pg.wait_for_function("document.querySelector('#tStart').value === '38:54'", timeout=15000)
        assert pg.input_value('#tEnd') == '1:20:00'
        pg.click('#planBtn')
        pg.wait_for_function("document.querySelector('#shortsMsg').textContent.startsWith('완료')", timeout=15000)
        out_html = pg.inner_html('#shortsOut')
        assert '#t=' not in out_html, '화면에 #t= 표기가 남아 있음'
        assert pg.locator('#shortsOut a.ts').first.get_attribute('href') == f'https://youtu.be/{VIDEO}?t=2334'
        assert '두려움을 이기는 믿음' in pg.inner_text('#shortsOut .vh-title')

        # PDF
        with pg.expect_download() as d:
            pg.click('#shortsOut .pdfSave')
        dl = d.value
        assert dl.suggested_filename.startswith('쇼츠기획_') and dl.suggested_filename.endswith('.pdf')
        reader = PdfReader(io.BytesIO(pathlib.Path(dl.path()).read_bytes()))
        text = '\n'.join(pg_.extract_text() for pg_ in reader.pages)
        assert '#t=' not in text and '쇼츠 1: 두려움 대신 믿음' in text
        uris = [a.get_object()['/A']['/URI'] for page in reader.pages for a in page.get('/Annots', [])]
        assert f'https://youtu.be/{VIDEO}?t=2412' in uris, uris[:5]

        # 설교 대본: 기획이 끝난 뒤 서버가 이어서 받아쓰고, '설교 대본 보기'로 펼친다
        pg.click('#shortsOut .scriptBtn')
        pg.wait_for_selector('#shortsOut .md.script', timeout=20000)
        assert '출애굽기 14장' in pg.inner_text('#shortsOut .md.script')
        assert '#t=' not in pg.inner_html('#shortsOut .md.script')

        # 새로고침 → 로그인 유지, 최신 쇼츠 표시
        pg.reload()
        pg.wait_for_selector('#app:not([hidden])')
        pg.wait_for_selector('#shortsOut .md')

        # ---------- 작업 기록 (세트리스트 + 쇼츠 기획을 한곳에서) ----------
        # 페이지 넘기기와 주일 묶음을 보려고 기록을 더 심는다(이번 주일 + 지난 10주).
        sunday = datetime.date.today()
        sunday -= datetime.timedelta(days=(sunday.weekday() + 1) % 7)
        seeds = []
        for i in range(11):
            d = sunday - datetime.timedelta(days=7 * i)
            seeds.append({'playlistId': f'PLseed{i:012d}', 'title': f'Setlist {d:%y%m%d}', 'ok': 1, 'total': 1,
                          'url': f'https://www.youtube.com/playlist?list=PLseed{i:012d}',
                          'songs': [{'id': VIDEO, 'name': '씨앗 찬양', 'notes': []}], 'rawMessage': ''})
        seeded = pg.evaluate("""async (rows) => { const out = [];
            for(const body of rows){ const r = await fetch('/api/setlists', {method: 'POST',
              headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}); out.push(r.status); }
            return out; }""", seeds)
        assert seeded == [200] * 11, seeded

        pg.click('.tabs button[data-tab=records]')
        pg.wait_for_selector('#recList .wk')
        assert '세트리스트 13 · 쇼츠 기획 1' in pg.inner_text('#recCount')

        # 주일 10개씩 + 페이지 넘기기 (언제나 주일 기준으로 묶는다)
        assert pg.locator('#recList .wk').count() == 10 and pg.inner_text('#recPage') == '1 / 2'
        pg.click('#recNext')
        pg.wait_for_function("document.querySelector('#recPage').textContent === '2 / 2'")
        assert pg.locator('#recList .wk').count() >= 1
        pg.click('#recPrev')
        pg.wait_for_function("document.querySelector('#recPage').textContent === '1 / 2'")

        # 같은 주의 세트리스트와 쇼츠 기획이 한 묶음
        week = pg.locator(f'#recList .wk', has=pg.locator('.kind.k-shorts')).first
        assert week.locator('.kind').all_inner_texts() == ['세트리스트', '쇼츠 기획']
        assert f'{sunday.year}년 {sunday.month}월 {sunday.day}일 주일' in week.locator('.wk-d').inner_text()
        # 묶음 머리에 그 주에 뭐가 몇 건인지
        assert week.locator('.wk-n').inner_text() == '세트리스트 1 · 쇼츠 기획 1'

        # 종류 거르기
        pg.select_option('#recKind', 'shorts')
        pg.wait_for_function("document.querySelectorAll('#recList .hrow').length === 1")
        assert set(pg.locator('#recList .kind').all_inner_texts()) == {'쇼츠 기획'}
        pg.select_option('#recKind', 'all')
        pg.wait_for_function("document.querySelectorAll('#recList .wk').length === 10")

        # 날짜로 검색 → 세트리스트 상세
        pg.fill('#recQ', '261118')
        pg.click('#recSearch')
        pg.wait_for_selector('#recList .hrow[data-key=PLtestplaylist1]')
        assert pg.locator('#recList .hrow').count() == 1
        pg.click('#recList .hopen')
        pg.wait_for_selector('#recordView .song')
        assert pg.input_value('#recordView .link').endswith('PLtestplaylist1')
        assert pg.locator('#recordView .song').count() == 3

        # 찬양 제목으로 검색 → 그 주의 쇼츠 기획도 함께 (한 묶음)
        pg.fill('#recQ', '씨앗 찬양')
        pg.click('#recSearch')
        pg.wait_for_function("document.querySelectorAll('#recList .wk').length === 10")
        hit = pg.locator('#recList .wk', has=pg.locator('.kind.k-shorts')).first
        assert hit.locator('.kind').all_inner_texts() == ['세트리스트', '쇼츠 기획']

        # 검색된 이유: 곡 제목에서 맞았다고 알려 주고, 맞은 기록만 모아 보여 준다
        assert pg.is_visible('#recWhy')
        why = pg.locator('#recWhyList .hrow')
        assert why.count() == 11 and set(pg.locator('#recWhyList .kind').all_inner_texts()) == {'세트리스트'}
        assert '곡 제목' in why.first.inner_text()
        assert pg.locator('#recWhyList mark').first.inner_text() == '씨앗'  # 조각마다 표시

        # 주제 키워드로 검색 → 쇼츠 기획 상세 (본문까지 찾는다)
        pg.fill('#recQ', '두려움')
        pg.click('#recSearch')
        pg.wait_for_function("document.querySelectorAll('#recList .wk').length === 1")
        assert set(pg.locator('#recList .kind').all_inner_texts()) == {'세트리스트', '쇼츠 기획'}
        assert pg.locator('#recWhyList .hrow').count() == 1  # 같은 주 세트리스트는 이유 목록에 없다
        assert '제목' in pg.inner_text('#recWhyList')
        pg.fill('#recQ', '감사의 고백')   # 제목에는 없고 쇼츠 본문에만 있는 말
        pg.click('#recSearch')
        pg.wait_for_function("document.querySelector('#recWhyList').textContent.includes('쇼츠 본문')")
        assert pg.locator('#recWhyList .hrow').count() == 1

        pg.fill('#recQ', '두려움')
        pg.click('#recSearch')
        pg.wait_for_function("document.querySelectorAll('#recList .wk').length === 1")
        pg.locator('#recList .hrow[data-kind=shorts] .hopen').click()
        pg.wait_for_selector('#recordView .md')
        assert '#t=' not in pg.inner_html('#recordView')
        assert pg.locator('#recordView a.ts').first.get_attribute('href') == f'https://youtu.be/{VIDEO}?t=2334'
        assert '두려움을 이기는 믿음' in pg.inner_text('#recordView .vh-title')

        # 작업 기록에서도 설교 대본을 본다
        pg.click('#recordView .scriptBtn')
        pg.wait_for_selector('#recordView .md.script')
        assert '출애굽기 14장' in pg.inner_text('#recordView .md.script')
        assert pg.locator('#recordView .md.script a.ts').first.get_attribute('href') == f'https://youtu.be/{VIDEO}?t=2334'
        pg.click('#recordView .scriptBtn')
        pg.wait_for_function("!document.querySelector('#recordView .md.script')")
        pg.click('#recordView .close')
        pg.wait_for_function("!document.querySelector('#recordView .md')")

        # 상세에서 2단계 삭제
        pg.fill('#recQ', '261011')
        pg.click('#recSearch')
        pg.wait_for_selector('#recList .hrow[data-key=PLtestplaylist2]')
        pg.click('#recList .hrow[data-key=PLtestplaylist2] .hopen')
        pg.wait_for_selector('#recordView .del')
        pg.click('#recordView .del')
        pg.click('#recordView .del')
        pg.wait_for_function("!document.querySelector('#recList .hrow[data-key=PLtestplaylist2]')")
        assert api(pg, '/api/setlists/PLtestplaylist2')['status'] == 404
        admin_ctx, admin_pg = ctx, pg

        # ---------- 팀원 ----------
        ctx, pg = new_page(browser, f'ok:{MEMBER}', errors, counter)
        login(pg, base)
        assert '홍길동 · 팀원' == pg.inner_text('#whoami')
        assert pg.is_visible('#tabs') and pg.is_hidden('#tab-setlist') and pg.is_hidden('#tab-admin')
        assert pg.locator('.tabs button:visible').all_inner_texts() == ['쇼츠 기획', '작업 기록']
        assert pg.is_visible('#tab-shorts')
        for path in ('/api/setlists', '/api/settings', '/api/members'):
            assert api(pg, path)['status'] == 403, path

        # 팀원도 작업 기록을 본다. 세트리스트 기록도 열 수 있지만(그 주 찬양 확인) 지우지는 못한다.
        pg.click('.tabs button[data-tab=records]')
        pg.wait_for_selector('#recList .hrow')
        assert '세트리스트 12 · 쇼츠 기획 1' in pg.inner_text('#recCount')
        pg.fill('#recQ', '261118')
        pg.click('#recSearch')
        pg.wait_for_selector('#recList .hrow[data-key=PLtestplaylist1]')
        pg.click('#recList .hopen')
        pg.wait_for_selector('#recordView .song')
        assert pg.locator('#recordView .song').count() == 3
        assert pg.locator('#recordView .del').count() == 0
        pg.click('.tabs button[data-tab=shorts]')

        # 작업 중 새로고침해도 결과를 이어 받는다
        gem.delay = 3
        pg.fill('#liveUrl', f'https://youtu.be/{VIDEO}')
        pg.fill('#tStart', '38:54')
        pg.fill('#tEnd', '1:20:00')
        pg.click('#planBtn')
        pg.wait_for_function("localStorage.getItem('activeJob')")
        pg.reload()
        pg.wait_for_function("document.querySelector('#shortsMsg').textContent.includes('진행 중이던')")
        pg.wait_for_function("document.querySelector('#shortsMsg').textContent.startsWith('완료')", timeout=20000)
        gem.delay = 0

        # 쇼츠 기록 삭제는 본인이 만든 것만
        pg.click('.tabs button[data-tab=records]')
        pg.wait_for_function("document.querySelector('#recCount').textContent.includes('세트리스트 12 · 쇼츠 기획 2')")
        pg.select_option('#recKind', 'shorts')
        pg.wait_for_function("document.querySelectorAll('#recList .hrow').length === 2")
        assert '홍길동' in pg.locator('#recList .hrow').first.inner_text()
        pg.locator('#recList .hopen').first.click()
        pg.wait_for_selector('#recordView .md')
        pg.click('#recordView .del')
        pg.click('#recordView .del')
        pg.wait_for_function("document.querySelectorAll('#recList .hrow').length === 1")
        pg.locator('#recList .hopen').first.click()  # 관리자가 만든 기록
        pg.wait_for_selector('#recordView .md')
        assert pg.locator('#recordView .del').count() == 0

        # 로그아웃
        pg.click('#logoutBtn')
        pg.wait_for_selector('#login:not([hidden])')
        assert api(pg, '/api/me')['status'] == 401
        ctx.close()

        # 관리자가 팀원을 삭제
        admin_pg.click('.tabs button[data-tab=admin]')
        admin_pg.wait_for_function("document.querySelectorAll('#memberList .mrow').length === 2")
        admin_pg.locator('#memberList .mrow button').click()
        admin_pg.locator('#memberList .mrow button').click()
        admin_pg.wait_for_function("document.querySelectorAll('#memberList .mrow').length === 1")
        admin_ctx.close()

        browser.close()
    server.should_exit = True
    assert not errors, errors
    print('OK: v2 테스트 모두 통과')


if __name__ == '__main__':
    main()
