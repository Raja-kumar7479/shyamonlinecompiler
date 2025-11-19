# Dockerfile - improved for Render + Gunicorn + safer defaults
FROM python:3.11-slim


ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLASK_APP=app.py \
    PORT=5000


RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    default-libmysqlclient-dev \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /usr/src/app


COPY requirements.txt .
RUN python -m pip install --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt


COPY . .

RUN groupadd --system app && useradd --system --gid app --create-home app \
 && chown -R app:app /usr/src/app

USER app

EXPOSE ${PORT}

CMD ["sh", "-c", "gunicorn app:app --bind 0.0.0.0:${PORT} --workers 1 --threads 2 --timeout 120 --access-logfile - --error-logfile -"]

