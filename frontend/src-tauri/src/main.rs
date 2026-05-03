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
use std::sync::Mutex;
use std::thread;
use tauri::{Manager, RunEvent, Runtime, WindowEvent};

struct FlaskHandle(Mutex<Option<Child>>);

fn spawn_flask() -> std::io::Result<Child> {
    let mut child = Command::new("maiko")
        .args(["serve"])
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

// Kill the Flask child if it's still alive. Generic over Manager so
// we can call it from both window events (passing the &Window) and
// the app-level run callback (passing the &AppHandle).
fn kill_flask<R: Runtime, M: Manager<R>>(manager: &M) {
    let state = manager.state::<FlaskHandle>();
    if let Some(mut child) = state.0.lock().unwrap().take() {
        let _ = child.kill();
        let _ = child.wait();
    }
}

fn main() {
    let flask = spawn_flask().expect(
        "Failed to start `maiko serve`. Is the maiko CLI installed and on PATH? \
         From the repo root: `pip install -e .`",
    );

    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|_, _, _| {
            // Second launch focuses the existing window — no-op here
            // because the default window manager already raises us.
        }))
        .manage(FlaskHandle(Mutex::new(Some(flask))))
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { .. } = event {
                kill_flask(window);
            }
        })
        .build(tauri::generate_context!())
        .expect("error building Maiko Tauri shell");

    // ExitRequested catches Cmd+Q on Mac, Quit menu items, and the
    // implicit "all windows closed → app exits" path. CloseRequested
    // alone misses these and Flask gets orphaned.
    app.run(|app_handle, event| {
        if let RunEvent::ExitRequested { .. } = event {
            kill_flask(app_handle);
        }
    });
}
