//! CHROMA Console — Tauri shell (BUILD_ORDER §4, M2).
//!
//! - Supervises / attaches to chromad over its Unix socket (supervisor.rs, ipc.rs).
//! - `chromad_request` proxies one request from the UI; events arrive as
//!   `chromad://event`, connection changes as `chromad://status`.
//! - Tray and the Chroma app menu: Devices → Fake | Real (devices.rs) switches the
//!   engine between fake devices (~/.chroma/dev) and the real ones (~/.chroma); saved.
//! - Tray (and the CHROMA app menu): Force Update (force_update.rs) quits, ends every
//!   CHROMA process, stops `lite show` and relaunches the latest dev version.
//! - Tray: status, Open, Restart engine, Quit. Closing the window only hides it;
//!   the engine keeps running. Quit (tray or ⌘Q) stops an engine the app spawned.
//! - `chroma-art://` serves the current artwork (art.rs).

mod art;
mod config;
mod devices;
mod force_update;
mod ipc;
mod supervisor;

use ipc::EventSink;
use serde_json::Value;
use std::sync::Arc;
use supervisor::{EngineStatus, StatusSink, Supervisor};
use devices::{DeviceModes, Devices};
use std::path::PathBuf;
use std::sync::Mutex as StdMutex;
use tauri::menu::{CheckMenuItem, Menu, MenuItem, MenuItemKind, PredefinedMenuItem, Submenu};
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
    modes: DeviceModes,
    choice_path: PathBuf,
    user_home: PathBuf,
    /// the device mode last asked for (menu clicks are serialized by `switch_lock`)
    target: StdMutex<Devices>,
    switch_lock: tokio::sync::Mutex<()>,
    checks: StdMutex<Vec<(CheckMenuItem<Wry>, Devices)>>,
}

fn toast(app: &AppHandle, level: &str, text: impl Into<String>) {
    let _ = app.emit("chromad://event", serde_json::json!({"ev": "toast", "level": level, "text": text.into()}));
}

fn devices_submenu(app: &tauri::App, current: Devices) -> tauri::Result<(Submenu<Wry>, Vec<(CheckMenuItem<Wry>, Devices)>)> {
    let fake = CheckMenuItem::with_id(app, "devices_fake", "Fake", true, current == Devices::Fake, None::<&str>)?;
    let real = CheckMenuItem::with_id(app, "devices_real", "Real", true, current == Devices::Real, None::<&str>)?;
    let sub = Submenu::with_items(app, "Devices", true, &[&fake, &real])?;
    Ok((sub, vec![(fake, Devices::Fake), (real, Devices::Real)]))
}

fn sync_checks(shell: &Shell, mode: Devices) {
    for (item, m) in shell.checks.lock().unwrap().iter() {
        let _ = item.set_checked(*m == mode);
    }
}

/// Chroma → Devices → Fake | Real.
fn set_devices(app: &AppHandle, mode: Devices) {
    let shell = app.state::<Shell>();
    {
        let mut target = shell.target.lock().unwrap();
        sync_checks(&shell, mode); // a click toggles the check itself: always show the truth
        if *target == mode {
            return;
        }
        *target = mode;
    }
    if let Err(e) = devices::save_choice(&shell.choice_path, mode) {
        eprintln!("[chroma] could not save the device choice: {e}");
    }
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        let shell = app.state::<Shell>();
        let _one_at_a_time = shell.switch_lock.lock().await;
        if *shell.target.lock().unwrap() != mode {
            return; // a newer click wins
        }
        if mode == Devices::Real {
            let home = shell.user_home.clone();
            match tauri::async_runtime::spawn_blocking(move || devices::stop_lite_show(&home)).await {
                Ok(Ok(true)) => toast(&app, "info", "Stopped lite show: the app drives the real lights now"),
                Ok(Err(e)) => toast(&app, "warn", e),
                _ => {}
            }
        }
        let new = shell.modes.config_for(mode);
        let home = new.home.display().to_string();
        shell.sup.switch(new).await;
        let what = if mode == Devices::Fake { "Fake devices (test)" } else { "Real devices" };
        toast(&app, "info", format!("{what}: engine in {home}"));
    });
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

/// Take export (Takes screen): `filename` reduced to a safe basename, written into
/// ~/Downloads without overwriting (" 2", " 3", … before the extension). Returns the path.
#[tauri::command]
fn save_export(filename: String, text: String) -> Result<String, String> {
    let home = std::env::var_os("HOME").ok_or("HOME is not set")?;
    let dir = std::path::Path::new(&home).join("Downloads");
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let path = export_path(&dir, &filename);
    std::fs::write(&path, text).map_err(|e| e.to_string())?;
    Ok(path.to_string_lossy().into_owned())
}

