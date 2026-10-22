# 악보 관리 설계 (세트리스트 ↔ 구글 드라이브 ↔ drdr)

2026-10-22 사용자와 정한 내용입니다. 1~5단계를 구현했습니다(9장). drdr 연동만 남았습니다.

목표는 두 가지입니다.

1. **세트리스트에 악보를 붙인다.** 재생목록을 만들 때 곡마다 구글 드라이브의 악보를 찾아 연결하고, 없으면 그 자리에서 올립니다. 올린 악보는 드라이브에 남습니다. 팀원은 그 주 악보를 받습니다.
2. **그 악보를 drdr(채보 학습)의 자료로 넘긴다.** 세트리스트의 곡과 악보를 `drdr.ppsoft.kr`에 넘겨 리드시트 채보 학습 자료로 씁니다. 이번에는 설계만 정하고, 구현은 drdr 저장소 쪽 작업과 함께 합니다(7장).

## 1. 정한 것 (2026-10-22)

| 항목 | 결정 |
|---|---|
| 드라이브 접근 | **관리자 브라우저가 직접** 구글 드라이브 API를 호출합니다. 서버는 드라이브를 부르지 않고, 악보 **사본과 매핑만** 보관합니다 |
| 추가 OAuth 범위 | `https://www.googleapis.com/auth/drive.file` (민감 범위가 아니라 구글 검증이 필요 없음). 기존 `youtube` 토큰과 함께 받습니다 |
| 폴더 고르기 | Google Picker로 '악보' 폴더를 한 번 고르고, 폴더 ID를 설정 값으로 저장합니다 |
| 올린 악보 위치 | **악보 폴더 한 곳에 평평하게** 둡니다(곡 하나당 파일 하나). 주차 폴더는 만들지 않습니다 |
| 파일 이름 | 드라이브의 기존 악보 이름에는 규칙이 없습니다. 그래서 **느슨하게 찾아 후보를 보여 주고 사람이 고르는** 방식입니다. 한 번 고르면 그 곡은 다음부터 자동으로 붙습니다 |
| 악보 받기 | 서버 사본으로 내려줍니다. 드라이브 파일을 '링크가 있는 사람은 보기'로 공유하지 않습니다 |
| drdr 연동 | 이번에는 설계만. worship-tools 쪽 악보 관리를 먼저 끝냅니다 |

### 왜 서버가 드라이브를 대신 부르지 않는가

서버가 드라이브를 계속 읽으려면 refresh token을 보관해야 합니다. 그런데 이 앱의 OAuth 동의 화면은 **'테스트' 상태라 refresh token이 7일 뒤 만료**됩니다. 관리자가 매주 다시 동의해야 하고, `drive` 범위는 구글 검증 대상이기도 합니다. 반면 브라우저 토큰 방식(`initTokenClient`)은 세트리스트를 만들 때 이미 쓰고 있고, 테스트 상태에서도 제한이 없습니다.

대신 **자동 매핑(사람 없이 돌아가는 일)은 할 수 없습니다.** 세트리스트는 관리자가 손으로 만드는 작업이라 괜찮습니다. 자동 쇼츠 기획과 달리 악보는 월요일 1시에 혼자 돌 일이 없습니다.

## 2. 역할

| 기능 | 관리자 | 팀원 |
|---|---|---|
| 악보 폴더 고르기, 드라이브 목록 새로 읽기 | ✅ | ❌ |
| 세트리스트 곡에 악보 연결·바꾸기 | ✅ | ❌ |
| 악보 올리기 | ✅ (드라이브에 바로 올라감) | ✅ (서버에만 먼저 저장, 3-5장) |
| 악보 받기 (곡별, 주별 zip) | ✅ | ✅ |
| 매핑 삭제 | ✅ | ❌ |

팀원도 올릴 수 있게 하는 이유: 악보를 가진 사람이 꼭 관리자는 아닙니다. 팀원이 올린 파일은 서버에 `sync='pending'`으로 두고, 관리자가 다음에 접속하면 '드라이브에 올릴 악보 n개' 안내와 함께 한 번에 올립니다(브라우저가 올립니다). 권한은 늘 그랬듯 서버 의존성(`current_user`, `require_admin`)으로 막습니다.

## 3. 곡 제목과 악보 맞추기

### 3-1. 메들리는 곡마다 나눈다

세트리스트에는 한 줄에 두 곡이 붙어 있는 일이 흔합니다(`tests/fixtures/setlist-sample.txt`).

