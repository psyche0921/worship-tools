#!/usr/bin/env bash
# SQLite 백업(매일 cron). 실행 중인 DB를 sqlite 백업 API로 안전하게 복사하고 14일치만 남긴다.
#   30 3 * * * /home/tstech/worship-tools/deploy/backup.sh >> /home/tstech/worship-tools/data/backup.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."

name="worship-$(date +%F).db"
docker compose -f deploy/compose.yaml exec -T web python -c "
import sqlite3
src = sqlite3.connect('/data/worship.db')
dst = sqlite3.connect('/data/backups/$name')
src.backup(dst)
dst.close(); src.close()
" </dev/null
find data/backups -name 'worship-*.db' -mtime +14 -delete

# 악보 사본. 원본은 구글 드라이브에 있지만 '드라이브 대기'(팀원이 올린 것)는 서버에만 있다.
sheets=''
if [ -d data/sheets ]; then
  sheets="sheets-$(date +%F).tgz"
  tar -czf "data/backups/$sheets" -C data sheets
  find data/backups -name 'sheets-*.tgz' -mtime +14 -delete
  sheets=" · data/backups/$sheets"
fi
echo "$(date '+%F %T') 백업 완료: data/backups/$name$sheets"
