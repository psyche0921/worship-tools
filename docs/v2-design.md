# v2 설계: bbc.ppsoft.kr (자체 서버 + DB)

`docs/handoff.md`의 v2 목표를 170번 서버 기준으로 구체화한 문서입니다. 2026-10-19 사용자와 정한 내용을 반영했습니다.

## 1. 확정된 결정

| 항목 | 결정 |
|---|---|
| 주소 | `https://bbc.ppsoft.kr` (DNS는 이미 서버 공인 IP `14.50.236.172`를 가리킴) |
| 서버 | 170번 서버(`192.168.0.170`, CentOS Stream 10, 계정 `tstech`). 다른 서비스와 함께 쓰는 서버 |
| 사용자 | 관리자 1명(사용자 본인) + 예배팀원 |
| 로그인 | Google 로그인 + 관리자가 등록한 이메일만 허용 |
| 역할 | **관리자**: 모든 기능. **팀원**: 쇼츠 기획만 |
| 쇼츠 기록 | 팀 공용. 모두가 모든 기록을 보고, 삭제는 본인 것만(관리자는 전체) |
| 세트리스트 | 관리자만. 재생목록은 관리자 YouTube 계정에 만들고 링크만 공유 |
| Gemini 키 | 서버 환경변수로 옮김. 브라우저에는 키가 없음 |
| DB | SQLite (소규모, 별도 DB 서버 불필요) |

### 역할별 권한

| 기능 | 관리자 | 팀원 |
|---|---|---|
| 세트리스트 → 재생목록, 세트리스트 기록 | ✅ | ❌ (메뉴 없음, API도 403) |
| 쇼츠 기획 생성, 설교 구간 찾기, PDF 공유 | ✅ | ✅ |
| 쇼츠 기록 보기 | ✅ 전체 | ✅ 전체 |
| 쇼츠 기록 삭제 | ✅ 전체 | 본인이 만든 것만 |
| 설정 (SKILL.md, 모델) | ✅ | ❌ |
| 팀원 관리 (허용 이메일) | ✅ | ❌ |
| v1 기록 가져오기 | ✅ | ❌ |

**권한은 서버에서 확인합니다.** 화면에서 메뉴를 숨기는 것은 편의일 뿐이고, 모든 API가 세션의 역할을 직접 검사합니다.

## 2. 서버 현황 (2026-10-19 확인)

- 자원: 16코어, RAM 31GB, `/` 여유 28GB, `/home` 여유 17GB(사용률 90%). 이 앱은 데이터가 작아서 문제없지만 `/home`은 여유가 적다는 점을 기억해 둡니다.
- 이미 돌고 있는 것: nginx 1.26(서브도메인별 `conf.d/*.conf` + certbot), Docker 29 + Compose v5(drdr), pm2(pedal), uvicorn(gp5, tone), MariaDB.
- `tstech`는 `docker` 그룹이라 sudo 없이 컨테이너를 올릴 수 있습니다. **sudo는 비밀번호가 필요**해서 nginx 설정과 인증서 발급은 사용자가 직접 실행합니다.
- SELinux Enforcing, `httpd_can_network_connect` on(nginx → 로컬 포트 프록시 가능). firewalld 사용 중.
- 사용 중인 로컬 포트: 3000, 3100, 3306, 8000, 8020, 8810, 9090 등. **이 앱은 `127.0.0.1:8830`** 을 씁니다(확인 시점에 비어 있음).

## 3. 전체 구조

```
브라우저 (폰/PC)
   │  https://bbc.ppsoft.kr
   ▼
nginx (TLS, certbot) ── /etc/nginx/conf.d/bbc.ppsoft.kr.conf
   │  proxy_pass http://127.0.0.1:8830
   ▼
Docker 컨테이너 worship-web (FastAPI + uvicorn, 워커 1개)
   ├─ 정적 파일: web/index.html, vendor/, fonts/
   ├─ /api/*  : 로그인, 쇼츠, 세트리스트 기록, 설정, 팀원
   ├─ 작업 실행기: Gemini 호출을 백그라운드로 실행
   └─ SQLite: /data/worship.db  (호스트 ~/worship-tools/data 에 마운트)
         │
         └─▶ Gemini API (서버 키)

YouTube Data API는 지금처럼 관리자 브라우저에서 직접 호출합니다(서버를 거치지 않음).
```