fn export_path(dir: &std::path::Path, filename: &str) -> std::path::PathBuf {
    let base: String = filename
        .rsplit('/')
        .next()
        .unwrap_or("")
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-'))
        .collect();
    let base = base.trim_start_matches('.');
    let base = if base.is_empty() { "take.jsonl" } else { base };
    let (stem, ext) = match base.rfind('.') {
        Some(i) if i > 0 => (&base[..i], &base[i..]),
        _ => (base, ""),
    };
    let mut path = dir.join(base);
    let mut n = 2;
    while path.exists() {
        path = dir.join(format!("{stem} {n}{ext}"));
        n += 1;
    }
    path
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

/// CHROMA → Force Update: start the detached helper, then quit like Quit does.
fn force_update(app: &AppHandle) {
    // a tray click may reach both the tray and the app-wide menu handler: run once
    static STARTED: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
    if STARTED.swap(true, std::sync::atomic::Ordering::SeqCst) {
        return;
    }
    let repo_root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let checkout = force_update::dev_checkout(&repo_root);
    let log = force_update::log_path();
    eprintln!("[chroma] force update: relaunching from {} (log {})", checkout.display(), log.display());
    // nothing is ended unless the relaunch can work
    let started = force_update::preflight(&checkout)
        .map_err(|e| e.to_string())
        .and_then(|()| force_update::launch(std::process::id(), &checkout, &log).map_err(|e| e.to_string()));
    match started {
        Ok(()) => quit(app),
        Err(e) => {
            STARTED.store(false, std::sync::atomic::Ordering::SeqCst);
            let _ = app.emit("chromad://event", serde_json::json!({
                "ev": "toast", "level": "error", "text": format!("Force Update could not start: {e}")}));
        }
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
        // let the UI fetch state (CHROMA_SELFTEST_HOLD_S: longer, to drive the engine meanwhile)
        let hold = std::env::var("CHROMA_SELFTEST_HOLD_S").ok().and_then(|v| v.parse().ok()).unwrap_or(3u64);
        tokio::time::sleep(Duration::from_secs(hold)).await;
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
    let repo_root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let repo_root = repo_root.canonicalize().unwrap_or(repo_root);
    let user_home = std::env::var_os("HOME").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("/"));
    let modes = DeviceModes::new(|k| std::env::var(k).ok(), cfg!(debug_assertions), &repo_root, &user_home);
    let env_pinned = std::env::var_os("CHROMA_HOME").is_some() || std::env::var_os("CHROMAD_ARGS").is_some();
    tauri::Builder::default()
        .register_uri_scheme_protocol("chroma-art", {
            move |ctx, _req| {
                // the current engine's home (it changes with Devices → Fake | Real)
                let art_path = ctx.app_handle().try_state::<Shell>().map(|s| s.sup.cfg().artwork()).unwrap_or_default();
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
            let choice_path = app.path().app_config_dir()?.join("devices.json");
            let cfg = devices::initial(&modes, env_pinned, devices::load_choice(&choice_path));
            let current = Devices::of(&cfg);
            let (tray_devices, mut checks) = devices_submenu(app, current)?;
            let status_item = MenuItem::with_id(app, "status", "Engine: connecting…", false, None::<&str>)?;
            let menu = Menu::with_items(
                app,
                &[
                    &status_item,
                    &PredefinedMenuItem::separator(app)?,
                    &MenuItem::with_id(app, "open", "Open CHROMA", true, None::<&str>)?,
                    &MenuItem::with_id(app, "restart", "Restart engine", true, None::<&str>)?,
                    &tray_devices,
                    &PredefinedMenuItem::separator(app)?,
                    &MenuItem::with_id(app, "force_update", "Force Update", true, None::<&str>)?,
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
                    "force_update" => force_update(app),
                    "devices_fake" => set_devices(app, Devices::Fake),
                    "devices_real" => set_devices(app, Devices::Real),
                    "quit" => quit(app),
                    _ => {}
                })
                .build(app)?;

            // the same item in the app menu (Chroma → Force Update), after "About"
            let app_menu = Menu::default(app.handle())?;
            if let Some(MenuItemKind::Submenu(first)) = app_menu.items()?.into_iter().next() {
                let (app_devices, more) = devices_submenu(app, current)?;
                checks.extend(more);
                first.insert(&PredefinedMenuItem::separator(app)?, 1)?;
                first.insert(&app_devices, 2)?;
                first.insert(&MenuItem::with_id(app, "force_update", "Force Update", true, None::<&str>)?, 3)?;
            }
            app.set_menu(app_menu)?;
            app.on_menu_event(|app, event| match event.id.as_ref() {
                "force_update" => force_update(app),
                "devices_fake" => set_devices(app, Devices::Fake),
                "devices_real" => set_devices(app, Devices::Real),
                _ => {}
            });

            let sup = Supervisor::new(
                cfg.clone(),
                Arc::new(UiEvents(handle.clone())),
                Arc::new(UiStatus { app: handle.clone(), item: status_item }),
            );
            app.manage(Shell {
                sup: sup.clone(),
                modes: modes.clone(),
                choice_path,
                user_home: user_home.clone(),
                target: StdMutex::new(current),
                switch_lock: tokio::sync::Mutex::new(()),
                checks: StdMutex::new(checks),
            });
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
        .invoke_handler(tauri::generate_handler![chromad_request, engine_status, save_export])
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

#[cfg(test)]
mod export_tests {
    use super::export_path;

    #[test]
    fn export_path_is_a_safe_basename_and_never_overwrites() {
        let dir = std::env::temp_dir().join(format!("chroma-export-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let p = export_path(&dir, "../../etc/20261009-214800_older.jsonl");
        assert_eq!(p, dir.join("20261009-214800_older.jsonl"));
        std::fs::write(&p, "x").unwrap();
        assert_eq!(export_path(&dir, "20261009-214800_older.jsonl"), dir.join("20261009-214800_older 2.jsonl"));
        assert_eq!(export_path(&dir, "..."), dir.join("take.jsonl"));
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
