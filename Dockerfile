FROM python:3.12-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Install dependencies (cached layer separate from app code)
COPY requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt gunicorn

# Copy application
COPY . .

RUN chmod +x entrypoint.sh

# Media files volume mount point
RUN mkdir -p /app/media/audio

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
