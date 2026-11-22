# 자동 배포

`main` 에 머지되어 Test Suite 가 통과하면 GitHub Actions(`.github/workflows/deploy.yml`)가 서버에 SSH 로 접속해
`scripts/deploy.sh <커밋 SHA>` 를 실행한다. 이미지는 서버에서 빌드한다 (`make docker-compose-up-mq`).

`scripts/deploy.sh` 가 하는 일:

1. SHA 형식 확인, `origin/main` 에 있는 커밋인지 확인 (다른 브랜치는 배포하지 않음)
2. 서버 체크아웃에 커밋하지 않은 변경(추적 중인 파일)이 있으면 중단
3. postgres 가 떠 있으면 DB 백업 → `~/backups/korail_bot-<시각>-<이전 커밋>.dump` (최근 10개만 유지)
4. 해당 커밋을 detached 로 체크아웃 → `make docker-compose-up-mq`
5. `/health` 와 Celery 워커 ping 을 2분 동안 기다림, 실패하면 컨테이너 상태와 로그를 남기고 실패 처리
6. 쓰지 않는 이미지 정리

배포는 한 번에 하나만 돈다 (`flock`, 워크플로 `concurrency`).

## 서버 설정 (한 번만)

### 1. 배포 키

키를 만들고 공개키만 서버에 둔다.

```bash
ssh-keygen -t ed25519 -N "" -C "github-actions korail deploy" -f korail_deploy
```

서버의 `~raonsol/.ssh/authorized_keys` 에 한 줄로 넣는다. `restrict` 가 포트 포워딩·터미널 등을 막고, `command` 가
이 키로는 `deploy.sh` 만 실행되게 한다. GitHub 이 보낸 SHA 는 `SSH_ORIGINAL_COMMAND` 로 넘어간다.

```text
restrict,command="/home/raonsol/korail_telegrambot/scripts/deploy.sh" ssh-ed25519 AAAA...(korail_deploy.pub) github-actions korail deploy
```

서버 호스트 키를 확인해 둔다. GitHub Actions 는 이 값과 다른 서버에는 접속하지 않는다.

```bash
ssh-keyscan -p 210 -t ed25519 <서버 주소>   # 출력 줄이 DEPLOY_KNOWN_HOSTS
```

### 2. GitHub 설정

저장소 Settings 에서 한다.

- **Environments > New environment `production`**
  - Deployment branches and tags: `main` 만 허용
- **Environments > production > Environment secrets**

  | 이름                  | 값                                   |
  | -------------------- | ------------------------------------ |
  | `DEPLOY_HOST`        | 서버 주소                              |
  | `DEPLOY_USER`        | `raonsol`                            |
  | `DEPLOY_PORT`        | `210`                                |
  | `DEPLOY_SSH_KEY`     | `korail_deploy` 개인키 파일 전체          |
  | `DEPLOY_KNOWN_HOSTS` | 1번의 `ssh-keyscan` 출력 줄              |

## 운영

- **손으로 배포**: Actions > Deploy > Run workflow. 고른 ref 의 커밋을 배포한다 (`main` 에 있는 커밋만)
- **되돌리기**: 서버에서 `scripts/deploy.sh <예전 main 커밋 SHA>`. DB 를 되돌려야 하면 백업에서
  `docker compose --profile celery exec -T postgres sh -c 'pg_restore -c -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < 백업.dump`
- **배포 잠시 막기**: production environment 에 required reviewers 나 wait timer 를 건다
- 서버 체크아웃은 배포 후 detached HEAD 상태다. 서버에서 직접 고칠 일이 있으면 브랜치를 만들어 커밋하고 PR 로 올린다
- 실행 중인 예약은 배포로 워커가 재시작되면 "예약 워커가 다시 시작되어..." 오류로 끝난다 (CLAUDE.md Celery Mode 참고)
