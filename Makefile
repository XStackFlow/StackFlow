.PHONY: start_editor start_api sync_memory

SHELL := /bin/bash

# Variables
VENV = venv
PYTHON = $(VENV)/bin/python
PIP = $(VENV)/bin/pip


start_editor:
	@echo "Starting LiteGraph visual editor..."
	@cd litegraph-editor && npm run dev

start_api:
	@echo "Starting API server..."
	@PYTHONPATH=$$(pwd):$$PYTHONPATH $(PYTHON) src/api_server.py

sync_memory:
	@echo "Syncing memory to Vector DB..."
	@PYTHONPATH=$$(pwd):$$PYTHONPATH $(PYTHON) src/utils/manager/memory_manager.py

# Catch-all target to prevent make errors when passing script paths as arguments
%:
	@: 
