"""작업 기록 보기: 세트리스트와 쇼츠 기획을 한곳에서 보는 읽기 전용 API.

팀원도 모두 쓸 수 있다(세트리스트를 *만드는* 것은 여전히 관리자 전용이고, 여기서는 보기만 한다).
"그 기록이 어느 주일 예배의 것인가"는 제목의 6자리 날짜로 정한다(`Setlist 261115`,
`261115 쇼츠 기획 · ...`). 날짜가 없으면 세트리스트는 돌아오는 일요일, 쇼츠는 지난 일요일로 본다.
"""
import json
import re
import sqlite3
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import current_user, get_db
from ..jobs import KST
from .admin import setlist_item

router = APIRouter(prefix='/api/records')

PAGE_SIZE = 10
MAX_TOKENS = 6
MAX_MATCHES = 30  # '검색된 이유'에 보여 줄 기록 수
MAX_WHERE = 3     # 한 조각이 여러 칸에서 맞았을 때 보여 줄 칸 수
YYMMDD = re.compile(r'(?<!\d)(\d{6})(?!\d)')
DATEISH = re.compile(r'^[\d.\-/]{4,10}$')
WEEKDAY_KO = '월화수목금토일'


# ---------- 날짜 ----------

def _kst_date(at_ms: int) -> date:
    return datetime.fromtimestamp(at_ms / 1000, KST).date()


def _sunday_before(d: date) -> date:
    return d - timedelta(days=(d.weekday() + 1) % 7)


def _sunday_after(d: date) -> date:
    return d + timedelta(days=(6 - d.weekday()) % 7)


def _from_yymmdd(s: str) -> date | None:
    try:
        return date(2000 + int(s[:2]), int(s[2:4]), int(s[4:6]))
    except ValueError:
        return None


def service_date(kind: str, title: str, at_ms: int) -> date:
    """그 기록이 가리키는 예배 날짜."""
    m = YYMMDD.search(title or '')
    if m:
        d = _from_yymmdd(m.group(1))
        if d:
            return d
    created = _kst_date(at_ms)
    return _sunday_after(created) if kind == 'setlist' else _sunday_before(created)


def _dates(kind: str, title: str, at_ms: int) -> dict:
    d = service_date(kind, title, at_ms)
    w = _sunday_before(d)
    return {
        'date': d.isoformat(),
        'week': w.isoformat(),
        'dateLabel': f'{d.year % 100:02d}.{d.month:02d}.{d.day:02d}({WEEKDAY_KO[d.weekday()]})',
        'weekLabel': f'{w.year}년 {w.month}월 {w.day}일 주일',
    }


# ---------- 검색 ----------

def _tokens(q: str) -> list[dict]:
    """검색어를 공백으로 나눈다. 숫자로 된 조각(261115, 2026-11)은 날짜로도 맞춰 본다."""
    out = []
    for t in (q or '').lower().split()[:MAX_TOKENS]:
        digits = re.sub(r'\D', '', t)
        out.append({'t': t, 'digits': digits if DATEISH.match(t) and len(digits) >= 4 else ''})
    return out


