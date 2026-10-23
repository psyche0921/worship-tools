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
import zipfile

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
 oauth2:{initTokenClient:(o)=>{
   // youtube와 drive.file은 한 번에 요청할 수 없다(400 invalid_request). 범위마다 따로 받아야 한다.
   if(String(o.scope).trim().split(/\\s+/).length>1) throw new Error('범위를 한꺼번에 요청했어요: '+o.scope);
   if(o.include_granted_scopes!==false) throw new Error('include_granted_scopes를 꺼야 해요');
   window.__scopes=(window.__scopes||[]).concat(o.scope);
   const c={callback:o.callback,
   requestAccessToken(){setTimeout(()=>c.callback({access_token:'tok',expires_in:3600}),10)}};return c;}}}};"""


# 유튜브 IFrame 플레이어 흉내. 재생목록을 만들지 않고 영상 ID 배열을 큐에 넣는지 본다.
FAKE_YT = """window.YT={Player:class{
 constructor(el,o){this.o=o;window.__player=this;
   setTimeout(()=>o.events&&o.events.onReady&&o.events.onReady({target:this}),10);}
 loadPlaylist(a){window.__cued=a;}
 cuePlaylist(a){window.__cued=a;}
 playVideoAt(i){window.__cued=Object.assign({},window.__cued,{index:i});}
 getVideoData(){const c=window.__cued||{playlist:[]};
   return {video_id:c.playlist[c.index||0]||''};}
 nextVideo(){}
}};
if(window.onYouTubeIframeAPIReady) window.onYouTubeIframeAPIReady();"""

# 드라이브에 들어 있는 것으로 가정하는 악보들(이름 규칙이 없는 상황을 흉내 낸다).
# '한 번만 쓰는 가져오기'가 이 목록을 보고 서버로 옮긴다.
DRIVE_FILES = [
    {'id': 'd1', 'name': '01_새 힘 얻으리_A.pdf', 'mimeType': 'application/pdf',
     'size': '2048', 'modifiedTime': '2026-10-01T03:04:05.000Z'},
    {'id': 'd2', 'name': '나로부터.pdf', 'mimeType': 'application/pdf',
     'size': '2048', 'modifiedTime': '2026-10-01T03:04:05.000Z'},
    {'id': 'd3', 'name': '기대 (G).pdf', 'mimeType': 'application/pdf',
     'size': '2048', 'modifiedTime': '2026-10-01T03:04:05.000Z'},
]
FAKE_PDF = b'%PDF-1.4\n% e2e fake sheet\n'


def drive_route(downloads):
    """드라이브 API 흉내: 악보를 서버로 옮길 때 쓰는 **내려받기만** 남았다.

    올리기·목록 읽기는 쓰지 않는다(악보는 서버에 바로 올린다). 그 길로 요청이 가면 바로 실패한다.
    """
    def handle(route):
        url = route.request.url
        if 'alt=media' in url:
            downloads.append(url)
            route.fulfill(status=200, content_type='application/pdf', body=FAKE_PDF)
        else:
            raise AssertionError('드라이브에 쓸 일이 없는데 호출했어요: ' + url)
    return handle


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
    settings = Settings(db_path=db_path, sheets_dir=str(pathlib.Path(db_path).parent / 'sheets'),
                        google_client_id='test.apps.googleusercontent.com',
                        google_picker_key='picker-key', google_picker_app_id='123456789',
                        admin_emails=[ADMIN], public_origin=f'http://127.0.0.1:{port}')
    app = create_app(settings, verify_token=fake_verifier, gemini_call=gem)
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning'))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    return server, f'http://127.0.0.1:{port}'


def new_page(browser, cred, errors, downloads=None):
    ctx = browser.new_context(viewport={'width': 390, 'height': 844}, accept_downloads=True)
    ctx.add_init_script(f'window.__cred = {json.dumps(cred)};')
    pg = ctx.new_page()
    pg.on('pageerror', lambda e: errors.append(str(e)))
    pg.route('**/accounts.google.com/**', lambda r: r.fulfill(status=200, content_type='text/javascript', body=FAKE_GSI))
    pg.route('**/www.googleapis.com/**', drive_route(downloads if downloads is not None else []))
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
    errors, downloads = [], []
    sheet_pdf = pathlib.Path(tmp) / 'upload.pdf'
    sheet_pdf.write_bytes(FAKE_PDF)

    with sync_playwright() as p:
        browser = p.chromium.launch()

        # ---------- 미등록 계정 ----------
        ctx, pg = new_page(browser, f'ok:{STRANGER}', errors)
        pg.goto(base + '/')
        pg.wait_for_selector('#fakeGsi')
        pg.click('#fakeGsi')
        pg.wait_for_function("document.querySelector('#loginMsg').textContent.includes('등록되지 않은 계정')")
        assert pg.is_hidden('#app')
        ctx.close()

        # ---------- 관리자 ----------
        ctx, pg = new_page(browser, f'ok:{ADMIN}', errors, downloads)
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

        # ---------- 악보: 드라이브에서 한 번만 가져오기 ----------
        # drive_index에 들어 있는 것을 브라우저가 받아 서버로 옮긴다(한시적 기능).
        seeded = pg.evaluate("""async (files) => {
            const r = await fetch('/api/drive/index', {method: 'PUT',
              headers: {'Content-Type': 'application/json'}, body: JSON.stringify({files})});
            return (await r.json()).count; }""", DRIVE_FILES)
        assert seeded == 3, seeded
        pg.reload()
        pg.wait_for_selector('#app:not([hidden])')
        pg.click('.tabs button[data-tab=admin]')
        pg.wait_for_function("document.querySelector('#sheetState').textContent === '3개 남음'",
                             timeout=15000)
        pg.click('#importDrive')
        pg.wait_for_function(
            "document.querySelector('#sheetMsg').textContent.includes('3개를 모두 서버로 옮겼어요')",
            timeout=30000)
        # 상태 뱃지는 가져오기가 끝난 뒤 다시 읽어서 바뀐다
        pg.wait_for_function("document.querySelector('#sheetState').textContent === '모두 옮겼어요'",
                             timeout=15000)
        assert len(downloads) == 3, downloads
        # 악보는 drive.file 토큰만 받는다. youtube 범위는 더 쓰지 않는다.
        assert pg.evaluate('window.__scopes') == ['https://www.googleapis.com/auth/drive.file']
        got = api(pg, '/api/sheets')['body']['items']
        assert sorted(s['title'] for s in got) == ['01_새 힘 얻으리_A', '기대 (G)', '나로부터']

        # ---------- 세트리스트 만들기 (유튜브 재생목록 없이) ----------
        pg.click('.tabs button[data-tab=setlist]')
        pg.fill('#input', SETLIST)
        pg.click('#parseBtn')
        assert pg.locator('#blocks > .card').count() == 4

        # 가져온 악보는 파일 이름이 제목이라('01_새 힘 얻으리_A') 곡 제목과 꼭 같지는 않다.
        # 그래서 처음에는 '비슷한 악보'로 걸리고, 한 번 고르면 별칭이 생겨 다음부터 자동이다.
        pg.wait_for_selector('#sh-1-0 .sh')
        assert '비슷한 악보 1개' in pg.inner_text('#sh-1-0')
        pg.click('#sh-1-0 .pick')
        pg.wait_for_selector('.modal .cand')
        assert '01_새 힘 얻으리_A' in pg.inner_text('.modal')
        pg.click('.modal .cand button')
        pg.wait_for_selector('#sh-1-0 .sh-n.ok')
        assert '01_새 힘 얻으리_A.pdf' in pg.inner_text('#sh-1-0')
        # 다시 파싱하면 이제 저절로 붙는다(별칭이 남았다)
        pg.click('#parseBtn')
        pg.wait_for_selector('#sh-1-0 .sh-n.ok')
        assert pg.locator('#sh-1-0 .pick').count() == 0

        # 메들리는 곡마다 한 줄이고 둘 다 저절로 붙는다('기대 (G)'는 괄호를 떼면 '기대'와 같은 키).
        assert pg.locator('#sh-2-1 .sh').count() == 2
        assert pg.locator('#sh-2-1 .sh-t').all_inner_texts() == ['나로부터', '기대']
        assert pg.locator('#sh-2-1 .sh-n.ok').count() == 2

        # 악보가 없는 곡은 서버로 바로 올린다(드라이브를 부르지 않는다)
        before = len(downloads)
        with pg.expect_file_chooser() as fc:
            pg.click('#sh-0-0 .up')
        fc.value.set_files(str(sheet_pdf))
        pg.wait_for_selector('#sh-0-0 .sh-n.ok')
        assert len(downloads) == before   # 드라이브 호출이 늘지 않았다

        # 맞는 악보가 없는 곡은 '올리기'만 보인다
        pg.wait_for_selector('#sh-0-1 .sh')
        assert '아직 올린 악보가 없어요' in pg.inner_text('#sh-0-1')             or '올린 악보가 없어요' in pg.inner_text('#sh-0-1')

        pg.locator('#blocks .create').nth(0).click()
        pg.wait_for_selector('#blocks .card >> nth=0 >> .result')
        link = pg.locator('#blocks .card').nth(0).locator('.link')
        share_url = link.input_value()
        assert '/s/' in share_url, share_url
        token = share_url.rsplit('/s/', 1)[1]
        pg.locator('#blocks .create').nth(1).click()
        pg.wait_for_selector('#blocks .card >> nth=1 >> .result')
        pg.wait_for_function("""async () => (await (await fetch('/api/setlists')).json()).items.length === 2""")

        saved = api(pg, '/api/setlists/' + token)
        assert saved['status'] == 200, saved['status']
        assert saved['body']['title'] == 'Setlist 261118'
        assert saved['body']['rawMessage'] == SETLIST.replace('\r', '')
        assert saved['body']['playlistId'] == '' and saved['body']['url'] == ''
        songs = saved['body']['songs']
        assert songs[0]['sheets'][0]['title'] == '내가 소망하는 아버지 나라'
        assert songs[2]['sheets'] == [] and songs[2]['needSheets'] == ['예수 인도하셨네']

        # ---------- 공유 페이지 (로그인 없이 누구나) ----------
        sctx = browser.new_context(viewport={'width': 390, 'height': 844}, accept_downloads=True)
        spg = sctx.new_page()
        spg.on('pageerror', lambda e: errors.append('share: ' + str(e)))
        spg.route('**/www.youtube.com/iframe_api', lambda r: r.fulfill(
            status=200, content_type='text/javascript', body=FAKE_YT))
        spg.route('**/i.ytimg.com/**', lambda r: r.abort())
        spg.goto(f'{base}/s/{token}')
        spg.wait_for_selector('#body:not([hidden])')
        assert spg.inner_text('#title') == 'Setlist 261118'
        assert spg.locator('#songs .song').count() == 3
        assert '내가 소망하는 아버지 나라' in spg.inner_text('#songs')
        # 로그인 화면이 아니다
        assert spg.locator('#gsiBtn').count() == 0

        # 전체 연속 재생: 재생목록을 만들지 않고 영상 ID 배열을 큐에 넣는다
        spg.click('#playAll')
        spg.wait_for_function("window.__cued && window.__cued.playlist.length === 3", timeout=15000)
        assert spg.evaluate('window.__cued.playlist')[0] == VIDEO
        assert spg.evaluate('window.__cued.index') == 0

        # 악보 받기 (로그인 없이)
        with spg.expect_download() as d:
            spg.locator('#songs .song').nth(0).locator('a.btn[href*="/sheet/"]').click()
        assert d.value.suggested_filename.endswith('.pdf')
        with spg.expect_download() as d:
            spg.click('a.btn[href*="sheets.zip"]')
        assert d.value.suggested_filename.endswith('.zip')

        # 남의 악보는 이 주소로 받을 수 없다
        other = api(spg, '/api/sheets')          # 로그인 없이는 목록도 못 본다
        assert other['status'] == 401
        assert api(spg, f'/api/share/{token}/sheet/99999')['status'] == 404
        assert api(spg, '/api/share/nope')['status'] == 404
        sctx.close()

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
            seeds.append({'title': f'Setlist {d:%y%m%d}',
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
        pg.wait_for_selector(f'#recList .hrow[data-key="{token}"]')
        assert pg.locator('#recList .hrow').count() == 1
        pg.click('#recList .hopen')
        pg.wait_for_selector('#recordView .song')
        # 기록 상세의 링크는 공유 주소다(재생목록이 아니다)
        assert pg.input_value('#recordView .link') == share_url
        assert pg.locator('#recordView .song').count() == 3

        # 상세의 악보: 붙은 곡은 받기, 없는 곡은 올리기. 머리에 '악보 전체 받기(zip)'
        assert '악보 전체 받기' in pg.inner_text('#recordView')
        assert pg.locator('#recordView .sh a.btn').count() >= 1
        # 악보가 없는 '지금 우리가'와 '예수 인도하셨네'
        assert pg.locator('#recordView .sh .rup').count() == 2
        with pg.expect_download() as d:
            pg.click('#recordView a.btn[href^="/api/sheets/zip/"]')
        assert d.value.suggested_filename.endswith('.zip'), d.value.suggested_filename
        with zipfile.ZipFile(io.BytesIO(pathlib.Path(d.value.path()).read_bytes())) as zf:
            assert '내가 소망하는 아버지 나라.pdf' in zf.namelist(), zf.namelist()
        pg.click('#recordView .close')

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
        pg.wait_for_selector('#recList .hrow[data-kind=setlist]')
        gone = pg.locator('#recList .hrow[data-kind=setlist]').first.get_attribute('data-key')
        pg.locator('#recList .hrow[data-kind=setlist] .hopen').first.click()
        pg.wait_for_selector('#recordView .del')
        pg.click('#recordView .del')
        pg.click('#recordView .del')
        pg.wait_for_function("k => !document.querySelector(`#recList .hrow[data-key=\"${k}\"]`)", arg=gone)
        assert api(pg, '/api/setlists/' + gone)['status'] == 404
        admin_ctx, admin_pg = ctx, pg

        # ---------- 팀원 ----------
        ctx, pg = new_page(browser, f'ok:{MEMBER}', errors)
        login(pg, base)
        assert '홍길동 · 팀원' == pg.inner_text('#whoami')
        assert pg.is_visible('#tabs') and pg.is_hidden('#tab-admin')
        assert pg.locator('.tabs button:visible').all_inner_texts() == ['세트리스트', '쇼츠 기획', '작업 기록']
        # 세트리스트는 팀원도 쓴다(구글 연동이 없어졌다). 설정·팀원은 그대로 관리자 전용.
        assert api(pg, '/api/setlists')['status'] == 200
        for path in ('/api/settings', '/api/members', '/api/drive/todo'):
            assert api(pg, path)['status'] == 403, path

        # 팀원도 작업 기록을 본다. 세트리스트 기록도 열 수 있지만(그 주 찬양 확인) 지우지는 못한다.
        pg.click('.tabs button[data-tab=records]')
        pg.wait_for_selector('#recList .hrow')
        assert '세트리스트 12 · 쇼츠 기획 1' in pg.inner_text('#recCount')
        pg.fill('#recQ', '261118')
        pg.click('#recSearch')
        pg.wait_for_selector('#recList .hrow[data-kind=setlist]')
        pg.locator('#recList .hrow[data-kind=setlist] .hopen').first.click()
        pg.wait_for_selector('#recordView .song')
        assert pg.locator('#recordView .song').count() == 3
        assert pg.locator('#recordView .del').count() == 0

        # 팀원도 악보를 받고, 없는 곡은 서버로 바로 올린다.
        assert pg.locator('#recordView .sh a.btn').count() >= 1
        with pg.expect_file_chooser() as fc:
            pg.locator('#recordView .sh .rup').first.click()
        fc.value.set_files(str(sheet_pdf))
        pg.wait_for_function(
            "() => document.querySelectorAll('#recordView .sh .rup').length === 0", timeout=15000)
        pg.click('#recordView .close')
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
