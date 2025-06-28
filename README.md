# 코레일 KTX 예매 텔레그램 챗봇

## 참고

- 본 서비스는 [carpedm20/korail2](https://github.com/carpedm20/korail2)를 기반으로 합니다.

## 주의사항

1. 개인용 목적이 아닌 상업적 목적등으로 이용하는 것을 엄중히 금합니다.
2. 본 프로그램을 사용할 경우, 기본으로 설정된 1초에 1번 조회 요청에 대한 설정 값 이상으로 빠르게 설정하지 마십시오. 코레일 서버에 무리가 갈 뿐 아니라, 단위 시간내에 보다 빠른 값으로 조회를 요청할 경우, 계정이 정지될 수 있습니다.
3. 본 프로그램은 2025-01-26일 기준으로 테스트되었으며, 추후 코레일 서버의 업데이트에 따라 작동하지 않을 수 있습니다.

## 사전 설정

### 환경변수

`.env.example` 파일을 참조하여 아래와 같이 `.env` 파일을 생성합니다.

```bash
USERID # 코레일 아이디
USERPW # 코레일 비밀번호
BOTTOKEN # 텔레그램 봇 토큰
ALLOW_LIST # 예약을 허용할 계정 전화번호(콤마로 구분)
ADMIN_PW # 관리자 비밀번호
```

### 텔레그램 설정

1. 텔레그램 봇을 생성하고 API 토큰을 발급받은 후, `.env` 파일에 `BOTTOKEN` 환경변수로 설정합니다.
2. 다음 API를 통해 webhook을 설정합니다. 서버 endpoint는 https로 설정되어야 하며, 80, 88, 443, 8443 포트만 허용됩니다.

```bash
curl -F "url=[서버 endpoint]" "https://api.telegram.org/bot[BOTTOKEN 키]/setWebhook"
```

## 실행 기능 안내

- 예약 챗봇: 텔레그램 대화창에서 /start, /cancel, /status 등 명령어로 예약 과정을 진행할 수 있습니다.
- 관리자 기능: /cancelall, /allusers 명령어 등을 통해 다수 사용자 예약을 한 번에 제어할 수 있습니다.

## 실행

### 로컬 설치 및 실행

시스템에 `pipenv`가 설치되어 있어야 합니다.
실행 후의 endpoint는 `localhost:8080/telebot`입니다.

```bash
make setup
make run
```

## 사용 방법 (Case by Case)

### 1. 로컬 실행

1) pipenv 설치  
2) `.env` 파일 수정  
3) 다음 명령어 순서대로 실행  

```bash
make install
make run
```

이후, 브라우저 또는 텔레그램 챗봇에서 예약을 진행합니다.

### 2. Docker 컨테이너 실행

1) `.env` 파일 준비  
2) Docker 이미지 빌드  

```bash
make build
```

3) 컨테이너 실행  

```bash
make run-docker
```

## NGINX 설정

dev는 8390, prod는 8391 포트로 서버가 실행됩니다.
이를 reverse proxy로 연결하기 위해 아래와 같이 설정합니다.
> telegram webhook은 80, 88, 443, 8443 포트와 https url만 사용할 수 있습니다. 따라서 포트 대신 subpath를 통해 서버를 분기하는 것이 좋습니다.

### 설정 예시

```nginx
location /telebot {
  rewrite ^/telebot/(.*) /$1 break;
  proxy_pass http://localhost:8391;
  proxy_set_header Host $host;
  proxy_set_header X-Real-IP $remote_addr;
  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  proxy_set_header X-Forwarded-Proto $scheme;
}

location /telebot_dev {
  rewrite ^/telebot_dev/(.*) /$1 break;
  proxy_pass http://localhost:8390;
  proxy_set_header Host $host;
  proxy_set_header X-Real_IP $remote_addr;
  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  proxy_set_header X-Forwarded-Proto $scheme;
}
```