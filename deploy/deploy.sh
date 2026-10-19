#!/usr/bin/env bash
# 170번 서버에서 실행: 최신 코드 받기 → 이미지 빌드 → 컨테이너 교체 → 상태 확인.
#   ~/worship-tools/deploy/deploy.sh
# PC에서는: ssh tstech@192.168.0.170 '~/worship-tools/deploy/deploy.sh'
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f deploy/.env ]; then
  echo "deploy/.env 가 없어요. deploy/.env.example 을 복사해서 채우세요." >&2
  exit 1
fi

git pull --ff-only --quiet
mkdir -p data/backups

export HOST_UID="$(id -u)" HOST_GID="$(id -g)"
docker compose -f deploy/compose.yaml up -d --build --remove-orphans

for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8830/api/health >/dev/null 2>&1; then
    echo "배포 완료: $(git log -1 --format='%h %s')"
    docker image prune -f >/dev/null
    exit 0
  fi
  sleep 1
done
echo "서버가 응답하지 않아요. 로그: docker compose -f deploy/compose.yaml logs web" >&2
exit 1
