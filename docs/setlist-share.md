# 세트리스트 공유 페이지 (v3)

2026-10-23 사용자와 정한 내용입니다. 세트리스트의 결과물을 **재생목록 링크 하나**에서 **공유 페이지 하나**로 바꿉니다.

`docs/sheets-design.md`(악보 관리)와 `docs/drdr-training.md`(채보 학습)를 이 문서가 덮어쓰는 부분이 있습니다. 어긋나면 이 문서가 기준입니다.

## 1. 정한 것

| 항목 | 결정 | 왜 |
|---|---|---|
| 유튜브 재생목록 | **만들지 않습니다.** 공유 페이지의 플레이어가 연속 재생합니다 | 재생목록의 목적은 '끊기지 않는 연속 재생'뿐이었습니다. 플레이어로 되면 구글 계정이 아예 필요 없습니다 |
| 악보 저장소 | **서버에 직접 올립니다.** 구글 드라이브 연동을 뗍니다 | 어차피 서버로 사본을 받아 쓰고 있었습니다. 올릴 때 바로 서버로 보내면 한 단계가 없어집니다 |
| 기존 드라이브 악보 | ✅ 2026-10-23 완료 — 111개를 서버로 옮기고 드라이브 코드를 모두 지웠습니다 | 10MB를 넘은 1개는 nginx가 막았습니다(8장) |
| 공유 페이지 접근 | 추측 불가능한 토큰 주소(`/s/{token}`). **로그인 없이 누구나** | 찬양팀에게 링크만 주면 되게 |
| 악보 다운로드 | 같은 토큰 아래 주소(`/api/share/{token}/sheet/{id}`). 그 세트리스트의 악보만 | 번호를 바꿔 다른 악보를 엿볼 수 없게 |
| 팀원 권한 | 세트리스트 만들기를 **팀원도** 할 수 있습니다 | 구글 연동이 없어져서 막을 이유가 사라졌습니다 |
| drdr 연동 | 공유 페이지의 **JSON을 drdr가 읽어 가는** 방식. 밀어 넣지 않습니다 | 이미 공개·추측불가 주소라 따로 인증을 만들 필요가 없습니다 |

### 유튜브 재생목록을 왜 뺄 수 있는가