```
나로부터 & 기대
비전 + Holy Forever
마음의 예배 & 내 사람의 이유라
```

유튜브 링크는 하나지만 **악보는 곡마다 따로** 있습니다. 그래서 제목을 `&`, `+`, `/`로 쪼개 곡 하나당 악보 하나를 연결합니다. 한 곡(세트리스트의 한 줄)에 악보가 여러 개 붙을 수 있습니다.

### 3-2. 곡 키 정규화 (`songKey`)

파일 이름과 곡 제목을 같은 규칙으로 줄여서 비교합니다.

1. 유니코드 NFC 정규화 + 소문자
2. 괄호 안 제거: `(Live)`, `(G key)`, `[찬양]`
3. 확장자 제거(파일 이름일 때)
4. 공백과 `·-_.'",!?` 같은 기호 모두 제거

`새 힘 얻으리` → `새힘얻으리`, `새힘얻으리` → `새힘얻으리`. 한글 띄어쓰기 차이가 이렇게 흡수됩니다. `Holy Forever` → `holyforever`.

### 3-3. 후보 찾기

파일 이름에 규칙이 없으니(번호·작곡자·날짜가 섞임) 단계적으로 찾습니다.

1. **이미 매핑된 곡**(`sheets.song_key`, `sheet_aliases.alias_key`)이면 바로 연결합니다. 화면에 ✓.
2. 드라이브 목록 캐시(`drive_index`)에서 `name_key`에 `song_key`가 **들어 있는** 파일을 찾습니다. `01_새힘얻으리_A키.pdf`도 이렇게 걸립니다.
3. 그래도 없으면 `difflib.SequenceMatcher` 비율로 상위 5개를 후보로 보여 줍니다(표준 라이브러리만 씁니다).
4. 사람이 고르면 `sheets`에 매핑을 저장하고, 고른 파일의 `name_key`를 `sheet_aliases`에 함께 넣습니다. 다음에 그 파일 이름으로도 걸립니다.
5. 후보가 없으면 '악보 올리기'입니다.

## 4. DB 스키마

`server/app/db.py`의 `MIGRATIONS` 끝에 단계를 덧붙입니다(앞 단계는 고치지 않습니다).

```sql
CREATE TABLE sheets (
  id INTEGER PRIMARY KEY,
  song_key TEXT NOT NULL UNIQUE,        -- 정규화한 곡 제목(매칭 키)
  title TEXT NOT NULL,                  -- 사람이 읽는 곡 제목
  drive_file_id TEXT NOT NULL DEFAULT '',
  drive_name TEXT NOT NULL DEFAULT '',
  mime TEXT NOT NULL DEFAULT '',
  size INTEGER NOT NULL DEFAULT 0,
  file_path TEXT NOT NULL DEFAULT '',   -- 서버 사본 (/data/sheets/{id}{확장자})
  sync TEXT NOT NULL DEFAULT 'drive'
       CHECK (sync IN ('drive', 'pending', 'error')),
  sync_error TEXT,
  created_by TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE TABLE sheet_aliases (
  alias_key TEXT PRIMARY KEY,
  sheet_id INTEGER NOT NULL REFERENCES sheets(id) ON DELETE CASCADE
);
CREATE INDEX sheet_aliases_sheet ON sheet_aliases(sheet_id);

-- 악보 폴더의 파일 목록 캐시. 관리자 브라우저가 드라이브에서 읽어 서버에 넣는다.
CREATE TABLE drive_index (
  file_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  name_key TEXT NOT NULL,
  mime TEXT NOT NULL DEFAULT '',
  size INTEGER NOT NULL DEFAULT 0,
  modified_at INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL
);
CREATE INDEX drive_index_key ON drive_index(name_key);
```

- 설정 값(`settings` 표) 추가: `sheet_folder_id`, `sheet_folder_name`, `drive_index_at`(마지막으로 읽은 시각).
- 세트리스트와의 연결은 `setlists.songs_json`에 넣습니다. v1 형식 `[{id, name, notes[]}]`에 `sheets: [{sheetId, title}]`를 **덧붙이기만** 하므로 기존 기록도 그대로 읽힙니다. 매핑은 곡 키로도 다시 찾을 수 있지만, '그 주에 이 악보를 썼다'를 남기려고 기록에 함께 저장합니다.
- 악보 사본은 `/data/sheets/`에 둡니다(`deploy/compose.yaml`이 이미 `../data:/data`를 마운트합니다).
- `deploy/backup.sh`는 지금 SQLite만 백업합니다. 악보 원본은 드라이브에 있으니 서버 사본이 사라져도 다시 받을 수 있지만, **`sync='pending'`인 파일은 서버에만 있습니다.** 백업에 `/data/sheets`를 포함하거나(tar), pending을 빨리 드라이브로 올리는 쪽으로 처리합니다.

