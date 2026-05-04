// Planet Maiko desktop shell. Tauri opens a window pointing at the
// existing React frontend (Vite in dev, the Flask static build in
// release) and spawns `maiko serve` as a child process so the user
// gets a single-icon launch instead of two terminals.
//
// Lifecycle:
//   - main() resolves the absolute path to `maiko` via the user's
//     login shell, then spawns it directly. Finder-launched apps
//     inherit a minimal /usr/bin:/bin PATH that doesn't include
//     ~/.local/bin / homebrew / pyenv shims / venvs — running through
//     the login shell once to get the path lets every standard pip
//     install location work without us hardcoding any of them.
//   - If maiko isn't found we log a clear error and let Tauri open
//     anyway. Better to show the user a window with broken API
//     calls than to crash silently before the window appears.
//   - stdout / stderr from Flask are piped to a logfile in
//     ~/Library/Logs (Mac) or ~/.cache (Linux) so Finder launches
//     leave a trace — Finder apps have no terminal stderr.
//   - kill_flask runs from BOTH WindowEvent::CloseRequested (red X)
//     AND RunEvent::ExitRequested (Cmd+Q on Mac, Quit menu, all
//     windows closed). On Mac, Cmd+Q skips per-window events and
//     goes straight to the app-level event.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::fs::OpenOptions;
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use tauri::{RunEvent, WindowEvent};

// Shared handle on the Flask child. Cloned into each close closure —
// no Tauri state plumbing or Manager-trait generics needed.
type FlaskHandle = Arc<Mutex<Option<Child>>>;

// Where to write Flask stdout / stderr so Finder-launched failures
// leave a trace. ~/Library/Logs is Apple's blessed log dir; on
// Linux ~/.cache/planet-maiko serves the same role.
fn log_path() -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| ".".to_string());
    if cfg!(target_os = "macos") {
        PathBuf::from(format!("{}/Library/Logs/planet-maiko-tauri.log", home))
    } else if cfg!(target_os = "windows") {
        let appdata = std::env::var("LOCALAPPDATA").unwrap_or_else(|_| home.clone());
        PathBuf::from(format!("{}\\planet-maiko\\tauri.log", appdata))
    } else {
        PathBuf::from(format!("{}/.cache/planet-maiko-tauri.log", home))
    }
}

fn log_line(msg: &str) {
    let path = log_path();
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(&path) {
        let _ = writeln!(f, "{}", msg);
    }
    eprintln!("{}", msg);
}

// Resolve the absolute path to `maiko` by asking the user's login
// shell (Mac/Linux) — this loads .zshrc/.zshenv/.bashrc so any PATH
// edits the user has (~/.local/bin, /opt/homebrew/bin, pyenv shims,
// venv bins) get picked up. On Windows we trust the registry-level
// user PATH that Finder/Explorer launches inherit by default.
#[cfg(any(target_os = "macos", target_os = "linux"))]
fn resolve_maiko_path() -> std::io::Result<PathBuf> {
    let shell = std::env::var("SHELL").unwrap_or_else(|_| "/bin/zsh".to_string());
    let output = Command::new(&shell)
        .args(["-l", "-c", "command -v maiko"])
        .output()?;
    if !output.status.success() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::NotFound,
            format!(
                "`maiko` not found in {}'s login PATH. Install with `pip install --user -e .` from the planet-maiko repo, or make sure your venv's bin dir is on PATH in ~/.zshrc.",
                shell
            ),
        ));
    }
    let path_str = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if path_str.is_empty() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::NotFound,
            "`command -v maiko` returned empty in login shell".to_string(),
        ));
    }
    Ok(PathBuf::from(path_str))
}

#[cfg(target_os = "windows")]
fn resolve_maiko_path() -> std::io::Result<PathBuf> {
    Ok(PathBuf::from("maiko"))
}

fn spawn_flask() -> std::io::Result<Child> {
    let maiko = resolve_maiko_path()?;
    log_line(&format!("[maiko] Resolved maiko binary: {}", maiko.display()));

    let mut child = Command::new(&maiko)
        .arg("serve")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;

    // Drain stdout / stderr to the log file so a Flask crash isn't
    // invisible to a Finder-launched user. Detached threads — they
    // exit when the pipes close on Flask exit.
    let log_for_stdout = log_path();
    if let Some(stdout) = child.stdout.take() {
        thread::spawn(move || {
            let mut log = OpenOptions::new()
                .create(true)
                .append(true)
                .open(&log_for_stdout)
                .ok();
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                eprintln!("[flask] {}", line);
                if let Some(f) = log.as_mut() {
                    let _ = writeln!(f, "[flask] {}", line);
                }
            }
        });
    }
    let log_for_stderr = log_path();
    if let Some(stderr) = child.stderr.take() {
        thread::spawn(move || {
            let mut log = OpenOptions::new()
                .create(true)
                .append(true)
                .open(&log_for_stderr)
                .ok();
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                eprintln!("[flask] {}", line);
                if let Some(f) = log.as_mut() {
                    let _ = writeln!(f, "[flask] {}", line);
                }
            }
        });
    }
    Ok(child)
}

fn kill_flask(handle: &FlaskHandle) {
    if let Some(mut child) = handle.lock().unwrap().take() {
        let _ = child.kill();
        let _ = child.wait();
    }
}

fn main() {
    log_line(&format!(
        "[maiko] Starting Tauri shell, log file: {}",
        log_path().display()
    ));

    // Try to spawn Flask. On failure, log the error and let Tauri
    // open the window anyway — the user sees a clear "API down"
    // state instead of a silent crash before the window appears,
    // and the log path is in our startup line for them to tail.
    let flask = match spawn_flask() {
        Ok(child) => Some(child),
        Err(e) => {
            log_line(&format!("[maiko] Failed to start Flask: {}", e));
            None
        }
    };

    let flask_handle: FlaskHandle = Arc::new(Mutex::new(flask));
    let close_handle = flask_handle.clone();
    let exit_handle = flask_handle.clone();

    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|_, _, _| {
            // Second launch focuses the existing window — no-op here
            // because the default window manager already raises us.
        }))
        .on_window_event(move |_window, event| {
            if let WindowEvent::CloseRequested { .. } = event {
                kill_flask(&close_handle);
            }
        })
        .build(tauri::generate_context!())
        .expect("error building Maiko Tauri shell");

    app.run(move |_app_handle, event| {
        if let RunEvent::ExitRequested { .. } = event {
            kill_flask(&exit_handle);
        }
    });
}