`YT.Player`의 `cuePlaylist({playlist: [영상ID…]})`가 **영상 ID 배열을 그대로 큐에 넣어 차례로 재생**합니다([IFrame Player API](https://developers.google.com/youtube/iframe_api_reference)). 계정도, OAuth도, 재생목록도 필요 없습니다.

그래서 함께 사라지는 것들: `youtube` OAuth 범위, 팀원을 테스트 사용자로 등록하는 일, 재생목록이 사람마다 다른 계정에 흩어지는 문제, 하루 할당량 걱정.

**알아 둘 제약**: 임베드를 막아 둔 영상(일부 공식 뮤직비디오)은 우리 페이지에서 재생되지 않습니다. `onError` 101·150이 오면 그 곡을 건너뛰고 '유튜브에서 열기'를 보여 줍니다.

### 휴대폰에서는 유튜브 앱으로 넘깁니다 (2026-10-24)

페이지 안의 플레이어는 **백그라운드 재생이 안 됩니다**(화면을 끄거나 다른 앱으로 가면 멈춥니다). 유튜브 프리미엄이면 **앱**에서는 계속 들립니다. 그래서 공유 페이지의 첫 단추를 '유튜브 앱에서 연속 재생'으로 두고, 페이지 안 플레이어는 '이 페이지에서 재생'으로 뒤에 둡니다.

- `GET /api/share/{token}/youtube`(로그인 없이)가 서버에서 `https://www.youtube.com/watch_videos?video_ids=…`를 **리다이렉트를 따라가지 않고** 불러 `Location`의 임시 재생목록 ID(`TLGG…`)를 꺼냅니다(`app/youtube.py`의 `make_temp_list`).
- 돌려주는 주소는 `https://www.youtube.com/watch?v={첫 영상}&list=TLGG…`입니다. **평범한 재생목록 주소**라서 안드로이드·아이폰에서 누르면 앱 링크로 유튜브 앱이 열립니다. `watch_videos` 주소를 그대로 주면 앱이 아니라 브라우저가 열립니다 — 이것이 한 번 더 부르는 이유입니다.
- 임시 재생목록 ID는 서버에서 **한 시간** 기억합니다(`_LIST_CACHE`). 곡 목록이 같으면 유튜브를 다시 부르지 않습니다.
- 못 받아 왔거나(502) 서버가 느릴 때도 단추는 `watch_videos` 주소로 열립니다. 로그인도 API 키도 필요 없습니다.
- 곡마다 있는 '유튜브' 링크도 받아 온 뒤에는 `&list=…&index=N`이 붙어 **그 곡부터 앱에서 이어서** 재생됩니다.

#### 카카오톡에서 열면 앱 링크가 안 듣습니다 (2026-10-24)

세트리스트는 카톡으로 보내니 대부분 **카카오톡 인앱 브라우저**에서 열립니다. 인앱 브라우저(WebView)는 `youtube.com` 주소를 앱으로 넘겨 주지 않아서, 평범한 재생목록 주소만으로는 웹 플레이어가 열립니다(주소를 크롬·사파리에 직접 넣으면 앱이 열리는 이유가 이것입니다).

그래서 휴대폰에서는 **앱을 직접 부르는 주소**로 바꿔 둡니다(`web/share.html`의 `appUrl`).

| 기기 | 주소 | 앱이 없으면 |
| --- | --- | --- |
| 안드로이드 | `intent://www.youtube.com/watch?v=…&list=…#Intent;scheme=https;package=com.google.android.youtube;S.browser_fallback_url=…;end` | `browser_fallback_url`로 웹이 열립니다 |
| 아이폰 | `youtube://www.youtube.com/watch?v=…&list=…` | 아무 일도 안 일어나므로 1.5초 뒤 웹으로 보냅니다(`iosFallback`) |
| PC | 그대로 `https://www.youtube.com/watch?…` | — |

- 앱 스킴은 **새 창으로 열지 않습니다**(`target`을 떼어 둡니다). 새 창으로 열면 빈 창이 남습니다.
- 그래도 앱이 안 열릴 때를 위해 인앱 브라우저(UA에 `KAKAOTALK`·`NAVER(inapp`·`Instagram`·`FBAN`·`Line/`)에서는 '기본 브라우저로 열기'(`#outHint`)를 함께 보여 줍니다. 카카오톡은 `kakaotalk://web/openExternal?url=…`로 바깥 브라우저를 띄워 줍니다.
- e2e(`tests/e2e_v2.py`)에서 카카오톡 안드로이드 UA와 아이폰 UA로 각각 `intent://`·`youtube://`가 되는지 봅니다.

## 2. 세트리스트의 키가 바뀝니다

지금 기록의 키는 재생목록 ID(`setlists.playlist_id`)입니다. 재생목록을 안 만들면 그 키가 없습니다. **키를 공유 토큰(`share_token`)으로 바꿉니다.** 공유 주소가 곧 그 기록의 키입니다.

마이그레이션(`MIGRATIONS` 5단계)에서 `setlists`를 다시 만듭니다.

```sql
share_token TEXT NOT NULL UNIQUE   -- 새 키. 공유 주소 /s/{token}
playlist_id TEXT UNIQUE            -- 예전에 만든 재생목록(이제 비어 있어도 된다)
url         TEXT NOT NULL DEFAULT ''
```

기존 행에는 임의 토큰을 채워 넣습니다(`lower(hex(randomblob(16)))`). 새 행은 `secrets.token_urlsafe(16)`입니다.

## 3. API

| 메서드 · 경로 | 권한 | 내용 |
|---|---|---|
| `POST /api/setlists` | 로그인 | `{title, songs, rawMessage}` → 세트리스트를 만들고 `{key, shareToken, shareUrl, …}`를 돌려준다. 재생목록은 만들지 않는다 |
| `GET /api/setlists`, `GET /api/setlists/{token}` | 로그인 | 목록·상세(원본 메시지는 관리자에게만) |
| `DELETE /api/setlists/{token}` | 만든 사람 · 관리자 | 삭제 |
| **`GET /s/{token}`** | **누구나** | 공유 페이지(`web/share.html`) |
| **`GET /api/share/{token}`** | **누구나** | 공유 페이지가 그리는 JSON. drdr도 이것을 읽는다(5장) |
| **`GET /api/share/{token}/sheet/{sheet_id}`** | **누구나** | 그 세트리스트에 붙은 악보만 내려준다 |
| **`GET /api/share/{token}/sheets.zip`** | **누구나** | 악보 묶음 |
| `POST /api/sheets` | 로그인 | 악보 업로드(multipart: `title`, `file`). 드라이브 관련 칸은 없앤다 |

- 공개 경로는 전부 GET이라 Origin 검사(CSRF)와 무관합니다. 세션 의존성을 붙이지 않습니다.
- `GET /api/sheets/zip/{...}`와 로그인용 `GET /api/sheets/{id}/file`은 그대로 둡니다(로그인한 팀원이 작업 기록에서 받는 길).
- `rawMessage`는 공유 JSON에 **넣지 않습니다**(단톡방 원문).

## 4. 공유 페이지 (`web/share.html`)

```
예배팀 세트리스트            2026년 10월 25일 주일
┌─────────────────────────────┐
│   [ 유튜브 플레이어 ]        │   ← cuePlaylist(곡 전체)
└─────────────────────────────┘
 [전체 연속 재생]  [유튜브에서 연속 재생]  [악보 전체 받기(zip)]

 1. 나의 죄를 씻기는                        ▶  악보 받기
    (KEY - C | TIME - 4/4 | TEMPO - 72)
 2. 나로부터 & 기대                         ▶  나로부터 ⬇  기대 ⬇
    (KEY - A | TIME - 4/4 | TEMPO - 123)
    intro(16) - V - C - (8) - V - C*3 - …
```

- 로그인 화면이 없습니다. 토큰이 틀리면 '찾을 수 없는 주소'만 보여 줍니다.
- 곡을 누르면 플레이어가 그 곡으로 건너뜁니다(`playVideoAt`).
- 임베드가 막힌 곡은 `onError`에서 표시하고 건너뜁니다.
- 폰에서 먼저 보는 화면이라 세로 한 줄 배치입니다. v2 화면의 CSS 토큰을 그대로 씁니다.

## 5. drdr 연동 계약 (여기서 정하는 것은 이것뿐)

drdr가 **공유 JSON을 읽어 갑니다.** worship-tools가 밀어 넣지 않으니 양쪽에 인증을 만들 필요가 없습니다. 관리자가 drdr에 공유 주소를 붙여넣으면 drdr가 `GET /api/share/{token}`을 한 번 받습니다.

```json
{
  "token": "…",
  "title": "Setlist 261025",
  "date": "2026-10-25",
  "weekLabel": "2026년 10월 25일 주일",
  "songs": [
    {
      "videoId": "PV3LJu68nwk",
      "youtubeUrl": "https://youtu.be/PV3LJu68nwk",
      "name": "마음의 예배 & 내 사람의 이유라",
      "titles": ["마음의 예배", "내 사람의 이유라"],
      "notes": ["(KEY - E | TIME - 4/4 | TEMPO - 64)", "Intro(4) - A - B - C - …"],
      "key": "E", "timeSignature": "4/4", "tempo": 64,
      "structure": "Intro(4) - A - B - C - (4) - A - B - B2 - C2*2 - …",
      "sheets": [
        {"title": "마음의 예배", "name": "마음의 예배.pdf",
         "mime": "application/pdf", "size": 131003,
         "url": "/api/share/…/sheet/7"}
      ]
    }
  ],
  "sheetsZipUrl": "/api/share/…/sheets.zip",
  "watchAllUrl": "https://www.youtube.com/watch_videos?video_ids=…"
}
```

`GET /api/share/{token}/youtube`는 따로 내려줍니다(유튜브를 한 번 불러야 해서 페이지 로딩을 붙잡지 않습니다).

```json
{"listId": "TLGG…",
 "url": "https://www.youtube.com/watch?v=…&list=TLGG…",
 "playlistUrl": "https://www.youtube.com/playlist?list=TLGG…"}
```

- `sheets[]`의 `id`·`version`(2026-10-24 추가)은 BBC Score(찬양팀 악보 필기 앱)가 씁니다. `version`은 악보 파일 sha256의 앞 16자입니다. 같은 곡이 다른 주에 다시 나오면 `id`·`version`이 같고, 악보를 새 내용으로 다시 올리면 `version`만 바뀝니다. 필드를 더하기만 했으므로 drdr는 그대로 읽으면 됩니다.
- `scoreUrl`(2026-10-24 추가, `SCORE_ORIGIN`이 있을 때만): BBC Score에서 이 세트리스트를 여는 주소.
- `key`·`timeSignature`·`tempo`·`structure`는 단톡방 메모에서 서버가 뽑아 둡니다(`app/setlistmeta.py`). **조성·박자·BPM·구조 정답을 공짜로 얻는 자리**입니다(`docs/drdr-training.md` 5장).
- `titles`는 메들리를 쪼갠 곡 이름입니다(`sheets`와 짝). 악보 하나가 곡 하나입니다.
- `url`은 **상대 주소**입니다. drdr는 공유 주소의 origin을 붙여 씁니다.
- **주의**: 세트리스트의 유튜브 링크는 **원곡**이고, `structure`는 **우리 팀 연주 계획**입니다. 둘이 맞지 않습니다(`docs/drdr-training.md` 3장). drdr는 `structure`를 원곡 영상의 반복 라벨로 쓰면 안 됩니다.
- drdr 저장소 쪽 작업(이 JSON을 받아 채보 작업을 만들고 악보를 참고 자료로 붙이는 일)은 그 저장소에서 합니다.

### 세트리스트를 만들면 drdr가 스스로 가져갑니다 (2026-10-24)

관리자가 공유 주소를 drdr에 붙여 넣지 않아도 되게, drdr가 **새 세트리스트 목록을 읽어 갑니다** (여전히 밀어 넣지 않습니다).

| 경로 | 권한 | 하는 일 |
|---|---|---|
| `GET /api/drdr/setlists?since=<ms>&limit=50` | `Authorization: Bearer <DRDR_FEED_TOKEN>` | `since`보다 나중에 만든 세트리스트를 오래된 순서로 `{items: [{token, title, songs, createdAt, shareUrl}], more}` |

- 목록은 공유 토큰을 모두 담으므로 **비밀 키**를 겁니다. `deploy/.env`의 `DRDR_FEED_TOKEN`과 drdr의 `DRDR_WORSHIP_FEED_TOKEN`이 같은 값이어야 합니다. 비우면 목록이 꺼집니다(404)
- drdr는 15분마다 묻고, 마지막으로 본 `createdAt`을 기억합니다. 곡 · 악보는 지금처럼 각 세트리스트의 공유 JSON에서 읽습니다
- 악보는 곡 제목으로 그때그때 찾아 붙이므로 **나중에 올린 악보도** 공유 JSON에 나타납니다. drdr는 만든 지 14일 안의 세트리스트를 다시 읽어 새 곡 · 새 악보를 받습니다

## 6. 악보: 드라이브를 뗀다

- 업로드는 서버로 바로 갑니다. `POST /api/sheets`에서 `driveFileId`·`driveName`을 없애고 `sheets.sync`는 쓰지 않습니다(칸은 남겨 두고 값은 항상 `'drive'`가 아니라 의미 없는 값).
- 후보 찾기는 `drive_index` 대신 **이미 올라온 악보(`sheets`)** 에서 합니다. 사용자가 말한 '이미 했던 곡과 중복되는 경우'가 바로 이 길로 해결됩니다 — 곡 제목이 같으면 자동으로 붙고(`find_sheet`), 비슷하면 후보로 보여 줍니다(`candidates`).
- 설정 탭에는 **'드라이브에서 모두 가져오기'** 만 한시적으로 남깁니다. `drive_index`에 있는 파일을 브라우저가 차례로 받아 서버에 올리고(제목은 파일 이름에서), 끝나면 그 카드와 드라이브 코드를 지웁니다.
- 가져오기가 끝나면 `drive_index`·`sheet_aliases`의 드라이브 흔적은 더 늘지 않습니다.

## 7. 작업 순서

1. ✅ 마이그레이션(`share_token`), `POST /api/setlists` 새 모양, 기록 키 교체, 메모 파서 (pytest)
2. ✅ 공개 API(`/api/share/...`)와 `web/share.html`
3. ✅ 세트리스트 탭: 재생목록 만들기 → '세트리스트 만들기 + 공유 링크', 악보는 서버로 직접 업로드
4. ✅ 설정 탭: 드라이브 카드를 '한 번만 쓰는 가져오기'로 축소
5. ✅ 드라이브 코드 제거 (2026-10-23)
6. drdr 저장소에서 공유 JSON 읽기 (별도)

## 8. 남은 확인 사항

- **임베드가 막힌 영상이 얼마나 되는지**는 실제 세트리스트로 봐야 압니다. 2026-10-24부터 앱 단추를 앞세웠으니 영향이 줄었습니다.
- **임시 재생목록(`TLGG…`)이 얼마나 오래 사는지**는 확실하지 않습니다. 한 시간만 기억하고 다시 만드는 것으로 두었습니다. 앱이 '재생목록을 찾을 수 없다'고 하면 페이지를 새로 열어 주세요.
- 공유 토큰이 유출되면 그 주 악보를 누구나 받습니다. 기간 제한은 두지 않기로 했으니(1장), 새 토큰이 필요하면 기록을 지우고 다시 만듭니다.
- 예전 기록 2건의 `playlist_id`와 재생목록 링크는 그대로 남습니다. 공유 페이지에서는 '유튜브 재생목록' 링크로 함께 보여 줍니다.
- **악보 크기 한도가 두 곳에 있습니다**(2026-10-23에 걸렸습니다). nginx `client_max_body_size 10m`이 먼저 막아서 10.08MB 악보 하나가 들어오지 못했습니다. 2026-10-23에 nginx를 `25m`, `MAX_SHEET_MB`를 `20`으로 함께 올렸습니다. **살아 있는 nginx 설정은 덮어쓰지 말고 그 줄만 고칩니다** — certbot이 같은 파일에 SSL 설정을 넣어 두었기 때문입니다.
