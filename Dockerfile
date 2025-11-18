# Use a lightweight base image for Python 3.11
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV FLASK_APP app.py

# Set the working directory
WORKDIR /usr/src/app

# Copy and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY . .

# Expose the port (Flask/Gunicorn default is 5000)
EXPOSE 5000

# Run the application using Gunicorn
# The --workers are set dynamically (2*CPU + 1 is a common formula)
CMD ["gunicorn", "-b", "0.0.0.0:5000", "app:app"]