### 기술 선택과 이유

- **FastAPI + uvicorn (Python 3.12)**: 서버에 이미 같은 방식의 앱들이 있고, Gemini 호출과 Google ID 토큰 검증에 쓸 라이브러리가 충분합니다.
- **Docker Compose**: sudo 없이 배포하고, 재부팅 뒤 자동으로 다시 뜨게 할 수 있습니다(`restart: unless-stopped`). 같은 서버의 다른 앱과 Python 환경이 섞이지 않습니다.
- **워커 1개**: SQLite 쓰기 경합이 없고, 작업 실행기를 같은 프로세스 안에 둘 수 있습니다. 예배팀 규모에서는 충분합니다.
- **빌드 단계 없는 프론트엔드 유지**: v1처럼 HTML 파일 하나에 두고, 파서·PDF·`cleanText` 코드는 그대로 씁니다. 바뀌는 것은 저장소(`localStorage` → API)와 로그인, 역할별 화면입니다.

## 4. 로그인과 세션

1. 로그인하지 않은 사용자에게는 로그인 화면만 보여 줍니다. "Google로 로그인" 버튼은 Google Identity Services의 ID 토큰 방식입니다(범위는 `openid email profile`뿐).
2. 브라우저가 받은 ID 토큰을 `POST /api/auth/google`로 보냅니다.
3. 서버는 `google-auth`로 토큰 서명, `aud`(OAuth 클라이언트 ID), `email_verified`를 검증합니다. 그다음 `users` 표에 있는 이메일인지 확인합니다. 없으면 "등록되지 않은 계정" 안내와 함께 403을 돌려줍니다.
4. 통과하면 서버 세션을 만들고 쿠키를 보냅니다: `HttpOnly; Secure; SameSite=Lax; Max-Age=30일`. 쿠키에는 임의 토큰만 넣고, DB에는 그 해시를 저장합니다.
5. 상태를 바꾸는 요청(POST/PUT/DELETE)은 `Origin` 헤더가 `https://bbc.ppsoft.kr`인지 확인합니다(CSRF 방지).

- **관리자 지정**: 서버 환경변수 `ADMIN_EMAILS`. 시작할 때 이 이메일을 `role=admin`으로 넣습니다. 공개 저장소라 이메일은 코드에 넣지 않습니다.
- **YouTube 권한**은 별개입니다. 관리자가 재생목록을 만들 때만 v1처럼 토큰 클라이언트(`youtube` 범위)로 따로 받습니다. 팀원은 이 창을 볼 일이 없습니다.
- **OAuth 동의 화면 '테스트' 상태**: 테스트 상태에서는 테스트 사용자로 등록한 계정만 로그인할 수 있다고 보고, 팀원 이메일을 Google Cloud 콘솔의 테스트 사용자에도 추가합니다(최대 100명). 기본 범위만 쓰는 로그인도 이 제한을 받는지는 구현할 때 실제로 확인합니다.

## 5. 쇼츠 생성: 백그라운드 작업 + 폴링

쇼츠 기획은 1~2분 걸립니다. 폰 화면이 꺼지거나 브라우저가 백그라운드로 가면 오래 걸리는 요청은 끊기기 쉽습니다. 그래서 요청을 작업으로 바꿔 처리합니다.

1. `POST /api/jobs` `{kind: "plan", videoId, start, end, extra, sermonTitle}` → `{jobId}`를 바로 돌려줍니다.
2. 서버가 백그라운드에서 Gemini를 호출합니다. 성공하면 결과를 `shorts` 표에 저장하고 작업에 `shortsId`를 기록합니다.
3. 브라우저는 `GET /api/jobs/{id}`를 3초마다 확인합니다. 화면을 닫았다 열어도 작업 기록에 결과가 나타납니다.
4. "설교 구간 찾기"(`kind: "detect"`)도 같은 방식으로 처리합니다. 결과 `{start, end, title}`은 작업 결과에만 남기고 기록에는 넣지 않습니다.

