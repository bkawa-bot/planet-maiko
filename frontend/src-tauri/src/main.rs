// Planet Maiko desktop shell. Tauri opens a window pointing at the
// existing React frontend (Vite in dev, the Flask static build in
// release) and spawns `maiko serve` as a child process so the user
// gets a single-icon launch instead of two terminals.
//
// Lifecycle:
//   - main() spawns Flask before the Tauri builder runs. If the
//     spawn fails (maiko CLI missing), we panic with a clear message
//     instead of silently opening an empty window.
//   - stdout / stderr are piped into the Tauri shell's own stderr so
//     a Flask crash isn't invisible to whoever is debugging.
//   - kill_flask runs from BOTH WindowEvent::CloseRequested (red X)
//     AND RunEvent::ExitRequested (Cmd+Q on Mac, Quit menu, all
//     windows closed). On Mac, Cmd+Q skips per-window events and
//     goes straight to the app-level event — without ExitRequested
//     handling Flask gets orphaned and port 8420 stays bound until
//     the next reboot or manual `kill -9`.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use tauri::{RunEvent, WindowEvent};

// Shared handle on the Flask child. We share it across the two close
// paths (window event + app-level RunEvent) by cloning the Arc into
// each move closure — no Tauri state plumbing, no Manager-trait
// generics, no lifetime jousting with the borrow checker.
type FlaskHandle = Arc<Mutex<Option<Child>>>;

// Build the command that launches Flask. On Mac/Linux we route
// through the user's login shell so .zshrc/.bashrc PATH edits load
// — Finder-launched apps inherit a minimal /usr/bin:/bin PATH that
// doesn't include ~/.local/bin or /opt/homebrew/bin (where pip
// typically drops the `maiko` script). Windows inherits the user
// PATH directly from the registry, so the simple form is enough.
#[cfg(any(target_os = "macos", target_os = "linux"))]
fn build_maiko_command() -> Command {
    // $SHELL is set by the OS login session. Falling back to /bin/zsh
    // covers the Catalina+ default; users on bash/fish inherit their
    // own login shell via the env var.
    let shell = std::env::var("SHELL").unwrap_or_else(|_| "/bin/zsh".to_string());
    let mut cmd = Command::new(shell);
    cmd.args(["-l", "-c", "maiko serve"]);
    cmd
}

#[cfg(target_os = "windows")]
fn build_maiko_command() -> Command {
    let mut cmd = Command::new("maiko");
    cmd.arg("serve");
    cmd
}

fn spawn_flask() -> std::io::Result<Child> {
    let mut child = build_maiko_command()
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;

    // Drain stdout / stderr so a Flask crash leaves a trace in the
    // shell logs. Detached threads — they exit when the pipes close
    // on Flask exit.
    if let Some(stdout) = child.stdout.take() {
        thread::spawn(move || {
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                eprintln!("[flask] {}", line);
            }
        });
    }
    if let Some(stderr) = child.stderr.take() {
        thread::spawn(move || {
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                eprintln!("[flask] {}", line);
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
    let flask = spawn_flask().expect(
        "Failed to start `maiko serve`. Is the maiko CLI installed and on PATH? \
         From the repo root: `pip install -e .`",
    );

    // One canonical handle, two clones — one for each close path. The
    // first call wins; the other finds None and no-ops.
    let flask_handle: FlaskHandle = Arc::new(Mutex::new(Some(flask)));
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

    // ExitRequested catches Cmd+Q on Mac, Quit menu items, and the
    // implicit "all windows closed → app exits" path. CloseRequested
    // alone misses these and Flask gets orphaned.
    app.run(move |_app_handle, event| {
        if let RunEvent::ExitRequested { .. } = event {
            kill_flask(&exit_handle);
        }
    });
}
