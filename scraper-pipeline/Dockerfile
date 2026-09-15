FROM python:3.12-slim

# Install system utilities needed for Playwright's dependencies
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium and its OS-level dependencies
RUN playwright install --with-deps chromium

COPY . .

# Ensure Python reads standard output instantly in logs
ENV PYTHONUNBUFFERED=1

# Tell Uvicorn to listen to the dynamic port Render provides
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
