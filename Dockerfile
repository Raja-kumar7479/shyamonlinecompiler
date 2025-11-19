# Dockerfile - improved for Render + Gunicorn + safer defaults
FROM python:3.11-slim

# --- Environment ---
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLASK_APP=app.py \
    PORT=5000

# Install any system deps needed by some Python packages (adjust if not needed)
# Keep minimal to reduce image size
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    default-libmysqlclient-dev \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /usr/src/app

# Copy requirements and install (pip as root during build is fine)
COPY requirements.txt .
RUN python -m pip install --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create a non-root user and give ownership (optional, but removes pip root warning at runtime)
RUN groupadd --system app && useradd --system --gid app --create-home app \
 && chown -R app:app /usr/src/app

USER app

# Expose the port (Render will override $PORT at runtime)
EXPOSE ${PORT}

# Run gunicorn binding to the PORT environment variable that Render sets.
# We increase timeout and use a small worker count appropriate for small instances.
CMD ["sh", "-c", "gunicorn app:app --bind 0.0.0.0:${PORT} --workers 2 --threads 2 --timeout 120 --access-logfile - --error-logfile -"]
