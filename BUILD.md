# Building the Planet Maiko desktop app

> **Status: experimental, currently buggy, parked.** The supported
> launch is the terminal flow: `maiko up` (see the README). This file
> is kept for whenever the desktop shell gets picked back up.

For local use on **macOS only**. This produces a normal double-click
app. It is a thin window shell: it runs `maiko serve` (the Python
backend) as a child process, so the backend has to be installed and
the `maiko` command has to be findable.

## One-time prerequisites

- Xcode command line tools: `xcode-select --install`
- Rust toolchain: install via rustup (rustup.rs)
- Node 18+
- pipx: `brew install pipx && pipx ensurepath`

## 1. Install the backend

```
make backend
```

This `pipx install`s the `maiko` CLI into `~/.local/bin`. pipx is
preferred over a bare venv because it gives a stable, project
independent path for the command.

The catch: the app launches `maiko` through the **login** shell
(`zsh -l`), which reads `~/.zprofile` and `~/.zshenv` but **not**
`~/.zshrc`. `pipx ensurepath` only adds `~/.local/bin` to the
*interactive* config (`~/.zshrc`), so the app still would not see it.
Add this line to `~/.zprofile` once:

```
export PATH="$HOME/.local/bin:$PATH"
```

(If you use a venv instead of pipx, put that venv's `bin` directory
on `~/.zprofile` the same way.)

Open a new terminal, then sanity check the exact way the app resolves
it: `zsh -lc 'which maiko'` should print a path.

## 2. Build the app

```
make app
```

Artifacts land in:

- `frontend/src-tauri/target/release/bundle/macos/Planet Maiko.app`
- `frontend/src-tauri/target/release/bundle/dmg/` (a `.dmg` too)

Drag `Planet Maiko.app` to `/Applications`. It then shows in
Launchpad and Spotlight; drag it to the Dock to pin it.

First launch is unsigned, so macOS Gatekeeper blocks it once:
right-click the app and choose **Open**, or run
`xattr -dr com.apple.quarantine "/Applications/Planet Maiko.app"`.

## Dev shell

```
make dev
```

Runs the desktop window against the Vite dev server with hot reload.

## If the window opens but everything is dead

That means the app could not start `maiko serve`. It is almost always
PATH: the app uses the login shell, so confirm
`zsh -lc 'which maiko'` works. The Flask output is teed to
`~/Library/Logs/planet-maiko-tauri.log`; tail that to see why.
