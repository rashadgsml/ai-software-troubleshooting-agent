.PHONY: install test up down demo eval rescore

install:        ## create .venv and install dependencies
	python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

test:           ## free test suite (needs `make up` for the Postgres-backed tests)
	.venv/bin/pytest -q

up:             ## start Postgres + API (migrations run on start)
	docker compose up -d --build

down:
	docker compose down

demo:           ## live end-to-end demo against the running API (a few cents). Needs API_KEY exported
	scripts/demo.sh $(SCENARIO)

eval:           ## full real-API eval, 5 runs per scenario (about $1)
	.venv/bin/python -m evals.run -n 5

rescore:        ## re-score a saved run for free: make rescore LABEL=baseline-sonnet-5-5
	.venv/bin/python -m evals.rescore $(LABEL)
