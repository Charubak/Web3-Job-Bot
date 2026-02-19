FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# DATA_DIR points to the persistent Fly.io volume
ENV DATA_DIR=/data

CMD ["python3", "-u", "bot.py"]
