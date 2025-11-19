FROM python:3.11-slim

# Install compilers and runtime environments for supported languages
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    default-jdk \
    nodejs \
    npm \
    mono-complete \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create a non-root user for security
RUN useradd -m appuser
USER appuser

# Run Gunicorn
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "4", "--timeout", "60"]