"""Gemini 호출과 프롬프트 구성. 문구는 v1(index.html)의 것을 그대로 옮겼다."""
import json
import os
import re
from typing import Any, Awaitable, Callable
from urllib.parse import quote

import httpx

GEMINI_BASE = os.environ.get('GEMINI_BASE_URL', 'https://generativelanguage.googleapis.com').rstrip('/')

# (model, body) -> 응답 텍스트. 테스트에서는 app.state.gemini 를 가짜로 바꾼다.
GeminiCall = Callable[[str, dict], Awaitable[str]]


class GeminiError(Exception):
    pass


def make_client(api_key: str) -> GeminiCall:
    async def call(model: str, body: dict) -> str:
        if not api_key:
            raise GeminiError('서버에 Gemini API 키가 설정되지 않았어요. 관리자에게 알려주세요.')
        url = f"{GEMINI_BASE}/v1beta/models/{quote(model, safe='')}:generateContent"
        headers = {'Content-Type': 'application/json', 'x-goog-api-key': api_key}
        async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=20)) as client:
            for attempt in range(2):
                try:
                    res = await client.post(url, headers=headers, json=body)
                except httpx.TimeoutException:
                    raise GeminiError('Gemini 응답이 너무 오래 걸려요. 잠시 후 다시 시도하세요.')
                except httpx.HTTPError as e:
                    raise GeminiError(f'Gemini에 연결하지 못했어요. ({type(e).__name__})')
                # 일시적인 서버 오류는 한 번만 다시 시도한다.
                if res.status_code >= 500 and attempt == 0:
                    continue
                break
        return extract_text(res.status_code, _json(res))
    return call


def _json(res: httpx.Response) -> dict:
    try:
        return res.json()
    except ValueError:
        return {}


def extract_text(status: int, data: dict) -> str:
    if status >= 400:
        msg = (data.get('error') or {}).get('message') or f'HTTP {status}'
        if status == 429:
            raise GeminiError(f'요청 한도를 넘었어요. 잠시 후 다시 시도하세요. ({msg})')
        raise GeminiError(msg)
    cands = data.get('candidates') or []
    c = cands[0] if cands else {}
    parts = (c.get('content') or {}).get('parts') or []
    text = ''.join(p.get('text', '') for p in parts if p.get('text') and not p.get('thought'))
    if not text:
        why = (data.get('promptFeedback') or {}).get('blockReason') or c.get('finishReason') or '알 수 없음'
        raise GeminiError(f'응답이 비어 있어요. ({why})')
    return text


# ---------- 프롬프트 ----------

def fmt_time(sec: int) -> str:
    sec = max(0, round(sec))
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m}:{s:02d}'


def strip_frontmatter(skill: str) -> str:
    return re.sub(r'^﻿?---\r?\n[\s\S]*?\r?\n---\r?\n', '', skill).strip()


def video_part(video_id: str, start: int | None = None, end: int | None = None) -> dict:
    part: dict[str, Any] = {'file_data': {'file_uri': 'https://www.youtube.com/watch?v=' + video_id}}
    if start is not None or end is not None:
        meta = {}
        if start is not None:
            meta['start_offset'] = f'{start}s'
        if end is not None:
            meta['end_offset'] = f'{end}s'
        part['video_metadata'] = meta
    return part


DETECT_PROMPT = (
    '이 영상은 교회 주일 예배 라이브입니다. 설교(말씀 선포)가 시작되는 시각과 끝나는 시각을 찾아주세요. '
    '설교 전 성경 봉독은 포함하고, 설교 후 기도·찬양·광고는 제외합니다. '
    'JSON으로만 답하세요: {"start":"h:mm:ss","end":"h:mm:ss","title":"설교 제목(알 수 있으면)"}'
)


def detect_body(video_id: str) -> dict:
    return {
        'contents': [{'role': 'user', 'parts': [video_part(video_id), {'text': DETECT_PROMPT}]}],
        'generationConfig': {'responseMimeType': 'application/json', 'mediaResolution': 'MEDIA_RESOLUTION_LOW'},
    }


def parse_hms(s: str) -> int | None:
    s = re.sub(r'[^\d:]', '', str(s or ''))
    if not s:
        return None
    parts = s.split(':')
    if len(parts) > 3 or not all(parts):
        return None
    total = 0
    for p in parts:
        total = total * 60 + int(p)
    return total


def parse_detect(text: str) -> dict:
    raw = re.sub(r'^```(?:json)?|```$', '', text.strip()).strip()
    try:
        j = json.loads(raw)
    except ValueError:
        raise GeminiError('설교 구간을 찾지 못했어요. 직접 입력해 주세요.')
    start, end = parse_hms(j.get('start')), parse_hms(j.get('end'))
    if start is None or end is None or end <= start:
        raise GeminiError('설교 구간을 찾지 못했어요. 직접 입력해 주세요.')
    return {'start': start, 'end': end, 'title': str(j.get('title') or '').strip()}


def plan_body(skill: str, video_id: str, start: int | None, end: int | None, extra: str) -> dict:
    if start is not None or end is not None:
        a = fmt_time(start) if start is not None else '처음'
        b = fmt_time(end) if end is not None else '끝'
        rng = f'첨부 영상은 주일 예배 라이브 영상에서 설교 구간(원본 기준 {a} ~ {b})만 잘라낸 것입니다.'
    else:
        rng = '첨부 영상은 주일 예배 라이브 영상입니다.'
    lines = [
        rng,
        '스킬 지침에 따라 이 설교로 쇼츠를 기획해 주세요.',
        '모든 타임스탬프는 원본 라이브 영상 기준 절대 시각(h:mm:ss)으로 적어 주세요.'
        + (f' 잘라낸 영상의 0:00은 원본의 {fmt_time(start)}입니다.' if start else ''),
        f'추가 요청: {extra}' if extra else '',
    ]
    prompt = '\n'.join(l for l in lines if l)
    return {
        'system_instruction': {'parts': [{'text': strip_frontmatter(skill)}]},
        'contents': [{'role': 'user', 'parts': [video_part(video_id, start, end), {'text': prompt}]}],
        'generationConfig': {'mediaResolution': 'MEDIA_RESOLUTION_LOW'},
    }
