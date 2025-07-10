FROM python:3.13.1-slim

# Install timezone data
RUN apt-get update && apt-get install -y tzdata && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY Pipfile* ./

RUN pip install --no-cache-dir pipenv && \
  pipenv install --system --deploy --clear

COPY src .

EXPOSE 8390 8391

CMD ["fastapi", "run", "app.py", "--host", "0.0.0.0", "--port", "8391"]