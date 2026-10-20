"""매주 월요일 오후 1시(KST) 자동 쇼츠 기획.

교회 채널에서 지난 주일 예배 라이브 영상을 찾아, 설교 구간 찾기 → 쇼츠 기획 → 설교 대본까지
사람이 누르는 것과 같은 길(작업 큐)로 돌린다. 영상이 아직 안 올라왔으면 저녁 6시까지 30분마다
다시 본다. 결과는 settings의 'auto_last'에 남겨서 설정 화면에서 볼 수 있다.
"""
import asyncio
import json
import logging
import secrets
from datetime import datetime, timedelta

from fastapi import FastAPI

from . import youtube
from .db import connect, get_setting, now_ms, set_setting
from .jobs import KST

log = logging.getLogger('worship.auto')

AUTO_EMAIL = 'auto@worship.local'   # 로그인할 수 없는 주소(기록에 '자동'으로 보인다)
AUTO_NAME = '자동'
RUN_WEEKDAY = 0      # 월요일
RUN_HOUR = 13        # 오후 1시
RETRY_UNTIL = 18     # 못 찾으면 저녁 6시까지 다시 본다
RETRY_MINUTES = 30
STALE_MINUTES = 20   # 이보다 오래 'running'이면 멈춘 것으로 본다
TICK_SECONDS = 300


def seed_auto_user(conn) -> None:
    with conn:
        conn.execute("INSERT INTO users (email, name, role, created_at) VALUES (?, ?, 'member', ?) "
                     'ON CONFLICT(email) DO NOTHING', (AUTO_EMAIL, AUTO_NAME, now_ms()))


def last_run(conn) -> dict:
    try:
        return json.loads(get_setting(conn, 'auto_last') or '{}')
    except ValueError:
        return {}


def _save(conn, status: str, message: str, **extra) -> dict:
    """status: done(기획함) / skip(이미 있음) / none(영상 없음) / error / off"""
    out = {'at': now_ms(), 'status': status, 'message': message, **extra}
    with conn:
        set_setting(conn, 'auto_last', json.dumps(out, ensure_ascii=False), AUTO_EMAIL)
    return out


def clear_running(conn) -> None:
    """서버가 재시작되면 돌고 있던 자동 실행은 끊긴 것이다."""
    if last_run(conn).get('status') == 'running':
        _save(conn, 'error', '서버가 재시작되어 중단됐어요. 다시 시도해 주세요.',
              week=datetime.now(KST).date().isoformat())


def due(now: datetime, last: dict) -> bool:
    """지금 돌려야 하나. 월요일 13~18시 사이, 이번 주에 아직 끝내지 않았으면."""
    if now.weekday() != RUN_WEEKDAY or not (RUN_HOUR <= now.hour < RETRY_UNTIL):
        return False
    if last.get('status') == 'running' and now.timestamp() * 1000 - int(last.get('at') or 0) < STALE_MINUTES * 60_000:
        return False
    if last.get('week') == now.date().isoformat():
        if last.get('status') in ('done', 'skip', 'off'):
            return False
        # 영상이 아직 없거나 실패했으면 조금 있다 다시
        if now.timestamp() * 1000 - int(last.get('at') or 0) < RETRY_MINUTES * 60_000:
            return False
    return True


