PYTHON ?= python
CONTRACT ?= contracts/default.yaml
OUT ?= out/demo
RUN_ID ?= demo-001

.PHONY: demo test fixtures-check fixtures console clean

demo:
	$(PYTHON) tools/generate_fixtures.py --check
	$(PYTHON) -m tzgate verify --contract $(CONTRACT) --root . --out $(OUT) --run-id $(RUN_ID)
	$(PYTHON) -m tzgate summary --out $(OUT)

test:
	$(PYTHON) -m pytest

fixtures-check:
	$(PYTHON) tools/generate_fixtures.py --check

fixtures:
	$(PYTHON) tools/generate_fixtures.py --out fixtures/lake

console:
	$(PYTHON) -m tzgate console --out $(OUT) --port 0

clean:
	$(PYTHON) -c "import shutil; [shutil.rmtree(p, ignore_errors=True) for p in ('out', '.pytest_cache', '.tmp')]"
