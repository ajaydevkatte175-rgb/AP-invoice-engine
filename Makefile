.PHONY: setup up down migrate seed run worker ui test eval lint

setup:
	uv sync

up:
	docker compose up -d

down:
	docker compose down

migrate:
	uv run alembic upgrade head

seed:
	uv run python -m scripts.seed

run:
	uv run uvicorn app.main:app --reload --port 8000

worker:
	uv run arq app.workers.worker.WorkerSettings

ui:
	uv run streamlit run ui/app.py

test:
	uv run pytest tests/

eval:
	uv run python -m scripts.eval

lint:
	uv run ruff check .
	uv run ruff format --check .

