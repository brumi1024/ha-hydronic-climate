UV ?= uv
RUFF_FORMAT_LEGACY := custom_components/hydronic_climate/config_flow.py,custom_components/hydronic_climate/core/controller.py,custom_components/hydronic_climate/core/topology.py,custom_components/hydronic_climate/entry_configuration.py,custom_components/hydronic_climate/runtime.py,tests/core/test_controller.py,tests/integration/test_circuit_subentry.py,tests/integration/test_config_flow.py,tests/integration/test_zone_subentry.py,tests/test_runtime.py

.PHONY: bootstrap hooks lint format-check typecheck test-core test-integration test-scenarios test verify

bootstrap:
	$(UV) sync --frozen --extra test

hooks: bootstrap
	$(UV) run pre-commit install

lint:
	$(UV) run ruff check .
	$(UV) run python -m compileall -q custom_components tests
	$(UV) run python -m json.tool hacs.json >/dev/null
	$(UV) run python -m json.tool custom_components/hydronic_climate/manifest.json >/dev/null
	$(UV) run python -m json.tool custom_components/hydronic_climate/strings.json >/dev/null
	$(UV) run python -m json.tool custom_components/hydronic_climate/translations/en.json >/dev/null

format-check:
	$(UV) run ruff format --check --exclude "$(RUFF_FORMAT_LEGACY)" .

typecheck:
	$(UV) run mypy custom_components/hydronic_climate/core

test-core:
	$(UV) run pytest tests/core --cov=custom_components/hydronic_climate/core --cov-report=term-missing

test-integration:
	$(UV) run pytest tests/integration

test-scenarios:
	$(UV) run pytest tests/scenarios

test:
	$(UV) run pytest --cov=custom_components/hydronic_climate/core --cov-report=term-missing

verify: lint format-check typecheck test
