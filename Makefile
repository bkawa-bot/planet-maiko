# Planet Maiko. The supported launch is the terminal flow:
#   make run   ->  maiko up  (backend + frontend + opens the browser)
# The Tauri desktop targets below are experimental and parked
# (currently buggy). See BUILD.md.

.DEFAULT_GOAL := help

help:
	@echo "Planet Maiko:"
	@echo "  make install    venv + backend + frontend deps"
	@echo "  make run        start backend + frontend, open the browser (maiko up)"
	@echo ""
	@echo "  make app        (experimental, parked) build the Tauri desktop app"
	@echo "  make tauri-dev  (experimental) Tauri shell with hot reload"

install:
	python3 -m venv .venv
	. .venv/bin/activate && pip install -e .
	cd frontend && npm install
	@echo "Done. Activate the venv, then: maiko up"

run:
	maiko up

# --- experimental / parked: the Tauri desktop shell. Buggy. See BUILD.md.
app:
	cd frontend && npm install && npm run tauri:build
	@echo "Built: frontend/src-tauri/target/release/bundle/macos/Planet Maiko.app"

tauri-dev:
	cd frontend && npm install && npm run tauri:dev

.PHONY: help install run app tauri-dev
