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

**알아 둘 제약**: 임베드를 막아 둔 영상(일부 공식 뮤직비디오)은 우리 페이지에서 재생되지 않습니다. `onError` 101·150이 오면 그 곡을 건너뛰고 '유튜브에서 열기'를 보여 줍니다. 그래서 보조로 `https://www.youtube.com/watch_videos?video_ids=…`(유튜브가 임시 재생목록을 만들어 줌, 로그인 불필요) 링크를 함께 둡니다.

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

- `key`·`timeSignature`·`tempo`·`structure`는 단톡방 메모에서 서버가 뽑아 둡니다(`app/setlistmeta.py`). **조성·박자·BPM·구조 정답을 공짜로 얻는 자리**입니다(`docs/drdr-training.md` 5장).
- `titles`는 메들리를 쪼갠 곡 이름입니다(`sheets`와 짝). 악보 하나가 곡 하나입니다.
- `url`은 **상대 주소**입니다. drdr는 공유 주소의 origin을 붙여 씁니다.
- **주의**: 세트리스트의 유튜브 링크는 **원곡**이고, `structure`는 **우리 팀 연주 계획**입니다. 둘이 맞지 않습니다(`docs/drdr-training.md` 3장). drdr는 `structure`를 원곡 영상의 반복 라벨로 쓰면 안 됩니다.
- drdr 저장소 쪽 작업(이 JSON을 받아 채보 작업을 만들고 악보를 참고 자료로 붙이는 일)은 그 저장소에서 합니다.

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

- **임베드가 막힌 영상이 얼마나 되는지**는 실제 세트리스트로 봐야 압니다. 많으면 `watch_videos` 쪽을 앞세웁니다.
- 공유 토큰이 유출되면 그 주 악보를 누구나 받습니다. 기간 제한은 두지 않기로 했으니(1장), 새 토큰이 필요하면 기록을 지우고 다시 만듭니다.
- 예전 기록 2건의 `playlist_id`와 재생목록 링크는 그대로 남습니다. 공유 페이지에서는 '유튜브 재생목록' 링크로 함께 보여 줍니다.
- **악보 크기 한도가 두 곳에 있습니다**(2026-10-23에 걸렸습니다). nginx `client_max_body_size 10m`이 먼저 막아서 10.08MB 악보 하나가 들어오지 못했습니다. 2026-10-23에 nginx를 `25m`, `MAX_SHEET_MB`를 `20`으로 함께 올렸습니다. **살아 있는 nginx 설정은 덮어쓰지 말고 그 줄만 고칩니다** — certbot이 같은 파일에 SSL 설정을 넣어 두었기 때문입니다.