- 서버가 재시작되면 `running` 상태였던 작업은 `error`("서버가 재시작되어 중단됨")로 바꿉니다.
- **사용량 제한**: 키가 서버에 하나뿐이라 사용자별로 하루 `plan` 작업 수를 제한합니다(기본 20회, 환경변수로 조정). 같은 사용자의 동시 작업은 1개까지만 허용합니다.
- 프롬프트 구성(설교 구간 안내, 절대 시각 지시, 추가 요청)은 v1 문구를 서버로 옮깁니다. SKILL.md는 DB 설정에서 읽고 frontmatter를 제거합니다.
- 서버는 Gemini 응답 원문을 그대로 저장합니다. `#t=` 제거, 타임스탬프 링크, 시작 시각 보정은 v1처럼 화면에 그릴 때 처리합니다(같은 원문을 나중에 다시 그릴 수 있도록).

## 6. DB 스키마 (SQLite, WAL 모드)

```sql
users    (email TEXT PK, name TEXT, role TEXT CHECK(role IN ('admin','member')),
          created_at INT, last_login_at INT)
sessions (token_hash TEXT PK, email TEXT REFERENCES users ON DELETE CASCADE,
          created_at INT, expires_at INT)
settings (key TEXT PK, value TEXT, updated_at INT, updated_by TEXT)
          -- 'skill_md', 'model'
shorts   (id INTEGER PK, video_id TEXT, start_sec INT, end_sec INT,
          title TEXT, sermon_title TEXT, extra TEXT, text TEXT, model TEXT,
          created_by TEXT, created_at INT, legacy_key TEXT UNIQUE)
setlists (id INTEGER PK, playlist_id TEXT UNIQUE, title TEXT, url TEXT,
          ok INT, total INT, songs_json TEXT, raw_message TEXT,
          created_by TEXT, created_at INT)
jobs     (id TEXT PK, kind TEXT, status TEXT, input_json TEXT, result_json TEXT,
          error TEXT, shorts_id INT, created_by TEXT, created_at INT, finished_at INT)
```

- 시각은 모두 밀리초 epoch입니다(v1의 `at`과 같음).
- `songs_json`은 v1 형식 그대로입니다: `[{id, name, notes[]}]`.
- `raw_message`: 세트리스트 원본 메시지입니다. 나중에 다시 파싱할 수 있게 저장합니다(v1에는 없던 항목).
- `legacy_key`: v1 기록을 가져올 때 쓰는 키(`s{at}`)입니다. 같은 파일을 두 번 가져와도 중복되지 않습니다.
- v1의 개수 제한(100/60)은 없애고, 목록 API에서 페이지를 나눠 돌려줍니다.

## 7. API

모든 응답은 JSON입니다. 로그인하지 않으면 401, 권한이 없으면 403을 돌려줍니다.

| 메서드 · 경로 | 권한 | 내용 |
|---|---|---|
| `GET /api/config` | 누구나 | `{clientId}`. 로그인 버튼을 그리는 데 필요 |
| `POST /api/auth/google` | 누구나 | `{credential}` → 세션 쿠키 |
| `POST /api/auth/logout` | 로그인 | 세션 삭제 |
| `GET /api/me` | 로그인 | `{email, name, role}` |
| `POST /api/jobs` | 로그인 | 쇼츠 기획 또는 설교 구간 찾기 작업 시작 |
| `GET /api/jobs/{id}` | 본인 작업 | 상태와 결과 |
| `GET /api/shorts?before=` | 로그인 | 목록(최신순, 20개씩). 원문 없이 메타만 |
| `GET /api/shorts/{id}` | 로그인 | 원문 포함 |
| `DELETE /api/shorts/{id}` | 만든 사람 · 관리자 | 삭제 |
| `GET /api/setlists`, `GET /api/setlists/{id}` | 관리자 | 목록 / 상세 |
| `POST /api/setlists` | 관리자 | 재생목록을 만든 뒤 결과 저장(원본 메시지 포함) |
| `DELETE /api/setlists/{id}` | 관리자 | 기록 삭제(YouTube 재생목록은 지우지 않음) |
| `GET/PUT /api/settings` | 관리자 | SKILL.md, 모델 |
| `GET/POST/DELETE /api/members` | 관리자 | 허용 이메일 관리(관리자 자신은 삭제 불가) |
| `POST /api/import` | 관리자 | v1 기록 JSON 가져오기 |

