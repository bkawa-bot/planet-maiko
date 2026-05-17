# Planet Maiko build targets. macOS only (the desktop launch target).
# `make app` builds the double-click app; `make backend` puts the
# `maiko` CLI on a stable PATH so the app can find it.

.DEFAULT_GOAL := help

help:
	@echo "Planet Maiko (macOS):"
	@echo "  make backend   pipx-install the maiko CLI (so the .app can find it)"
	@echo "  make app       build the desktop app (.app + .dmg)"
	@echo "  make dev       run the desktop shell with hot reload"
	@echo ""
	@echo "First time: make backend && make app, then drag the .app to /Applications."

# The Tauri shell shells out to `maiko serve`. It does NOT bundle
# Python, so `maiko` must resolve from the login shell (zsh -l reads
# ~/.zprofile / ~/.zshenv, not ~/.zshrc). pipx gives a stable PATH
# both Finder and the login shell see. Prefer this over a bare venv.
backend:
	pipx install --force -e .
	@echo "Installed. Check: zsh -lc 'which maiko'"

# Vite build lands in src/planet_maiko/static (per tauri.conf.json),
# then the Rust shell compiles and bundles.
app:
	cd frontend && npm install && npm run tauri:build
	@echo ""
	@echo "Built: frontend/src-tauri/target/release/bundle/macos/Planet Maiko.app"
	@echo "DMG:   frontend/src-tauri/target/release/bundle/dmg/"
	@echo "Drag the .app to /Applications. First launch: right-click > Open"
	@echo "(unsigned, so Gatekeeper asks once)."

dev:
	cd frontend && npm install && npm run tauri:dev

.PHONY: help backend app dev
