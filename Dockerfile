FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080

CMD exec gunicorn --bind "0.0.0.0:$PORT" --workers 1 --threads 8 --timeout 300 main:app