def _snippet(text: str, token: str, width: int = 44) -> str:
    """맞은 자리 앞뒤를 잘라서 '왜 걸렸는지' 보여 줄 짧은 문구를 만든다."""
    text = ' '.join(str(text).split())
    i = text.lower().find(token)
    if i < 0:
        return text[:width * 2]
    a, b = max(0, i - width // 2), min(len(text), i + len(token) + width)
    return ('…' if a else '') + text[a:b] + ('…' if b < len(text) else '')


def _match_where(toks: list[dict], fields: list[tuple[str, str]], big: list[tuple[str, bool]],
                 dates: dict) -> list[dict] | None:
    """모든 조각이 맞아야 한다. 맞았으면 조각마다 '어느 칸에서' 맞았는지 돌려준다.

    fields는 바로 볼 수 있는 짧은 칸, big은 SQL로 포함 여부만 본 큰 칸(본문·대본)이다.
    한 조각이 여러 칸에서 맞으면 모두 적는다(제목에도 있고 대본에도 있으면 둘 다).
    """
    keys = [dates['date'].replace('-', ''), dates['week'].replace('-', '')]
    where = []
    for i, tok in enumerate(toks):
        found = []
        for label, value in fields:
            if value and tok['t'] in value.lower():
                found.append({'field': label, 'token': tok['t'], 'text': _snippet(value, tok['t'])})
        for label, hits in big:
            if hits[i]:
                found.append({'field': label, 'token': tok['t'], 'text': ''})
        if tok['digits'] and any(tok['digits'] in k for k in keys):
            found.append({'field': '날짜', 'token': tok['t'], 'text': dates['dateLabel']})
        if not found:
            return None  # 조각 하나라도 못 찾으면 이 기록은 아니다
        where += found[:MAX_WHERE]
    return where


def _select(base: str, text_exprs: list[str], toks: list[dict]) -> tuple[str, list]:
    """본문처럼 큰 칸은 가져오지 않고, 검색어가 들어 있는지만 SQL에서 칸마다 확인한다."""
    cols, params = '', []
    for j, expr in enumerate(text_exprs):
        for i, tok in enumerate(toks):
            cols += f', instr(lower({expr}), ?) AS kw{j}_{i}'
            params.append(tok['t'])
    return base.replace(' FROM ', cols + ' FROM ', 1), params


# ---------- 목록 ----------

SETLIST_SQL = 'SELECT playlist_id, title, url, ok, total, songs_json, created_at FROM setlists'
SHORTS_SQL = ('SELECT s.id, s.video_id, s.start_sec, s.end_sec, s.title, s.sermon_title, s.extra, s.created_by, '
              "s.created_at, COALESCE(t.text, '') <> '' AS has_transcript, u.name AS by_name "
              'FROM shorts s LEFT JOIN users u ON u.email = s.created_by '
              'LEFT JOIN transcripts t ON t.video_id = s.video_id')


def _setlists(db: sqlite3.Connection, toks: list[dict], user: dict) -> list[dict]:
    # 원본 메시지(raw_message)는 한 메시지에 여러 세트리스트가 들어 있어서 검색에서 뺀다.
    out = []
    for r in db.execute(SETLIST_SQL):
        songs = json.loads(r['songs_json'])
        names = [s.get('name') or '' for s in songs]
        notes = [n for s in songs for n in (s.get('notes') or [])]
        dates = _dates('setlist', r['title'], r['created_at'])
        out.append({'kind': 'setlist', 'key': r['playlist_id'], 'title': r['title'], 'url': r['url'],
                    'at': r['created_at'], 'ok': r['ok'], 'total': r['total'],
                    'songs': [s for s in names if s][:30],
                    'canDelete': user['role'] == 'admin', **dates,
                    '_where': _match_where(toks, [('제목', r['title']), ('곡 제목', ' / '.join(names)),
                                                  ('곡 메모', ' / '.join(notes))], [], dates)})
    return out


def _shorts(db: sqlite3.Connection, toks: list[dict], user: dict) -> list[dict]:
    # 쇼츠 본문과 설교 대본은 커서 SQL로 포함 여부만 본다(주변 문구는 걸린 기록만 따로 읽는다).
    sql, params = _select(SHORTS_SQL, ['s.text', "COALESCE(t.text, '')"], toks)
    out = []
    for r in db.execute(sql, params):
        dates = _dates('shorts', r['title'], r['created_at'])
        topic = r['sermon_title'] or (r['title'].split('·', 1)[1].strip() if '·' in r['title'] else '')
        big = [('쇼츠 본문', [bool(r[f'kw0_{i}']) for i in range(len(toks))]),
               ('설교 대본', [bool(r[f'kw1_{i}']) for i in range(len(toks))])]
        out.append({'kind': 'shorts', 'key': str(r['id']), 'id': r['video_id'], 'title': r['title'],
                    'at': r['created_at'], 'start': r['start_sec'], 'end': r['end_sec'], 'topic': topic,
                    'by': r['created_by'], 'byName': r['by_name'] or r['created_by'].split('@')[0],
                    'hasTranscript': bool(r['has_transcript']),
                    'canDelete': user['role'] == 'admin' or r['created_by'] == user['email'], **dates,
                    '_where': _match_where(toks, [('제목', r['title']), ('설교 주제', topic),
                                                  ('추가 요청', r['extra'])], big, dates)})
    return out


BIG_FIELDS = {'쇼츠 본문': 'text', '설교 대본': 'transcript'}


def fill_snippets(db: sqlite3.Connection, items: list[dict]) -> None:
    """본문·대본에서 걸린 기록만 그 글을 읽어서 주변 문구를 채운다."""
    need = [it for it in items
            if it['kind'] == 'shorts' and any(w['field'] in BIG_FIELDS and not w['text'] for w in it['_where'])]
    if not need:
        return
    ids = [int(it['key']) for it in need]
    marks = ','.join('?' * len(ids))
    texts = {r['id']: r for r in db.execute(
        f"SELECT s.id, s.text, COALESCE(t.text, '') AS transcript FROM shorts s "
        f'LEFT JOIN transcripts t ON t.video_id = s.video_id WHERE s.id IN ({marks})', ids)}
    for it in need:
        row = texts.get(int(it['key']))
        for w in it['_where']:
            if row is not None and w['field'] in BIG_FIELDS and not w['text']:
                w['text'] = _snippet(row[BIG_FIELDS[w['field']]], w['token'])


@router.get('')
def list_records(kind: str = Query('all', pattern='^(all|setlist|shorts)$'), q: str = Query('', max_length=200),
                 page: int = Query(1, ge=1), size: int = Query(PAGE_SIZE, ge=1, le=50),
                 user: dict = Depends(current_user), db: sqlite3.Connection = Depends(get_db)):
    """언제나 주일 예배 기준으로 묶어서 돌려준다(페이지도 주 단위).

    검색은 '찬양 제목으로 찾으면 그 주의 쇼츠 기획도 같이' 보이게, 맞은 기록이 있는 주를 통째로 돌려준다.
    """
    toks = _tokens(q)
    items: list[dict] = []
    if kind in ('all', 'setlist'):
        items += _setlists(db, toks, user)
    if kind in ('all', 'shorts'):
        items += _shorts(db, toks, user)
    matches = []
    if toks:
        hit = [it for it in items if it['_where'] is not None]
        weeks = {it['week'] for it in hit}
        items = [it for it in items if it['week'] in weeks]
        fill_snippets(db, hit)
        hit.sort(key=lambda it: (it['date'], it['at']), reverse=True)
        # 왜 걸렸는지: 맞은 기록과 그 자리를 따로 모아서 돌려준다(같은 주라서 함께 나온 기록은 빼고).
        matches = [{'kind': it['kind'], 'key': it['key'], 'title': it['title'], 'week': it['week'],
                    'dateLabel': it['dateLabel'], 'where': it['_where']} for it in hit[:MAX_MATCHES]]
    for it in items:
        del it['_where']
    items.sort(key=lambda it: (it['date'], it['at']), reverse=True)

    groups: dict[str, dict] = {}
    for it in items:
        g = groups.setdefault(it['week'], {'week': it['week'], 'label': it['weekLabel'], 'items': []})
        g['items'].append(it)
    for g in groups.values():
        # 한 주 안에서는 세트리스트 먼저, 그다음 쇼츠 기획
        g['items'].sort(key=lambda it: (it['kind'] != 'setlist', -it['at']))

    rows = list(groups.values())
    pages = max(1, -(-len(rows) // size))
    page = min(page, pages)
    return {'page': page, 'pages': pages, 'total': len(items), 'weeks': len(rows),
            'counts': {'setlist': sum(1 for it in items if it['kind'] == 'setlist'),
                       'shorts': sum(1 for it in items if it['kind'] == 'shorts')},
            'matched': len(matches), 'matches': matches,
            'groups': rows[(page - 1) * size: page * size]}


# ---------- 상세 ----------

@router.get('/setlist/{key}')
def record_setlist(key: str, user: dict = Depends(current_user), db: sqlite3.Connection = Depends(get_db)):
    """세트리스트 상세. 만드는 것은 관리자만 하지만, 보는 것은 팀원도 할 수 있다.

    쇼츠 상세는 이미 모두가 쓸 수 있는 GET /api/shorts/{id}를 그대로 쓴다.
    """
    row = db.execute('SELECT * FROM setlists WHERE playlist_id = ?', (key,)).fetchone()
    if not row:
        raise HTTPException(404, '기록을 찾을 수 없어요.')
    item = setlist_item(row, with_raw=user['role'] == 'admin')
    item['canDelete'] = user['role'] == 'admin'
    return item | _dates('setlist', row['title'], row['created_at'])
