# 1) 웹앱(PWA) 빌드 - 최종 이미지에는 빌드 결과(dist)만 포함
FROM node:22-alpine AS webapp

WORKDIR /webapp
COPY webapp/package.json webapp/package-lock.json ./
RUN npm ci
COPY webapp/ ./
RUN npm run build

# 2) 애플리케이션
FROM python:3.13.1-slim

# Install timezone data
RUN apt-get update && apt-get install -y tzdata && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock ./

# uv.lock 그대로 시스템 Python 에 설치 (PATH 의 fastapi·celery 를 그대로 사용)
# --locked: uv.lock 이 pyproject.toml 과 맞지 않으면 빌드 실패. 설치 후 uv 는 이미지에서 제거
RUN pip install --no-cache-dir uv==0.12.19 && \
  uv export --locked --no-dev --format requirements.txt -o /tmp/requirements.txt && \
  uv pip install --system --no-cache --require-hashes -r /tmp/requirements.txt && \
  rm /tmp/requirements.txt && \
  pip uninstall -y uv

COPY src .
COPY --from=webapp /webapp/dist ./webapp_dist

# subprocess 모드 SQLite 저장 위치 (docker-compose에서 볼륨 마운트)
RUN mkdir -p /app/data

# print() 출력이 버퍼에 쌓이지 않고 바로 docker logs 에 보이도록
ENV PYTHONUNBUFFERED=1

EXPOSE 8390 8391

CMD ["fastapi", "run", "app.py", "--host", "0.0.0.0", "--port", "8391"]