## 8. 화면 변경

- **처음 화면**: 로그인 전에는 로그인 카드만 보입니다. 등록되지 않은 계정이면 "관리자에게 이메일 등록을 요청하세요"라고 안내합니다.
- **탭**: 관리자에게는 `세트리스트 | 쇼츠 기획 | 설정`을, 팀원에게는 탭 없이 쇼츠 기획만 보여 줍니다.
- **설정**: OAuth 클라이언트 ID와 Gemini 키 입력칸은 없앱니다(서버 설정으로 옮김). 관리자 설정 탭에서는 SKILL.md 편집, 모델명, 팀원 이메일 추가·삭제, v1 기록 가져오기를 할 수 있습니다.
- **쇼츠 기록**: 각 항목에 만든 사람 이름을 붙이고, 삭제 버튼은 권한이 있을 때만 보여 줍니다.
- **세트리스트**: 파싱, 미리보기, 재생목록 생성은 그대로입니다. 생성이 끝나면 `POST /api/setlists`로 저장합니다.
- 그대로 두는 것: 파서 블록(`<script id="parser">`), `cleanText`, 타임스탬프 링크, 시작 시각 보정, jsPDF PDF와 공유창, 2단계 삭제 확인.
- `localStorage`에는 마지막 탭 같은 편의 정보만 남깁니다.

## 9. v1 기록 옮기기

`localStorage`는 출처(origin)마다 따로라서 `bbc.ppsoft.kr`에서는 `psyche0921.github.io`의 기록을 읽을 수 없습니다. 기기(폰, PC)마다 기록도 따로입니다. 그래서 다음 순서로 옮깁니다.

1. v1 페이지(GitHub Pages)에 **"기록 내보내기"** 버튼을 추가합니다. `hist.setlist`, `hist.shorts`, `shortsLast`, `skillText`를 JSON 파일 하나로 내려받거나 공유창으로 보냅니다.
2. 기록이 있는 기기마다 내보내기를 실행합니다.
3. v2 관리자 설정에서 **가져오기**로 파일을 올립니다. 중복은 키(`playlist_id`, `legacy_key`)로 건너뜁니다. 가져온 기록의 만든 사람은 관리자로 둡니다.
4. 내보낸 파일에 `skillText`가 있으면 SKILL.md 설정 값으로 쓸지 물어봅니다.

## 10. 저장소 구성

```
index.html            v1 그대로 유지(GitHub Pages). "기록 내보내기" 버튼만 추가
web/index.html        v2 화면. vendor/, fonts/는 루트의 것을 같이 씀
server/
  app/main.py         FastAPI 앱, 정적 파일, 라우터 연결
  app/db.py           SQLite 연결, 스키마 생성·마이그레이션
  app/auth.py         ID 토큰 검증, 세션, 권한 의존성
  app/jobs.py         백그라운드 작업 실행기
  app/gemini.py       Gemini 호출, 프롬프트 구성
  app/routes/*.py     shorts, setlists, settings, members, import
  requirements.txt
  Dockerfile
deploy/
  compose.yaml        127.0.0.1:8830:8000, ./data:/data, env_file .env
  bbc.ppsoft.kr.conf  nginx 설정 (설치 방법 주석 포함)
  deploy.sh           서버에서 git pull → docker compose up -d --build
  backup.sh           SQLite 백업 (cron)
  .env.example        GEMINI_API_KEY, GOOGLE_CLIENT_ID, ADMIN_EMAILS, ...
tests/
  e2e.py              v1 테스트 (그대로)
  test_api.py         pytest + FastAPI TestClient (Gemini, Google 토큰 검증은 가짜로)
  e2e_v2.py           Playwright: 관리자·팀원 화면, 작업 폴링, PDF
```

v2 화면을 `web/`에 따로 두는 이유: 루트 `index.html`을 v2로 바꾸면 GitHub Pages가 API 없이 뜨면서 깨집니다. 기록을 다 옮길 때까지 v1을 그대로 살려 둡니다.

## 11. 서버 설정 값 (`~/worship-tools/deploy/.env`, 권한 600, git에 넣지 않음)