## 5. API

서버는 드라이브를 호출하지 않습니다. 브라우저가 드라이브 API를 부르고 **결과를 서버에 알려 줍니다.**

| 메서드 · 경로 | 권한 | 내용 |
|---|---|---|
| `POST /api/sheets/match` | 로그인 | `{names: ["나로부터 & 기대", ...]}` → 곡마다 `{name, songs: [{title, songKey, sheet, candidates[]}]}`. 메들리 분리와 후보 찾기를 서버가 한다 |
| `GET /api/sheets?q=` | 로그인 | 매핑 목록·검색 |
| `POST /api/sheets` | 로그인 | 악보 사본을 저장하고 곡에 연결(multipart: `title`, `file`, 선택 `driveFileId`·`driveName`). `driveFileId`가 함께 오면 `sync='drive'`(관리자만), 없으면 `sync='pending'`. 같은 곡에 이미 악보가 있으면 바꾼다 |
| `GET /api/sheets/{id}/file` | 로그인 | 서버 사본 내려받기(곡 제목을 파일 이름으로) |
| `GET /api/sheets/pending` | 관리자 | 드라이브에 아직 안 올린 악보 목록 |
| `POST /api/sheets/{id}/drive` | 관리자 | 브라우저가 드라이브에 올린 뒤 `{driveFileId, driveName}`를 알려 준다 → `sync='drive'` |
| `DELETE /api/sheets/{id}` | 관리자 | 매핑과 서버 사본 삭제(**드라이브 파일은 지우지 않는다**) |
| `PUT /api/drive/index` | 관리자 | 브라우저가 읽은 폴더 목록 전체를 캐시에 넣는다 `{files: [{id, name, mimeType, size, modifiedTime}]}` |
| `GET /api/sheets/zip/{재생목록 ID}` | 로그인 | 그 주 악보 묶음(`261118_악보.zip`) |
| `GET/PUT /api/settings` | 관리자 | `sheetFolderId`, `sheetFolderName`, `pickerKey`, `driveIndexAt`, `driveIndexCount`, `sheetPending`, `maxSheetMb` |

- 올릴 수 있는 형식은 앞머리 바이트로 가립니다(클라이언트가 말한 타입을 믿지 않음): PDF, PNG, JPEG. 한 파일 `MAX_SHEET_MB`(기본 20MB)까지.
- `POST /api/setlists`의 `songs_json`에 `sheets: [{sheetId, title}]`가 함께 들어옵니다.
- **기록을 읽을 때는** 저장된 `sheetId`가 있으면 그것을 쓰고, 없으면 곡 제목으로 지금 매핑을 찾습니다(`auto: true`). 그래서 **악보를 나중에 올리면 그 곡이 나왔던 지난 주 기록에도 바로 보입니다.** 아직 악보가 없는 곡은 `needSheets`로 알려 주고, 화면이 그 자리에 '올리기'를 그립니다.

## 6. 화면 (`web/index.html`)

### 세트리스트 탭

곡 카드 안에 악보 줄을 넣습니다. 메들리면 곡마다 한 줄입니다.

```
┌─ 나로부터 & 기대 ──────────────────────────────
│  (KEY - A | TIME - 4/4 | TEMPO - 123)
│  악보  나로부터          ✓ 나로부터_A.pdf   [받기] [바꾸기]
│  악보  기대              찾는 중… → 후보 3개  [고르기] [올리기]
└───────────────────────────────────────────────
```

- 메시지를 붙여넣어 미리보기를 그릴 때 `POST /api/sheets/match`를 한 번 부릅니다.
- `[고르기]`는 후보 목록(파일 이름, 수정 날짜)을 보여 주고, 고르면 브라우저가 드라이브에서 그 파일을 받아 서버에 사본을 올립니다.
- `[올리기]`는 파일 고르기 → 드라이브 악보 폴더에 `곡제목.pdf`로 올리고 → 서버에 사본과 매핑을 저장합니다.
- 드라이브에 같은 이름이 있으면 덮어쓰지 않고 `곡제목 (2).pdf`로 올린 뒤 어느 것을 쓸지 묻습니다.
- 재생목록을 만들면 그때 매핑이 기록에 함께 저장됩니다.

