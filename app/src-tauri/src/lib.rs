//! CHROMA Console — Tauri shell (BUILD_ORDER §4, M2).
//!
//! - Supervises / attaches to chromad over its Unix socket (supervisor.rs, ipc.rs).
//! - `chromad_request` proxies one request from the UI; events arrive as
//!   `chromad://event`, connection changes as `chromad://status`.
//! - Tray: status, Open, Restart engine, Quit. Closing the window only hides it;
//!   the engine keeps running. Quit (tray or ⌘Q) stops an engine the app spawned.
//! - `chroma-art://` serves the current artwork (art.rs).

mod art;
mod config;
mod ipc;
mod supervisor;

use config::EngineConfig;
use ipc::EventSink;
use serde_json::Value;
use std::sync::Arc;
use supervisor::{EngineStatus, StatusSink, Supervisor};
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Emitter, Manager, RunEvent, WindowEvent, Wry};

struct UiEvents(AppHandle);
impl EventSink for UiEvents {
    fn event(&self, ev: Value) {
        let _ = self.0.emit("chromad://event", ev);
    }
}

struct UiStatus {
    app: AppHandle,
    item: MenuItem<Wry>,
}
impl StatusSink for UiStatus {
    fn status(&self, s: &EngineStatus) {
        let _ = self.app.emit("chromad://status", s);
        let _ = self.item.set_text(status_line(s));
        if let Some(tray) = self.app.tray_by_id("main") {
            let _ = tray.set_tooltip(Some(format!("CHROMA — {}", status_line(s))));
        }
    }
}

fn status_line(s: &EngineStatus) -> String {
    let who = if s.fake { " (fake devices)" } else { "" };
    match s.phase.as_str() {
        "connected" => format!("Engine: running{who}"),
        "starting" => "Engine: starting…".into(),
        "restarting" => "Engine: restarting…".into(),
        "stopped" => "Engine: stopped".into(),
        "waiting" => "Engine: not running".into(),
        _ => "Engine: connecting…".into(),
    }
}

struct Shell {
    sup: Arc<Supervisor>,
}

#[tauri::command]
async fn chromad_request(shell: tauri::State<'_, Shell>, req: Value) -> Result<Value, String> {
    let op = req.get("op").and_then(Value::as_str).unwrap_or("?").to_string();
    let reply = shell.sup.client.request(req).await;
    if std::env::var_os("CHROMA_DEBUG_IPC").is_some() {
        let ok = reply.as_ref().map(|r| r.get("ok") == Some(&Value::Bool(true))).unwrap_or(false);
        eprintln!("[chroma] ui request {op} -> ok={ok}");
    }
    reply
}

#[tauri::command]
fn engine_status(shell: tauri::State<'_, Shell>) -> EngineStatus {
    shell.sup.status()
}

fn show_main(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
}

fn quit(app: &AppHandle) {
    let app = app.clone();
    let sup = app.state::<Shell>().sup.clone();
    tauri::async_runtime::spawn(async move {
        sup.shutdown().await;
        app.exit(0);
    });
}

