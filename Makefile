UV ?= uv

.PHONY: bootstrap hooks lint typecheck test-core test-integration test-scenarios test verify

bootstrap:
	$(UV) sync --extra test

hooks: bootstrap
	$(UV) run pre-commit install

lint:
	$(UV) run ruff check .
	$(UV) run python -m compileall -q custom_components tests
	$(UV) run python -m json.tool hacs.json >/dev/null
	$(UV) run python -m json.tool custom_components/hydronic_climate/manifest.json >/dev/null
	$(UV) run python -m json.tool custom_components/hydronic_climate/strings.json >/dev/null
	$(UV) run python -m json.tool custom_components/hydronic_climate/translations/en.json >/dev/null

typecheck:
	$(UV) run mypy custom_components/hydronic_climate/core

test-core:
	$(UV) run pytest tests/core --cov=custom_components/hydronic_climate/core --cov-report=term-missing

test-integration:
	$(UV) run pytest tests/integration tests/test_config_flow_import.py tests/test_runtime.py

test-scenarios:
	$(UV) run pytest tests/scenarios

test:
	$(UV) run pytest --cov=custom_components/hydronic_climate/core --cov-report=term-missing

verify: lint typecheck test
