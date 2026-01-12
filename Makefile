.PHONY: lint format fix check typecheck imports all

all: fix check

lint:
	uvx ruff check .

format:
	uvx ruff format .

fix:
	uvx ruff check --fix .

typecheck:
	uvx pyright .

check:
	uvx ruff check .
	uvx pyright .

imports:
	@echo "Checking for circular imports..."
	@../../.venv/bin/python -c "import specter; print('All imports OK')"
