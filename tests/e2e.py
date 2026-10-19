"""
worship-tools 브라우저 통합 테스트.

정적 서버를 띄우고, 외부 API(Google 로그인, YouTube Data API, Gemini)는 가짜 응답으로 바꾼 뒤
세트리스트 → 재생목록, 작업 기록, 쇼츠 기획, `#t=` 표기 제거, PDF 생성/공유를 확인합니다.

    pip install playwright pypdf
    python3 tests/e2e.py
"""
import functools
import http.server
import io
import json
import pathlib
import socketserver
import threading

from playwright.sync_api import sync_playwright
from pypdf import PdfReader

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"
SETLIST = (FIX / "setlist-sample.txt").read_text(encoding="utf-8")
SHORTS = (FIX / "shorts-response.md").read_text(encoding="utf-8")
VIDEO = "re4fR9uW0Po"

FAKE_GSI = """window.google={accounts:{oauth2:{initTokenClient:(o)=>{const c={callback:o.callback,
requestAccessToken(){setTimeout(()=>c.callback({access_token:'tok',expires_in:3600}),10)}};return c;}}}};"""


def serve():
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    handler = functools.partial(Quiet, directory=str(ROOT))
    srv = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    srv = serve()
    base = f"http://127.0.0.1:{srv.server_address[1]}/index.html"
    playlists = {"n": 0}

    def gemini(route):
        body = json.loads(route.request.post_data)
        if "responseMimeType" in json.dumps(body):
            txt = json.dumps({"start": "0:38:54", "end": "1:20:00", "title": "두려움을 이기는 믿음"}, ensure_ascii=False)
        else:
            assert body["contents"][0]["parts"][0]["video_metadata"] == {"start_offset": "2334s", "end_offset": "4800s"}
            txt = SHORTS
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"candidates": [{"content": {"parts": [{"text": txt}]}}]}, ensure_ascii=False))

    def youtube(route):
        if "playlists?" in route.request.url:
            body = json.loads(route.request.post_data)
            assert body["status"]["privacyStatus"] == "unlisted"
            playlists["n"] += 1
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"id": f"PLtest{playlists['n']}"}))
        else:
            route.fulfill(status=200, content_type="application/json", body="{}")

    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, accept_downloads=True)
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.route("**/accounts.google.com/**", lambda r: r.fulfill(status=200, content_type="text/javascript", body=FAKE_GSI))
        pg.route("**/generativelanguage.googleapis.com/**", gemini)
        pg.route("**/www.googleapis.com/youtube/**", youtube)
        pg.route("**/i.ytimg.com/**", lambda r: r.abort())
        pg.goto(base)

        # 설정
        if not pg.eval_on_selector("#settings", "e => e.open"):
            pg.click("#settings summary")
        pg.fill("#clientId", "x.apps.googleusercontent.com")
        pg.fill("#geminiKey", "AIzaTEST")
        pg.fill("#skillText", "---\nname: t\n---\n쇼츠 지침")
        pg.click("#saveSettings")

        # 세트리스트 파싱 → 재생목록 2개 생성
        pg.fill("#input", SETLIST)
        pg.click("#parseBtn")
        assert pg.locator("#blocks > .card").count() == 4
        titles = [pg.locator("#blocks .title").nth(i).input_value() for i in range(4)]
        assert titles[:3] == ["Setlist 261118", "Setlist 261011", "Setlist 260823"], titles
        pg.locator("#blocks .create").nth(0).click()
        pg.wait_for_selector("#blocks .card >> nth=0 >> .result")
        pg.locator("#blocks .create").nth(1).click()
        pg.wait_for_selector("#blocks .card >> nth=1 >> .result")
        assert pg.inner_text("#histCount-setlist") == "2"

        # 기록 열기 / 2단계 삭제
        pg.click("#tab-setlist details.hist summary")
        pg.locator("#hist-setlist .hopen").nth(1).click()
        assert pg.input_value("#setlistView .link").endswith("PLtest1")
        assert pg.locator("#setlistView .song").count() == 3
        pg.locator("#hist-setlist .hdel").nth(0).click()
        assert pg.inner_text("#histCount-setlist") == "2"
        pg.locator("#hist-setlist .hdel").nth(0).click()
        assert pg.inner_text("#histCount-setlist") == "1"

        # 쇼츠 기획
        pg.click(".tabs button[data-tab=shorts]")
        pg.fill("#liveUrl", f"https://www.youtube.com/live/{VIDEO}?si=abc")
        pg.click("#detectBtn")
        pg.wait_for_function("document.querySelector('#tStart').value === '38:54'")
        pg.click("#planBtn")
        pg.wait_for_selector("#shortsOut .md")
        out_html = pg.inner_html("#shortsOut")
        assert "#t=" not in out_html, "화면에 #t= 표기가 남아 있음"
        first = pg.locator("#shortsOut a.ts").first.get_attribute("href")
        assert first == f"https://youtu.be/{VIDEO}?t=2334", first
        assert "두려움을 이기는 믿음" in pg.inner_text("#shortsOut .vh-title")

        # PDF 저장
        with pg.expect_download() as d:
            pg.click("#pdfSave")
        dl = d.value
        assert dl.suggested_filename.startswith("쇼츠기획_") and dl.suggested_filename.endswith(".pdf")
        reader = PdfReader(io.BytesIO(pathlib.Path(dl.path()).read_bytes()))
        text = "\n".join(pg_.extract_text() for pg_ in reader.pages)
        assert "#t=" not in text, "PDF에 #t= 표기가 남아 있음"
        assert "쇼츠 1: 두려움 대신 믿음" in text
        uris = [a.get_object()["/A"]["/URI"] for page in reader.pages for a in page.get("/Annots", [])]
        assert f"https://youtu.be/{VIDEO}?t=2412" in uris, uris[:5]
        assert len(reader.pages) >= 2

        # PDF 공유 경로
        pg.evaluate("""() => { window.__shared = null; navigator.canShare = () => true;
            navigator.share = async d => { window.__shared = {name: d.files[0].name, type: d.files[0].type}; }; }""")
        pg.click("#pdfShare")
        pg.wait_for_function("window.__shared !== null")
        assert pg.evaluate("window.__shared.type") == "application/pdf"

        # 새로고침 후 기록 유지
        pg.reload()
        assert pg.inner_text("#histCount-setlist") == "1"
        assert pg.inner_text("#histCount-shorts") == "1"
        assert pg.locator("#shortsOut .md").count() >= 3

        browser.close()
    srv.shutdown()
    assert not errors, errors
    print("OK: 모든 테스트 통과")


if __name__ == "__main__":
    main()
