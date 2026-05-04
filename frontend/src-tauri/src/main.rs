// Planet Maiko desktop shell. Tauri opens a window pointing at the
// existing React frontend (Vite in dev, the Flask static build in
// release) and spawns `maiko serve` as a child process so the user
// gets a single-icon launch instead of two terminals.
//
// Lifecycle:
//   - main() spawns Flask via `$SHELL -l -c "exec maiko serve"` on
//     Mac/Linux. Going through the login shell means the child
//     inherits PATH + env from .zprofile / .zshenv (homebrew gh,
//     pyenv shims, venv bins, ANTHROPIC_API_KEY, etc.) instead of
//     Tauri's minimal Finder-inherited env. `exec` drops the shell
//     out of the parent–child chain so kill() still terminates Flask
//     directly. On Windows the registry-level user PATH is already
//     inherited by Finder/Explorer launches, so we spawn maiko directly.
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

// Spawn `maiko serve` through the user's login shell on Mac/Linux so
// the Flask child inherits the full PATH + env (homebrew gh, pyenv
// shims, venv bins, ANTHROPIC_API_KEY, etc.) from .zprofile / .zshenv.
//
// `Command::new("maiko")` directly would inherit Tauri's own env, which
// from a Finder launch is the minimal /usr/bin:/bin set — so things
// like `gh` (typically /opt/homebrew/bin/gh) show up as missing in
// system_health even when `which gh` works in a terminal.
//
// `exec` replaces the shell with the maiko process so the parent–child
// chain is still Tauri → maiko (the shell drops out), keeping kill()
// behavior intact.
//
// `-l` makes it a login shell, which sources .zprofile/.zshenv but
// NOT .zshrc (zsh only reads .zshrc for interactive shells). Users who
// want PATH visible to Tauri must put their exports in .zprofile.
//
// On Windows we trust the registry-level user PATH that Finder/
// Explorer launches inherit by default.
#[cfg(any(target_os = "macos", target_os = "linux"))]
fn spawn_maiko_serve() -> std::io::Result<Child> {
    let shell = std::env::var("SHELL").unwrap_or_else(|_| "/bin/zsh".to_string());
    log_line(&format!("[maiko] Spawning `maiko serve` via {} -l", shell));

    Command::new(&shell)
        .args(["-l", "-c", "exec maiko serve"])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
}

#[cfg(target_os = "windows")]
fn spawn_maiko_serve() -> std::io::Result<Child> {
    log_line("[maiko] Spawning `maiko serve` (using inherited PATH)");
    Command::new("maiko")
        .arg("serve")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
}

fn spawn_flask() -> std::io::Result<Child> {
    let mut child = spawn_maiko_serve()?;

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
