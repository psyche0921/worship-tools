"""서버 설정. 모든 값은 환경변수(deploy/.env)에서 읽는다."""
import os
from dataclasses import dataclass, field


def _emails(raw: str) -> list[str]:
    return [e.strip().lower() for e in raw.split(',') if e.strip()]


@dataclass
class Settings:
    db_path: str = '/data/worship.db'
    sheets_dir: str = '/data/sheets'
    google_client_id: str = ''
    google_picker_key: str = ''
    # drive.file 범위로 Picker를 쓰려면 Cloud 프로젝트 번호(appId)가 필요하다.
    # 이것이 없으면 고른 폴더가 이 앱에 허락되지 않는다.
    google_picker_app_id: str = ''
    max_sheet_mb: int = 20
    admin_emails: list[str] = field(default_factory=list)
    public_origin: str = 'https://bbc.ppsoft.kr'
    default_model: str = 'gemini-3.8-flash'
    gemini_api_key: str = ''
    youtube_api_key: str = ''
    daily_plan_limit: int = 20
    session_days: int = 30

    @property
    def cookie_secure(self) -> bool:
        return self.public_origin.startswith('https://')

    @classmethod
    def from_env(cls) -> 'Settings':
        env = os.environ.get
        return cls(
            db_path=env('DB_PATH', cls.db_path),
            sheets_dir=env('SHEETS_DIR', cls.sheets_dir),
            google_client_id=env('GOOGLE_CLIENT_ID', ''),
            google_picker_key=env('GOOGLE_PICKER_KEY', ''),
            google_picker_app_id=env('GOOGLE_PICKER_APP_ID', ''),
            max_sheet_mb=int(env('MAX_SHEET_MB', cls.max_sheet_mb)),
            admin_emails=_emails(env('ADMIN_EMAILS', '')),
            public_origin=env('PUBLIC_ORIGIN', cls.public_origin).rstrip('/'),
            default_model=env('DEFAULT_MODEL', cls.default_model),
            gemini_api_key=env('GEMINI_API_KEY', ''),
            youtube_api_key=env('YOUTUBE_API_KEY', ''),
            daily_plan_limit=int(env('DAILY_PLAN_LIMIT', cls.daily_plan_limit)),
            session_days=int(env('SESSION_DAYS', cls.session_days)),
        )
