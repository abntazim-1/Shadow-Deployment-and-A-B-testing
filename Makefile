.PHONY: dev worker test lint

dev:
	uvicorn src.main:app --reload

worker:
	python -m src.evaluation.evaluator

test:
	pytest tests/ -v

lint:
	ruff check src/ && mypy src/
