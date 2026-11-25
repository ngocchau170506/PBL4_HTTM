FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# ffmpeg for H.264 MP4 stitching, libgl1/libglib2.0 for OpenCV & MediaPipe, curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY server_ai/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    "requests>=2.31" \
    "mediapipe>=0.10.14"

COPY contracts /app/contracts
COPY CNN_IOT /app/CNN_IOT
COPY APP_IOT /app/APP_IOT
COPY server_ai /app/server_ai

COPY server_ai/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh && \
    chmod +x /usr/local/bin/docker-entrypoint.sh && \
    mkdir -p /app/server_ai/data /app/server_ai/evidence

EXPOSE 5000
EXPOSE 5005/udp

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
