"""BBC Score(찬양팀 악보 필기 앱) 서버가 읽어 가는 길.

BBC Score는 따로 돌아가는 사이트입니다. 찬양팀원은 이 사이트가 아니라 BBC Score에
로그인하고, BBC Score 서버가 여기서 **찬양팀 명단**과 **세트리스트 목록**을 읽어 갑니다.
곡과 악보는 공개 공유 경로(`/api/share/{token}`, `.../sheet/{id}`)를 그대로 씁니다.

사람이 아니라 서버끼리 부르는 길이라 세션 대신 `X-Score-Key` 헤더(`SCORE_API_KEY`)로
막습니다. 키를 설정하지 않으면 이 경로는 없는 것처럼 404를 냅니다.
"""
import json
import secrets
import sqlite3

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from ..auth import get_db, get_settings
from ..config import Settings
from .records import _dates


def require_score_key(x_score_key: str = Header(''), settings: Settings = Depends(get_settings)):
    if not settings.score_api_key:
        raise HTTPException(404, 'Not Found')
    if not secrets.compare_digest(x_score_key.encode(), settings.score_api_key.encode()):
        raise HTTPException(401, '연동 키가 맞지 않아요.')


router = APIRouter(prefix='/api/integration', dependencies=[Depends(require_score_key)])


@router.get('/team/{email}')
def team_member(email: str, db: sqlite3.Connection = Depends(get_db)):
    """그 이메일이 찬양팀원인지. BBC Score가 로그인할 때와 가끔 다시 확인할 때 부른다."""
    row = db.execute('SELECT email, name FROM team_members WHERE email = ?',
                     (email.strip().lower(),)).fetchone()
    return {'member': row is not None, 'email': email.strip().lower(),
            'name': row['name'] if row else ''}


@router.get('/setlists')
def setlists(limit: int = Query(30, ge=1, le=200), db: sqlite3.Connection = Depends(get_db)):
    """최근 세트리스트 목록(새것부터). 곡·악보는 공유 경로에서 따로 받는다."""
    rows = db.execute('SELECT share_token, title, songs_json, created_at FROM setlists '
                      'ORDER BY created_at DESC LIMIT ?', (limit,)).fetchall()
    items = []
    for r in rows:
        songs = json.loads(r['songs_json'])
        items.append({'token': r['share_token'], 'title': r['title'], 'createdAt': r['created_at'],
                      'songCount': len(songs),
                      'songNames': [s.get('name') or '' for s in songs],
                      **_dates('setlist', r['title'], r['created_at'])})
    # 같은 주에 여러 개면 날짜 순서가 만든 순서와 다를 수 있다. 예배 날짜로 다시 줄 세운다.
    items.sort(key=lambda it: (it['date'], it['createdAt']), reverse=True)
    return {'items': items}