### 작업 기록 탭 (세트리스트 상세)

- 머리에 `악보 전체 받기 (zip)`.
- 곡 목록의 곡마다 악보 받기 링크. 악보가 없는 곡은 `악보 없음`으로 보이고, 팀원에게도 `올리기`가 보입니다(서버에만 저장).

### 설정 탭 (관리자)

- `악보 폴더`: `[드라이브에서 폴더 고르기]`(Picker) · 지금 폴더 이름 · `[목록 새로 읽기]`(파일 n개, 마지막 읽은 시각)
- `드라이브에 올릴 악보 n개` → `[지금 올리기]`

### 브라우저 쪽 드라이브 코드

- 토큰: 지금 `initTokenClient`의 `scope`에 `drive.file`을 더합니다 → `https://www.googleapis.com/auth/youtube https://www.googleapis.com/auth/drive.file`. 동의 창은 한 번만 뜹니다.
- 폴더 고르기: Google Picker(`https://apis.google.com/js/api.js` → `gapi.load('picker')`). **브라우저용 API 키(developer key)가 필요**해서 `GOOGLE_PICKER_KEY`를 `.env`에 넣고 `/api/config`로 내려줍니다(리퍼러를 `https://bbc.ppsoft.kr/*`로 제한).
- 목록 읽기: `GET /drive/v3/files?q='{folderId}' in parents and trashed=false&fields=files(id,name,mimeType,size,modifiedTime)&pageSize=1000` (페이지 넘기기 포함) → `PUT /api/drive/index`.
- 받기: `GET /drive/v3/files/{id}?alt=media` → Blob → 서버에 사본 업로드.
- 올리기: `POST /upload/drive/v3/files?uploadType=multipart`, `parents: [folderId]`.

## 7. drdr 연동 (설계만, 구현은 나중)

> **2026-10-22 다시 정함.** 처음에 "악보 인식(OMR)이 채보만큼 어렵다"고 적었는데 과장이었습니다. 찬양 리드시트는 OMR에서 쉬운 축이고, 악보 프로그램에서 만든 PDF는 OMR 없이도 코드·가사를 글자로 뽑을 수 있습니다. 진짜 병목은 OMR이 아니라 **악보 시간 ↔ 오디오 시간 정렬**입니다. 사람 감독 없이 학습 자료를 쌓는 전체 설계는 **`docs/drdr-training.md`** 에 따로 정리했습니다. 아래는 worship-tools 쪽이 맡는 '자료를 모아서 넘기는 일'만 남겨 둡니다.

### 1단계: 세트리스트를 drdr로 넘기기

```
worship-tools                                   drdr
  세트리스트 (곡 + 유튜브 링크 + 악보 + KEY/TEMPO)
        │  POST https://drdr.ppsoft.kr/api/ingest/worship
        │  헤더 X-Ingest-Token: (양쪽 .env의 공유 토큰)
        ▼
  {week: "2026-11-15",
   songs: [{title, youtubeUrl, videoId, key: "A", tempo: 106,
            timeSignature: "4/4", structure: "Intro(8) - V*4 - ...",
            sheetUrl, sheetName}]}
        │
        ▼                                   곡마다 채보 작업 만들기
                                            POST /api/transcribe 와 같은 길
                                            parts = melody,chords,lyrics
                                            + 작업 폴더에 참고 악보 저장
                                            + 편집 화면에 악보 나란히 보기
```

- `sheetUrl`은 worship-tools의 `GET /api/sheets/{id}/file`에 **짧은 수명의 서명 토큰**을 붙인 주소입니다(`?t=...`, 예: 10분). drdr 서버가 로그인 쿠키 없이 한 번 받아 갑니다.
- 세트리스트 메모의 `(KEY - A | TIME - 4/4 | TEMPO - 106)`을 그대로 넘깁니다. **조성·박자·BPM 정답을 공짜로 얻는 셈**이라 코드·박 채보 평가에 바로 쓸 수 있습니다. 곡 구성(`Intro(8) - V*4 - PreC - C`)도 구조 정답에 가깝습니다.
- 알아 둘 제약(drdr 쪽 현재 값): `max_duration_sec` 600(10분), `default_daily_limit` 3회/일, 허용 링크 도메인에 유튜브는 이미 들어 있습니다. 세트리스트를 한 번에 넣으면 한도에 걸리니 전용 계정과 한도를 따로 둡니다.

### 주일 라이브 영상도 함께 넘긴다

