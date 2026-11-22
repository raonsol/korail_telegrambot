#!/usr/bin/env bash
# 서버에서 커밋 하나를 배포한다: DB 백업 → 해당 커밋 체크아웃 → make docker-compose-up-mq → 상태 확인.
# GitHub Actions(.github/workflows/deploy.yml)가 SSH 로 커밋 SHA 를 넘겨 호출한다. 배포 키의 forced command 가
# 이 스크립트만 실행하게 하므로 SHA 는 SSH_ORIGINAL_COMMAND 로 들어온다. 설정·되돌리기: docs/deploy.md
#
#   scripts/deploy.sh <40자리 커밋 SHA>
#
# 환경변수(선택):
#   KORAIL_ROOT        서버 체크아웃 (기본: 이 스크립트가 있는 저장소)
#   KORAIL_BACKUP_DIR  DB 백업 위치 (기본: ~/backups), 최근 10개만 남긴다
set -euo pipefail

log() { echo "[deploy] $*"; }

main() {
  local sha="${1:-${SSH_ORIGINAL_COMMAND:-}}"
  if [[ ! "$sha" =~ ^[0-9a-f]{40}$ ]]; then
    echo "usage: deploy.sh <40자리 커밋 SHA>" >&2
    exit 2
  fi

  local root="${KORAIL_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
  cd "$root"

  # 배포는 한 번에 하나만
  exec 9>"${TMPDIR:-/tmp}/korail-deploy.lock"
  if ! flock -n 9; then
    echo "다른 배포가 진행 중입니다" >&2
    exit 1
  fi

  log "배포 시작 $sha"

  # main 에 들어간 커밋만 배포한다 (예전 커밋으로 되돌리기는 허용)
  git fetch --quiet origin main
  if ! git merge-base --is-ancestor "$sha" origin/main; then
    echo "$sha 는 origin/main 에 없는 커밋입니다" >&2
    exit 1
  fi
  # 추적 중인 파일을 서버에서 고쳐 둔 채로 덮어쓰지 않는다 (.env 등 추적 안 하는 파일은 그대로 둔다)
  if ! git diff --quiet HEAD; then
    echo "서버 체크아웃에 커밋하지 않은 변경이 있습니다:" >&2
    git status --short --untracked-files=no >&2
    exit 1
  fi

  backup_db

  git -c advice.detachedHead=false checkout --quiet --detach "$sha"

  log "이미지 빌드·컨테이너 갱신"
  make docker-compose-up-mq

  wait_healthy
  docker image prune -f >/dev/null
  log "배포 완료 $sha"
}

# 실행 중인 postgres 가 있을 때만 백업한다 (첫 배포 등 꺼져 있으면 건너뜀)
backup_db() {
  local dir="${KORAIL_BACKUP_DIR:-$HOME/backups}"
  if [[ -z "$(docker compose --profile celery ps -q --status running postgres)" ]]; then
    log "postgres 가 꺼져 있어 DB 백업을 건너뜁니다"
    return
  fi
  mkdir -p "$dir"
  local file
  file="$dir/korail_bot-$(date +%Y%m%d-%H%M%S)-$(git rev-parse --short HEAD).dump"
  log "DB 백업 $file"
  docker compose --profile celery exec -T postgres \
    sh -c 'pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' >"$file.tmp"
  mv "$file.tmp" "$file"
  ls -1t "$dir"/korail_bot-*-*.dump | tail -n +11 | xargs -r rm -f
}

# 웹 서버 /health 와 Celery 워커 준비를 최대 2분 기다린다
wait_healthy() {
  local deadline=$((SECONDS + 120))
  while ((SECONDS < deadline)); do
    if curl -fsS -o /dev/null http://127.0.0.1:8391/health &&
      docker compose --profile celery exec -T worker \
        celery -A telegramBot.tasks inspect ping -t 5 >/dev/null 2>&1; then
      log "웹 서버·워커 정상"
      return
    fi
    sleep 2
  done
  echo "2분 안에 웹 서버 또는 워커가 준비되지 않았습니다" >&2
  docker compose --profile celery ps >&2
  docker compose --profile celery logs --tail 50 web_celery worker >&2
  exit 1
}

# 체크아웃이 이 파일을 바꿀 수 있으므로 bash 가 이미 읽은 내용으로 끝낸다
main "$@"; exit
