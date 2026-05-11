PYTHON ?= python3

.PHONY: all validate clean figures

all: validate figures

validate:
	@echo "[make] Validating dataset..."
	$(PYTHON) scripts/validate_dataset.py

figures:
	@echo "[make] Generating figures from experiment results..."
	$(PYTHON) llm_experiments/scripts/visualize.py
	$(PYTHON) llm_experiments/scripts/dataset_stats.py

clean:
	@echo "[make] Clean completed (nothing to clean)"
	@echo "[make] Done"
