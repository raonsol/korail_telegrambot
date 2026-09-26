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
COPY Pipfile* ./

RUN pip install --no-cache-dir pipenv && \
  pipenv install --system --deploy --clear

COPY src .
COPY --from=webapp /webapp/dist ./webapp_dist

# subprocess 모드 SQLite 저장 위치 (docker-compose에서 볼륨 마운트)
RUN mkdir -p /app/data

EXPOSE 8390 8391

CMD ["fastapi", "run", "app.py", "--host", "0.0.0.0", "--port", "8391"]