class AutoRunner:
    def __init__(self, app: FastAPI):
        self.app = app
        self.task: asyncio.Task | None = None
        self.running = False

    def start_now(self, conn) -> dict:
        """'지금 한 번 실행'. 2~4분 걸려서 백그라운드로 돌리고 상태만 바로 돌려준다.

        진행 상황은 settings의 auto_last('running')에 남고, 화면은 그걸 확인한다.
        """
        if self.running or last_run(conn).get('status') == 'running':
            return last_run(conn) or {'status': 'running', 'message': '이미 돌고 있어요.'}
        out = _save(conn, 'running', '지난 주일 영상을 찾는 중이에요…', week=self._week())
        self.app.state.jobs._spawn(self._run_bg())
        return out

    async def _run_bg(self) -> None:
        conn = connect(self.app.state.settings.db_path)
        try:
            await self.run_once(conn, force=True)
        finally:
            conn.close()

    def start(self) -> None:
        self.task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        if self.task:
            self.task.cancel()

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(TICK_SECONDS)
                conn = connect(self.app.state.settings.db_path)
                try:
                    if due(datetime.now(KST), last_run(conn)):
                        await self.run_once(conn)
                finally:
                    conn.close()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception('자동 실행기 오류')

    async def run_once(self, conn, force: bool = False) -> dict:
        """한 번 돌린다. force는 설정 화면의 '지금 한 번 실행'."""
        if self.running:
            return {'status': 'error', 'message': '이미 돌고 있어요.'}
        self.running = True
        try:
            return await self._run(conn, force)
        except youtube.YoutubeError as e:
            return _save(conn, 'error', str(e), week=self._week(), )
        except Exception as e:
            log.exception('자동 쇼츠 기획 실패')
            return _save(conn, 'error', f'알 수 없는 오류가 났어요. ({type(e).__name__})', week=self._week())
        finally:
            self.running = False

    @staticmethod
    def _week() -> str:
        return datetime.now(KST).date().isoformat()

    async def _run(self, conn, force: bool) -> dict:
        settings = self.app.state.settings
        week = self._week()
        if not force and (get_setting(conn, 'auto_enabled') or '') != '1':
            return _save(conn, 'off', '자동 기획이 꺼져 있어요.', week=week)
        channel = (get_setting(conn, 'yt_channel') or '').strip()
        if not channel:
            return _save(conn, 'error', '설정에 교회 유튜브 채널 주소가 없어요.', week=week)
        if not (get_setting(conn, 'skill_md') or '').strip():
            return _save(conn, 'error', '설정에 SKILL.md가 없어요.', week=week)

        # 지난 주일(월요일에 돌면 어제). 일요일에 손으로 돌리면 그날.
        today = datetime.now(KST).date()
        target = today - timedelta(days=(today.weekday() + 1) % 7)

        video = await youtube.find_service_video(self.app.state.yt_fetch, channel, target,
                                                 settings.youtube_api_key)
        if not video:
            return _save(conn, 'none', f'{target:%Y-%m-%d} 주일 영상이 아직 안 올라왔어요.', week=week)

        vid = video['videoId']
        done = conn.execute('SELECT id FROM shorts WHERE video_id = ? ORDER BY id DESC', (vid,)).fetchone()
        if done and not force:
            return _save(conn, 'skip', '이미 기획한 영상이에요.', week=week, videoId=vid, shortsId=done['id'])

        seed_auto_user(conn)
        detect = await self._job(conn, 'detect', {'videoId': vid})
        if detect['status'] != 'done':
            return _save(conn, 'error', '설교 구간을 찾지 못했어요: ' + (detect['error'] or ''),
                         week=week, videoId=vid)
        found = json.loads(detect['result_json'] or '{}')

        plan = await self._job(conn, 'plan', {'videoId': vid, 'start': found.get('start'),
                                              'end': found.get('end'), 'extra': '',
                                              'sermonTitle': (found.get('title') or '').strip()})
        if plan['status'] != 'done':
            return _save(conn, 'error', '쇼츠 기획에 실패했어요: ' + (plan['error'] or ''), week=week, videoId=vid)
        return _save(conn, 'done', f"{target:%Y-%m-%d} 주일 · {video.get('title', '')[:60]}",
                     week=week, videoId=vid, shortsId=plan['shorts_id'])

    async def _job(self, conn, kind: str, inp: dict) -> dict:
        """사람이 누를 때와 같은 작업 큐로 돌리고 끝날 때까지 기다린다."""
        job_id = secrets.token_urlsafe(12)
        with conn:
            conn.execute(
                "INSERT INTO jobs (id, kind, status, input_json, created_by, created_at) VALUES (?, ?, 'queued', ?, ?, ?)",
                (job_id, kind, json.dumps(inp, ensure_ascii=False), AUTO_EMAIL, now_ms()))
        await self.app.state.jobs.run_now(job_id)
        return dict(conn.execute('SELECT * FROM jobs WHERE id = ?', (job_id,)).fetchone())
