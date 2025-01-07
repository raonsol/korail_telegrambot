FROM python:3.13-slim-buster

WORKDIR /app
COPY Pipfile /app
COPY Pipfile.lock /app

RUN pip install pipenv && pipenv install --system --deploy

COPY . /app

EXPOSE 8080

CMD ["fastapi", "run", "src/app.py", "--port", "8080"]