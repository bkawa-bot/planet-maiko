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
//   - On CloseRequested we kill + reap the Flask child so it doesn't
//     dangle when the window disappears.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use tauri::{Manager, WindowEvent};

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

fn main() {
    let flask = spawn_flask().expect(
        "Failed to start `maiko serve`. Is the maiko CLI installed and on PATH? \
         From the repo root: `pip install -e .`",
    );

    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|_, _, _| {
            // Second launch focuses the existing window — no-op here
            // because the default window manager already raises us.
        }))
        .manage(FlaskHandle(Mutex::new(Some(flask))))
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { .. } = event {
                let state = window.app_handle().state::<FlaskHandle>();
                if let Some(mut child) = state.0.lock().unwrap().take() {
                    let _ = child.kill();
                    let _ = child.wait();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running Maiko Tauri shell");
}
