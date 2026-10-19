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
echo "$(date '+%F %T') 백업 완료: data/backups/$name"
