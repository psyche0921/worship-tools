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


def _matches(toks: list[dict], hay: str, dates: dict, text_hits: list[bool]) -> bool:
    """모든 조각이 맞아야 한다(제목·내용 어디든, 또는 날짜)."""
    hay = hay.lower()
    keys = [dates['date'].replace('-', ''), dates['week'].replace('-', '')]
    for i, tok in enumerate(toks):
        if tok['t'] in hay or text_hits[i]:
            continue
        if tok['digits'] and any(tok['digits'] in k for k in keys):
            continue
        return False
    return True


def _select(base: str, text_expr: str, toks: list[dict]) -> tuple[str, list]:
    """본문처럼 큰 칸은 가져오지 않고, 검색어가 들어 있는지만 SQL에서 확인한다."""
    cols = ''.join(f', instr(lower({text_expr}), ?) AS kw{i}' for i in range(len(toks)))
    return base.replace(' FROM ', cols + ' FROM ', 1), [t['t'] for t in toks]


# ---------- 목록 ----------

SETLIST_SQL = 'SELECT playlist_id, title, url, ok, total, songs_json, created_at FROM setlists'
SHORTS_SQL = ('SELECT s.id, s.video_id, s.start_sec, s.end_sec, s.title, s.sermon_title, s.created_by, '
              "s.created_at, s.transcript <> '' AS has_transcript, u.name AS by_name "
              'FROM shorts s LEFT JOIN users u ON u.email = s.created_by')


def _setlists(db: sqlite3.Connection, toks: list[dict], user: dict) -> list[dict]:
    # 원본 메시지(raw_message)는 한 메시지에 여러 세트리스트가 들어 있어서 검색에서 뺀다.
    sql, params = _select(SETLIST_SQL, "title || ' ' || songs_json", toks)
    out = []
    for r in db.execute(sql, params):
        songs = [s.get('name') or '' for s in json.loads(r['songs_json'])]
        dates = _dates('setlist', r['title'], r['created_at'])
        hits = [bool(r[f'kw{i}']) for i in range(len(toks))]
        out.append({'kind': 'setlist', 'key': r['playlist_id'], 'title': r['title'], 'url': r['url'],
                    'at': r['created_at'], 'ok': r['ok'], 'total': r['total'],
                    'songs': [s for s in songs if s][:30],
                    'canDelete': user['role'] == 'admin', **dates,
                    '_hit': _matches(toks, r['title'] + ' ' + ' '.join(songs), dates, hits)})
    return out


def _shorts(db: sqlite3.Connection, toks: list[dict], user: dict) -> list[dict]:
    text_expr = "s.title || ' ' || s.sermon_title || ' ' || s.extra || ' ' || s.text || ' ' || s.transcript"
    sql, params = _select(SHORTS_SQL, text_expr, toks)
    out = []
    for r in db.execute(sql, params):
        dates = _dates('shorts', r['title'], r['created_at'])
        topic = r['sermon_title'] or (r['title'].split('·', 1)[1].strip() if '·' in r['title'] else '')
        hits = [bool(r[f'kw{i}']) for i in range(len(toks))]
        out.append({'kind': 'shorts', 'key': str(r['id']), 'id': r['video_id'], 'title': r['title'],
                    'at': r['created_at'], 'start': r['start_sec'], 'end': r['end_sec'], 'topic': topic,
                    'by': r['created_by'], 'byName': r['by_name'] or r['created_by'].split('@')[0],
                    'hasTranscript': bool(r['has_transcript']),
                    'canDelete': user['role'] == 'admin' or r['created_by'] == user['email'], **dates,
                    '_hit': _matches(toks, r['title'] + ' ' + topic, dates, hits)})
    return out


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
    if toks:
        weeks = {it['week'] for it in items if it['_hit']}
        items = [it for it in items if it['week'] in weeks]
    for it in items:
        del it['_hit']
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
