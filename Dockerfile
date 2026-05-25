# Use a slim Python base image
FROM python:3.12-slim

# Set a working directory
WORKDIR /app

# Install system dependencies required by Pillow and others
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libffi-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY . /app

# Expose the port Waitress will listen on
EXPOSE 8090

# Use Waitress as the production WSGI server
CMD ["waitress-serve", "--host=0.0.0.0", "--port=8090", "app:app"]
