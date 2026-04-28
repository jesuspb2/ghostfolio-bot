FROM python:3.12-slim

WORKDIR /app

RUN pip install uv

COPY pyproject.toml .
RUN uv sync --no-dev

COPY bot/ bot/

CMD ["uv", "run", "ghostfolio-bot"]
