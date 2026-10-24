"""설치용 파일(아이콘·manifest·서비스 워커)이 로그인 없이 제대로 나가는지 본다.

안드로이드 크롬의 '앱 설치'는 manifest 와 fetch 를 처리하는 서비스 워커가 있어야 뜬다.
"""
import json


def test_manifest(client):
    r = client.get('/manifest.webmanifest')
    assert r.status_code == 200
    assert r.headers['content-type'].startswith('application/manifest+json')
    m = json.loads(r.content)
    assert m['start_url'] == '/' and m['display'] == 'standalone'
    sizes = {i['sizes'] for i in m['icons']}
    assert {'192x192', '512x512'} <= sizes          # 크롬이 요구하는 크기
    assert any(i['purpose'] == 'maskable' for i in m['icons'])


def test_service_worker(client):
    r = client.get('/sw.js')
    assert r.status_code == 200
    assert 'javascript' in r.headers['content-type']
    body = r.text
    assert "addEventListener('fetch'" in body       # 없으면 설치가 안 뜬다
    assert "startsWith('/api/')" in body            # API 응답은 캐시하지 않는다


def test_icons(client):
    for path, ctype in [('/icon.svg', 'image/svg+xml'), ('/icon-192.png', 'image/png'),
                        ('/icon-512.png', 'image/png'), ('/icon-maskable-512.png', 'image/png'),
                        ('/apple-touch-icon.png', 'image/png'), ('/favicon.ico', '')]:
        r = client.get(path)
        assert r.status_code == 200, path
        assert len(r.content) > 500, path
        if ctype:
            assert r.headers['content-type'].startswith(ctype), path


def test_page_links_icons(client):
    html = client.get('/').text
    for tag in ['rel="manifest"', 'href="/icon.svg"', 'rel="apple-touch-icon"',
                'name="theme-color"']:
        assert tag in html, tag


def test_html_is_never_reused_from_browser_cache(client):
    """배포한 화면이 바로 보이게: HTML과 서비스 워커는 no-cache."""
    assert client.get('/').headers['cache-control'] == 'no-cache'
    assert client.get('/sw.js').headers['cache-control'] == 'no-cache'
    assert 'no-cache' not in client.get('/icon.svg').headers.get('cache-control', '')