/// Debug builds only: CHROMA_SELFTEST=close-quit drives the same code paths as a user
/// closing the window and then choosing Quit, and logs what happened, so the M2
/// lifecycle checks can be verified from a script (no screen access needed).
#[cfg(debug_assertions)]
fn selftest(app: AppHandle, sup: Arc<Supervisor>) {
    if std::env::var("CHROMA_SELFTEST").as_deref() != Ok("close-quit") {
        return;
    }
    tauri::async_runtime::spawn(async move {
        use std::time::Duration;
        for _ in 0..200 {
            if sup.status().connected {
                break;
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
        tokio::time::sleep(Duration::from_secs(3)).await; // let the UI fetch state
        let st = sup.status();
        eprintln!("[selftest] connected={} supervised={} pid={:?}", st.connected, st.supervised, st.pid);
        if let Some(w) = app.get_webview_window("main") {
            let _ = w.close(); // → CloseRequested → hidden, not closed
            tokio::time::sleep(Duration::from_secs(1)).await;
            eprintln!("[selftest] after close: window visible={:?}", w.is_visible().ok());
        }
        let alive = st.pid.map(|p| unsafe { libc::kill(p as i32, 0) } == 0);
        eprintln!("[selftest] after close: engine alive={alive:?} connected={}", sup.status().connected);
        tokio::time::sleep(Duration::from_secs(1)).await;
        eprintln!("[selftest] quitting");
        quit(&app);
    });
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let cfg = EngineConfig::from_env();
    tauri::Builder::default()
        .register_uri_scheme_protocol("chroma-art", {
            let art_path = cfg.artwork();
            move |_ctx, _req| {
                let (status, ctype, body) = art::load(&art_path);
                tauri::http::Response::builder()
                    .status(status)
                    .header("Content-Type", ctype)
                    .header("Cache-Control", "no-store")
                    .header("Access-Control-Allow-Origin", "*")
                    .body(body)
                    .unwrap()
            }
        })
        .setup(move |app| {
            let handle = app.handle().clone();
            let status_item = MenuItem::with_id(app, "status", "Engine: connecting…", false, None::<&str>)?;
            let menu = Menu::with_items(
                app,
                &[
                    &status_item,
                    &PredefinedMenuItem::separator(app)?,
                    &MenuItem::with_id(app, "open", "Open CHROMA", true, None::<&str>)?,
                    &MenuItem::with_id(app, "restart", "Restart engine", true, None::<&str>)?,
                    &PredefinedMenuItem::separator(app)?,
                    &MenuItem::with_id(app, "quit", "Quit CHROMA", true, None::<&str>)?,
                ],
            )?;
            let icon = tauri::image::Image::from_bytes(include_bytes!("../icons/tray-template.png"))?;
            TrayIconBuilder::with_id("main")
                .icon(icon)
                .icon_as_template(true)
                .tooltip("CHROMA")
                .menu(&menu)
                .show_menu_on_left_click(true)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "open" => show_main(app),
                    "restart" => {
                        let sup = app.state::<Shell>().sup.clone();
                        tauri::async_runtime::spawn(async move {
                            sup.restart().await;
                        });
                    }
                    "quit" => quit(app),
                    _ => {}
                })
                .build(app)?;

            let sup = Supervisor::new(
                cfg.clone(),
                Arc::new(UiEvents(handle.clone())),
                Arc::new(UiStatus { app: handle.clone(), item: status_item }),
            );
            app.manage(Shell { sup: sup.clone() });
            tauri::async_runtime::spawn(sup.clone().run());
            #[cfg(debug_assertions)]
            selftest(handle.clone(), sup);
            Ok(())
        })
        .on_window_event(|window, event| {
            // Closing the window hides it: the engine (and the lights) keep running.
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .invoke_handler(tauri::generate_handler![chromad_request, engine_status])
        .build(tauri::generate_context!())
        .expect("error while building the CHROMA app")
        .run(|app, event| match event {
            // ⌘Q / app menu Quit: same as tray Quit (stop an engine we spawned first).
            RunEvent::ExitRequested { code: None, api, .. } => {
                api.prevent_exit();
                quit(app);
            }
            #[cfg(target_os = "macos")]
            RunEvent::Reopen { .. } => show_main(app),
            _ => {}
        });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn status_lines() {
        let mut s = EngineStatus {
            phase: "connected".into(),
            connected: true,
            supervised: true,
            pid: Some(1),
            fake: true,
            message: String::new(),
            socket: String::new(),
        };
        assert_eq!(status_line(&s), "Engine: running (fake devices)");
        s.fake = false;
        assert_eq!(status_line(&s), "Engine: running");
        s.phase = "restarting".into();
        assert_eq!(status_line(&s), "Engine: restarting…");
    }
}