세트리스트 링크는 **원곡**이고, 곡 구성 메모(`Intro(8) - V*4 - ...`)는 **우리 팀 연주 계획**이라 원곡 영상과 맞지 않습니다. 그 메모가 정확히 맞는 연주는 쇼츠 기획이 이미 다루는 **주일 예배 라이브 영상**의 찬양 구간입니다. 그래서 `ingest`에 라이브 영상과 찬양 구간도 함께 넣습니다 — 찬양 구간을 찾는 일은 지금 설교 구간을 찾는 것과 같은 방식(`gemini.py`)입니다.

자세한 이유와 그다음 단계(악보에서 코드·가사·멜로디 뽑기, 가사 기반 섹션 정렬, 교차 검증으로 사람 없이 거르기)는 `docs/drdr-training.md`에 있습니다.

### 작업을 어디서 하나

`POST /api/ingest/worship`, 참고 악보 저장, 악보에서 정답지 뽑기, 정렬과 자동 승인은 모두 **drdr 저장소 작업**입니다. worship-tools 쪽은 자료를 모아 넘기는 일(악보 매핑, 라이브 찬양 구간 찾기, '보내기' 단추, 서명 토큰)뿐입니다.

## 8. 테스트

`tests/test_sheets.py` (pytest)

- `songKey` 정규화: 띄어쓰기 차이(`새 힘 얻으리` ↔ `새힘얻으리`), 괄호·확장자 제거, 영문 대소문자
- 메들리 분리: `나로부터 & 기대`, `비전 + Holy Forever` → 곡 2개
- 후보 찾기: 포함 검색이 먼저, 그다음 유사도 순서
- 권한: 팀원 업로드는 `sync='pending'`, 팀원의 매핑 삭제·폴더 변경은 403
- zip: 파일 이름과 개수, 악보 없는 곡은 건너뜀

`tests/e2e_v2.py`에 악보 흐름을 추가합니다. 드라이브 API와 Picker는 가짜로 대체합니다(`window.gapi`, `google.picker` 스텁과 `https://www.googleapis.com/drive/**` 라우트 가로채기 — 지금 유튜브를 가짜로 쓰는 방식과 같습니다).

## 9. 작업 순서 (2026-10-22 1~5 완료)

1. ✅ **서버**: 마이그레이션, `songKey`·메들리 분리·후보 찾기, `/api/sheets/*`, `/api/drive/index`, zip. `tests/test_sheets.py`
2. ✅ **설정 탭**: Picker로 악보 폴더 고르기, 목록 새로 읽기
3. ✅ **세트리스트 화면**: 악보 줄, 후보 고르기, 올리기, 받기
4. ✅ **작업 기록 상세**: 곡별 악보 받기·올리기, 주별 zip
5. ✅ **팀원 업로드 동기화**: pending 목록과 '지금 올리기'
6. **drdr 연동**: drdr 쪽 수신 API가 생긴 뒤 '보내기' 단추와 서명 토큰 (7장)

## 10. 남은 확인 사항

- **Picker로 폴더를 고르면 그 안의 기존 파일까지 `drive.file` 범위로 읽히는지**는 **실제 드라이브로 아직 확인하지 못했습니다**(테스트에서는 Picker와 드라이브 API를 가짜로 대체합니다). 배포 뒤 설정 탭에서 폴더를 고르고 '목록 새로 읽기'가 악보를 몇 개 찾는지 보면 바로 알 수 있습니다. 0개로 나오거나 403이 나면 Picker에서 악보 **파일**을 여러 개 직접 고르는 방식으로 바꿔야 합니다(`driveList`를 Picker 결과로 대체).
- 폴더 아래 하위 폴더까지 읽습니다(`driveList`, 폴더 50개·파일 20000개까지). 하위 폴더가 `drive.file`로 열리지 않으면 그 폴더만 조용히 건너뜁니다.
- 구글 클라우드 콘솔(사용자가 직접): Picker API 활성화, 브라우저용 API 키 발급(리퍼러 제한 `https://bbc.ppsoft.kr/*`), OAuth 동의 화면 범위에 `drive.file` 추가.
- 서버 디스크: `/home` 여유가 17GB입니다. 악보 PDF는 한 곡 수백 KB라 수백 곡이어도 문제없지만, 스캔 이미지가 섞이면 커질 수 있어 한 파일 20MB로 제한합니다.
- 악보는 교회 안에서만 씁니다. 드라이브 파일을 링크 공유로 열지 않고, 로그인한 팀원만 서버 사본을 받습니다.
