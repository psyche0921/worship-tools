"""교회 유튜브 채널에서 그 주일 예배 라이브 영상 찾기.

두 가지 길을 쓴다. 공식 API(YOUTUBE_API_KEY)가 있으면 먼저 쓰고, 없거나 실패하면
채널의 '실시간' 탭 페이지를 읽는다(키가 필요 없지만 유튜브가 페이지 구조를 바꾸면 깨진다).

영상은 **제목에 든 날짜**로 고른다. 이 교회 영상 제목이 "... 2026.10.18 주일예배 ..." 모양이라
지난 주일 날짜와 맞는 것을 정확히 집을 수 있다. 날짜가 맞는 게 없으면 '아직 안 올라왔다'로 본다.
"""
import json
import re
from datetime import date
from typing import Awaitable, Callable

import httpx

# url -> 응답 본문. 테스트에서는 app.state.yt_fetch 를 가짜로 바꾼다.
Fetch = Callable[[str], Awaitable[str]]

API = 'https://www.googleapis.com/youtube/v3/playlistItems'
CHANNEL_ID = re.compile(r'^UC[\w-]{20,}$')
HANDLE = re.compile(r'^@?[\w.\-]{3,60}$')
EXTERNAL_ID = re.compile(r'"externalId":"(UC[\w-]+)"')
INITIAL_DATA = re.compile(r'var ytInitialData = (\{.*?\});</script>', re.S)


class YoutubeError(Exception):
    pass


def make_fetch() -> Fetch:
    async def fetch(url: str) -> str:
        headers = {'Accept-Language': 'ko', 'User-Agent': 'Mozilla/5.0 (worship-tools)'}
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10), follow_redirects=True) as client:
            try:
                res = await client.get(url, headers=headers)
            except httpx.HTTPError as e:
                raise YoutubeError(f'유튜브에 연결하지 못했어요. ({type(e).__name__})')
        if res.status_code >= 400:
            raise YoutubeError(f'유튜브가 응답하지 않아요. (HTTP {res.status_code})')
        return res.text
    return fetch


# ---------- 임시 재생목록(휴대폰에서 유튜브 앱으로 연속 재생) ----------

WATCH_VIDEOS = 'https://www.youtube.com/watch_videos?video_ids='
TEMP_LIST = re.compile(r'[?&]list=(TLGG[\w-]+)')

# 영상 ID 목록 -> 임시 재생목록 ID. 테스트에서는 app.state.yt_temp_list 를 가짜로 바꾼다.
TempList = Callable[[list[str]], Awaitable[str]]


def temp_list_id(location: str) -> str:
    """`watch_videos`가 보낸 Location 헤더에서 임시 재생목록 ID(TLGG…)를 꺼낸다."""
    m = TEMP_LIST.search(location or '')
    if not m:
        raise YoutubeError('유튜브가 임시 재생목록을 만들어 주지 않았어요.')
    return m.group(1)


def make_temp_list() -> TempList:
    """영상 ID 목록 → 유튜브가 만들어 주는 임시 재생목록 ID(TLGG…).

    `watch_videos?video_ids=…`는 303으로 `watch?v=…&list=TLGG…`로 보내 준다. 그 ID로
    만든 주소는 **평범한 재생목록 주소**라서 휴대폰에서 누르면 유튜브 앱이 열린다(앱 링크).
    앱에서 들으면 화면을 꺼도 계속 재생된다(프리미엄). 로그인도 API 키도 필요 없다.
    """
    async def temp_list(ids: list[str]) -> str:
        headers = {'Accept-Language': 'ko', 'User-Agent': 'Mozilla/5.0 (worship-tools)'}
        async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=10),
                                     follow_redirects=False) as client:
            try:
                res = await client.get(WATCH_VIDEOS + ','.join(ids), headers=headers)
            except httpx.HTTPError as e:
                raise YoutubeError(f'유튜브에 연결하지 못했어요. ({type(e).__name__})')
        if res.status_code >= 400:
            raise YoutubeError(f'유튜브가 응답하지 않아요. (HTTP {res.status_code})')
        return temp_list_id(res.headers.get('location', ''))
    return temp_list


