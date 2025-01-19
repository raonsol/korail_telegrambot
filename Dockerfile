FROM python:3.13.1-slim

WORKDIR /app
COPY Pipfile* ./

RUN pip install --no-cache-dir pipenv && \
  pipenv install --system --deploy --clear

COPY src .

EXPOSE 8391

CMD ["fastapi", "run", "app.py", "--port", "8391"]