| 변수 | 내용 |
|---|---|
| `GEMINI_API_KEY` | Gemini API 키 |
| `GOOGLE_CLIENT_ID` | OAuth 웹 클라이언트 ID(로그인과 YouTube 토큰에 같이 사용) |
| `ADMIN_EMAILS` | 관리자 이메일(쉼표로 구분) |
| `PUBLIC_ORIGIN` | `https://bbc.ppsoft.kr` (Origin 검사, 쿠키) |
| `DEFAULT_MODEL` | `gemini-3.8-flash` (설정 탭 값이 우선) |
| `DAILY_PLAN_LIMIT` | 사용자별 하루 쇼츠 기획 횟수, 기본 20 |

## 12. 배포 절차

### 최초 1회

**사용자가 직접 할 일 (sudo, Google Cloud 콘솔)**

1. nginx 설정 넣기. SELinux 라벨 때문에 `mv`가 아니라 `cp`를 씁니다.
   ```bash
   sudo cp ~/worship-tools/deploy/bbc.ppsoft.kr.conf /etc/nginx/conf.d/bbc.ppsoft.kr.conf
   sudo nginx -t && sudo systemctl reload nginx
   sudo certbot --nginx -d bbc.ppsoft.kr
   ```
2. Google Cloud 콘솔
   - OAuth 클라이언트 → 승인된 JavaScript 원본에 `https://bbc.ppsoft.kr` 추가
   - OAuth 동의 화면 → 테스트 사용자에 팀원 이메일 추가
   - Gemini API 키 → 웹사이트 제한을 없애고 **API 제한을 Generative Language API로만** 설정합니다. 서버에서 호출하면 리퍼러가 없어서 웹사이트 제한이 동작하지 않습니다. 필요하면 IP 제한(`14.50.236.172`)을 추가합니다.
   - (권장) v2용 Gemini 키를 새로 만들고, 브라우저에 노출됐던 v1 키는 기록을 옮긴 뒤 삭제합니다.

**Claude가 할 일 (tstech 권한, sudo 불필요)**

3. `git clone`으로 `~/worship-tools` 받기, `.env` 작성(키 값은 사용자가 직접 입력)
4. `docker compose -f deploy/compose.yaml up -d --build`
5. `curl http://127.0.0.1:8830/api/config`로 확인하고, 백업 cron 등록(`crontab -e`, 매일 새벽, 14일치 보관)

### 이후 배포

PC에서 `ssh tstech@192.168.0.170 '~/worship-tools/deploy/deploy.sh'`를 실행하면 `git pull` → 이미지 다시 빌드 → 컨테이너 교체 순서로 진행됩니다. DB는 볼륨에 있어서 그대로 남습니다.

## 13. 작업 순서

1. **서버 골격**: FastAPI, DB 스키마, 로그인·세션·역할, `/api/me`. pytest로 권한 검사
2. **쇼츠**: 작업 실행기, Gemini 호출, 쇼츠 기록 API, 사용량 제한
3. **세트리스트 기록, 설정, 팀원 API**
4. **화면(`web/index.html`)**: v1을 복사해서 로그인, 역할별 탭, API 저장소, 작업 폴링 적용. Playwright 테스트
5. **v1 내보내기 버튼 + v2 가져오기**
6. **배포**: Dockerfile, compose, nginx 설정, deploy/backup 스크립트 → 170번 서버에 배포
7. **실제 검증**: 사용자의 SKILL.md와 실제 라이브 영상으로 쇼츠 카드 분리와 PDF 레이아웃 확인, 팀원 계정 로그인 확인
8. **정리**: 기록 이전이 끝나면 GitHub Pages를 v2 안내 페이지로 바꾸거나 끕니다

## 14. 남은 확인 사항

- **SKILL.md**: 아직 받지 못했습니다. v2에서는 관리자 설정 화면에서 붙여넣어 DB에 저장합니다(공개 저장소에 넣지 않음).
- **OAuth 테스트 상태에서 팀원 로그인**: 기본 범위만 쓰는 로그인도 테스트 사용자 제한을 받는지 구현할 때 확인합니다. 제한을 받으면 팀원을 테스트 사용자로 추가하면 됩니다(소수 인원).
- **팀원 명단**: 배포 후 관리자 설정 화면에서 추가합니다.
