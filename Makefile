.PHONY: lint format fix check typecheck imports all

all: fix check

lint:
	uvx ruff check .

format:
	uvx ruff format .

fix:
	uvx ruff check --fix .
	uvx ruff format .

typecheck:
	uvx pyright .

check:
	uvx ruff check .
	uvx ruff format --check .
	uvx pyright .

imports:
	@echo "Checking for circular imports..."
	@../../.venv/bin/python -c "import specter; print('All imports OK')"
