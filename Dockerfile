FROM python:3.11-slim

# 1. Install compilers for all supported languages
# build-essential (C/C++), default-jdk (Java), nodejs/npm (JS), mono-complete (C#)
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

# 2. Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 3. Copy application code
COPY . .

# 4. Create a non-root user (Render requirement for best practice)
RUN useradd -m appuser
USER appuser

# 5. Run the application
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:5000", "--timeout", "120"]