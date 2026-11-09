FROM python:3.13-slim

WORKDIR /srv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY pyproject.toml alembic.ini ./
COPY app ./app
COPY demo_app ./demo_app
COPY migrations ./migrations
RUN pip install --no-cache-dir -e ".[dev]"

EXPOSE 8000
# Apply migrations, then serve. (Single instance: see README on the restart sweep.)
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
