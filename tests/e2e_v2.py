"""
v2 브라우저 통합 테스트 (web/index.html + FastAPI 서버).

실제 서버(uvicorn)를 임시 DB로 띄우고, Google 로그인·ID 토큰 검증·Gemini·YouTube는 가짜로 바꿔서
관리자/팀원/미등록 사용자 흐름, v1 기록 내보내기 → v2 가져오기, 작업 폴링(새로고침 후 이어 받기),
#t= 표기 제거, PDF를 확인합니다.

    .venv/Scripts/python -m pip install -r server/requirements-dev.txt playwright pypdf
    .venv/Scripts/python tests/e2e_v2.py
"""
import asyncio
import functools
import http.server
import io
import json
import pathlib
import socket
import socketserver
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


def start_v1():
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    srv = socketserver.TCPServer(('127.0.0.1', 0), functools.partial(Quiet, directory=str(ROOT)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f'http://127.0.0.1:{srv.server_address[1]}/index.html'


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


def export_from_v1(browser, errors):
    """v1 페이지에 기록을 심어 두고 '기록 내보내기'로 파일을 받는다."""
    srv, url = start_v1()
    hist_setlist = [{'key': 'PLv1imported0001', 'title': 'Setlist 261011', 'url': 'https://www.youtube.com/playlist?list=PLv1imported0001',
                     'at': 1760000000000, 'ok': 1, 'total': 1, 'songs': [{'id': VIDEO, 'name': '예전 곡', 'notes': ['KEY - G']}]}]
    hist_shorts = [{'key': 's1760100000000', 'id': VIDEO, 'start': 2334, 'end': 4800, 'text': SHORTS,
                    'at': 1760100000000, 'title': '251012 쇼츠 기획 · 예전 설교', 'extra': ''}]
    ctx = browser.new_context(accept_downloads=True)
    ctx.add_init_script(f"""localStorage.setItem('hist.setlist', {json.dumps(json.dumps(hist_setlist))});
        localStorage.setItem('hist.shorts', {json.dumps(json.dumps(hist_shorts, ensure_ascii=False))});
        localStorage.setItem('skillText', {json.dumps(SKILL + '\n추가 줄')});
        localStorage.setItem('geminiKey', 'AIzaSECRET');""")
    pg = ctx.new_page()
    pg.on('pageerror', lambda e: errors.append('v1: ' + str(e)))
    pg.route('**/accounts.google.com/**', lambda r: r.fulfill(status=200, content_type='text/javascript', body=''))
    pg.goto(url)
    if not pg.eval_on_selector('#settings', 'e => e.open'):
        pg.click('#settings summary')
    with pg.expect_download() as d:
        pg.click('#exportBtn')
    path = pathlib.Path(tempfile.mkdtemp()) / d.value.suggested_filename
    d.value.save_as(path)
    data = json.loads(path.read_text(encoding='utf-8'))
    assert d.value.suggested_filename.startswith('worship-tools-기록_')
    assert len(data['setlist']) == 1 and len(data['shorts']) == 1
    assert 'AIzaSECRET' not in path.read_text(encoding='utf-8'), 'API 키가 내보내기 파일에 들어감'
    ctx.close()
    srv.shutdown()
    return path


def main():
    tmp = tempfile.mkdtemp()
    gem = FakeGemini()
    server, base = start_v2(str(pathlib.Path(tmp) / 'worship.db'), gem)
    errors, counter = [], {'n': 0}

    with sync_playwright() as p:
        browser = p.chromium.launch()
        export_file = export_from_v1(browser, errors)

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
        pg.on('dialog', lambda d: d.accept())
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
        pg.fill('#mEmail', MEMBER)
        pg.fill('#mName', '홍길동')
        pg.click('#addMember')
        pg.wait_for_function("document.querySelectorAll('#memberList .mrow').length === 2")
        assert pg.locator('#memberList .mrow').nth(0).locator('button').count() == 0  # 관리자는 삭제 버튼 없음

        # v1 기록 가져오기 (SKILL.md가 달라서 바꿀지 물어봄 → 수락)
        pg.set_input_files('#importFile', str(export_file))
        pg.wait_for_function("document.querySelector('#importMsg').textContent.includes('SKILL.md도 바꿨어요')")
        assert '세트리스트 1개' in pg.inner_text('#importMsg') and '쇼츠 1개' in pg.inner_text('#importMsg')
        assert pg.input_value('#skillText').endswith('추가 줄')
        # 테스트를 위해 지침을 원래대로
        pg.fill('#skillText', SKILL)
        pg.click('#saveSettings')
        pg.wait_for_function("document.querySelector('#skillMeta').textContent.includes('마지막 수정')")

        # 세트리스트 → 재생목록 2개
        pg.click('.tabs button[data-tab=setlist]')
        assert pg.inner_text('#histCount-setlist') == '1'
        pg.fill('#input', SETLIST)
        pg.click('#parseBtn')
        assert pg.locator('#blocks > .card').count() == 4
        pg.locator('#blocks .create').nth(0).click()
        pg.wait_for_selector('#blocks .card >> nth=0 >> .result')
        pg.locator('#blocks .create').nth(1).click()
        pg.wait_for_selector('#blocks .card >> nth=1 >> .result')
        pg.wait_for_function("document.querySelector('#histCount-setlist').textContent === '3'")
        saved = api(pg, '/api/setlists/PLtestplaylist1')
        assert saved['status'] == 200 and saved['body']['rawMessage'] == SETLIST.replace('\r', ''), saved['status']
        assert saved['body']['title'] == 'Setlist 261118'

        # 기록 열기 / 2단계 삭제
        pg.click('#tab-setlist details.hist summary')
        pg.locator('#hist-setlist .hopen').nth(1).click()
        assert pg.input_value('#setlistView .link').endswith('PLtestplaylist1')
        assert pg.locator('#setlistView .song').count() == 3
        pg.locator('#hist-setlist .hdel').nth(0).click()
        assert pg.inner_text('#histCount-setlist') == '3'
        pg.locator('#hist-setlist .hdel').nth(0).click()
        pg.wait_for_function("document.querySelector('#histCount-setlist').textContent === '2'")
        assert api(pg, '/api/setlists/PLtestplaylist2')['status'] == 404

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
        assert pg.inner_text('#histCount-shorts') == '2'

        # PDF
        with pg.expect_download() as d:
            pg.click('#pdfSave')
        dl = d.value
        assert dl.suggested_filename.startswith('쇼츠기획_') and dl.suggested_filename.endswith('.pdf')
        reader = PdfReader(io.BytesIO(pathlib.Path(dl.path()).read_bytes()))
        text = '\n'.join(pg_.extract_text() for pg_ in reader.pages)
        assert '#t=' not in text and '쇼츠 1: 두려움 대신 믿음' in text
        uris = [a.get_object()['/A']['/URI'] for page in reader.pages for a in page.get('/Annots', [])]
        assert f'https://youtu.be/{VIDEO}?t=2412' in uris, uris[:5]

        # 새로고침 → 로그인 유지, 기록 유지, 최신 쇼츠 표시
        pg.reload()
        pg.wait_for_selector('#app:not([hidden])')
        pg.wait_for_function("document.querySelector('#histCount-shorts').textContent === '2'")
        pg.wait_for_selector('#shortsOut .md')
        admin_ctx, admin_pg = ctx, pg

        # ---------- 팀원 ----------
        ctx, pg = new_page(browser, f'ok:{MEMBER}', errors, counter)
        login(pg, base)
        assert '홍길동 · 팀원' == pg.inner_text('#whoami')
        assert pg.is_hidden('#tabs') and pg.is_hidden('#tab-setlist') and pg.is_hidden('#tab-admin')
        assert pg.is_visible('#tab-shorts')
        pg.wait_for_function("document.querySelector('#histCount-shorts').textContent === '2'")
        pg.click('#tab-shorts details.hist summary')
        assert pg.locator('#hist-shorts .hdel').count() == 0  # 관리자 기록은 지울 수 없음
        for path in ('/api/setlists', '/api/settings', '/api/members'):
            assert api(pg, path)['status'] == 403, path

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
        assert pg.inner_text('#histCount-shorts') == '3'
        assert '홍길동' in pg.text_content('#hist-shorts .hrow >> nth=0')  # 새로고침으로 기록 목록은 접혀 있음
        pg.click('#tab-shorts details.hist summary')
        assert pg.locator('#hist-shorts .hdel').count() == 1  # 본인 것만
        pg.locator('#hist-shorts .hdel').click()
        pg.locator('#hist-shorts .hdel').click()
        pg.wait_for_function("document.querySelector('#histCount-shorts').textContent === '2'")

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
