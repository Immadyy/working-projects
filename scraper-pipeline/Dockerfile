FROM python:3.12-slim

# Install system utilities needed for Playwright's dependencies
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements relative to the root build context we set in Step 1
COPY scraper-pipeline/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium and its OS-level dependencies
RUN playwright install --with-deps chromium

# Copy the rest of the subfolder files into the container
COPY scraper-pipeline/ .

# Ensure Python reads standard output instantly in logs
ENV PYTHONUNBUFFERED=1

# Tell Uvicorn to listen to the dynamic port Render provides
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
