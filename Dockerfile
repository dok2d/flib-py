FROM python:3.9-slim

RUN apt-get update && apt-get install -y \
    unzip \
    libpango1.0-0 \
    libgdk-pixbuf2.0-0 \
    libharfbuzz0b \
    libffi-dev \
    fonts-dejavu \
    && rm -rf /var/lib/apt/lists/*

RUN adduser --disabled-password --gecos '' appuser

WORKDIR /app

COPY --chown=appuser:appuser . .

RUN pip install --no-cache-dir -r requirements.txt

ENV FLASK_APP=web-select.py
ENV FLASK_ENV=production
ENV DATABASE_PATH=/app/data/books.db
ENV ARCHIVES_PATH=/app/data/archives
ENV HOST=0.0.0.0
ENV PORT=5000

VOLUME /app/data

EXPOSE 5000

USER appuser

CMD ["sh", "-c", "flask run --host=$HOST --port=$PORT"]