def channel_url(channel: str, tab: str = '') -> str:
    """설정에 적은 채널(주소·핸들·UC아이디) → 채널 페이지 주소."""
    c = (channel or '').strip().rstrip('/')
    if CHANNEL_ID.match(c):
        url = f'https://www.youtube.com/channel/{c}'
    elif c.startswith('http'):
        url = re.sub(r'/(videos|streams|featured|playlists|community|about)$', '', c)
    elif HANDLE.match(c):
        url = 'https://www.youtube.com/@' + c.lstrip('@')
    else:
        raise YoutubeError('채널 주소를 확인하세요. (예: https://www.youtube.com/@bridgechurch_Daejeon)')
    return url + (f'/{tab}' if tab else '')


async def resolve_channel_id(fetch: Fetch, channel: str) -> str:
    c = (channel or '').strip().rstrip('/')
    if CHANNEL_ID.match(c):
        return c
    m = re.search(r'/channel/(UC[\w-]+)', c) or EXTERNAL_ID.search(await fetch(channel_url(c)))
    if not m:
        raise YoutubeError('채널을 찾지 못했어요. 채널 주소를 확인하세요.')
    return m.group(1)


def date_re(d: date) -> re.Pattern:
    """제목 속 날짜 표기를 넉넉히 받는다: 2026.10.18 / 2026-10-18 / 26.10.18 / 2026년 10월 18일 / 261018."""
    y4, y2, m, dd = d.year, d.year % 100, d.month, d.day
    sep = r'\s*[.\-/년]?\s*'
    return re.compile(rf'(?<!\d)(?:{y4}|{y2:02d}){sep}0?{m}{sep}(?:월)?\s*0?{dd}(?!\d)')


def parse_channel_page(html: str) -> list[dict]:
    """채널 페이지의 영상 목록(새 글 먼저)."""
    m = INITIAL_DATA.search(html)
    if not m:
        raise YoutubeError('채널 페이지를 읽지 못했어요. 잠시 후 다시 시도할게요.')
    try:
        data = json.loads(m.group(1))
    except ValueError:
        raise YoutubeError('채널 페이지를 읽지 못했어요. 잠시 후 다시 시도할게요.')
    out, seen = [], set()

    def walk(o):
        if isinstance(o, dict):
            lk = o.get('lockupViewModel')
            if isinstance(lk, dict) and lk.get('contentId') and lk.get('contentId') not in seen:
                seen.add(lk['contentId'])
                title = (((lk.get('metadata') or {}).get('lockupMetadataViewModel') or {})
                         .get('title') or {}).get('content') or ''
                out.append({'videoId': lk['contentId'], 'title': title})
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    return out


def parse_api(body: str) -> list[dict]:
    try:
        data = json.loads(body)
    except ValueError:
        raise YoutubeError('유튜브 응답을 읽지 못했어요.')
    if data.get('error'):
        raise YoutubeError('유튜브 API가 거절했어요: ' + str((data['error'] or {}).get('message') or ''))
    out = []
    for it in data.get('items') or []:
        sn = it.get('snippet') or {}
        vid = ((sn.get('resourceId') or {}).get('videoId'))
        if vid:
            out.append({'videoId': vid, 'title': sn.get('title') or '', 'publishedAt': sn.get('publishedAt') or ''})
    return out


async def recent_videos(fetch: Fetch, channel: str, api_key: str = '') -> list[dict]:
    """최근 영상 목록. API 키가 있으면 API로, 없거나 실패하면 채널 '실시간' 탭으로."""
    if api_key:
        try:
            cid = await resolve_channel_id(fetch, channel)
            uploads = 'UU' + cid[2:]
            body = await fetch(f'{API}?part=snippet&maxResults=15&playlistId={uploads}&key={api_key}')
            items = parse_api(body)
            if items:
                return items
        except YoutubeError:
            pass  # 키가 잘못됐거나 막혔으면 채널 페이지로
    return parse_channel_page(await fetch(channel_url(channel, 'streams')))


async def find_service_video(fetch: Fetch, channel: str, target: date, api_key: str = '') -> dict | None:
    """제목에 그 주일 날짜가 든 영상. 없으면 None(아직 안 올라옴)."""
    pat = date_re(target)
    for v in await recent_videos(fetch, channel, api_key):
        if pat.search(v.get('title') or ''):
            return v
    return None
