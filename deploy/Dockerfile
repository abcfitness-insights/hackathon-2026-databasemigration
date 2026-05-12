FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src

RUN pip install --upgrade pip && pip install -e .

EXPOSE 8080
ENV PORT=8080

CMD ["uvicorn", "migguard.bot.app:app", "--host", "0.0.0.0", "--port", "8080"]
