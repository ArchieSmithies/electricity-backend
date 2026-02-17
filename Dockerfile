FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN adduser --disabled-password --gecos "" appuser && chown -R appuser:appuser /app
USER appuser

ENV PORT=8000
EXPOSE $PORT

CMD gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 60 --access-logfile -
