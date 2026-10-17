//! Keeps the app attached to chromad (BUILD_ORDER §4, M2).
//!
//! Loop: connect to the socket. If nobody is listening and we may supervise,
//! spawn chromad (its own process group, log to $CHROMA_HOME/chromad.log) and
//! retry; if our child dies, back off 1/2/4/8/15 s before respawning. A chromad
//! we did not spawn (LaunchAgent, a terminal) is attached to but never stopped.
//! chromad outlives the window: hiding/closing the window never stops it; only
//! Quit stops a chromad that this app spawned.

use crate::config::EngineConfig;
use crate::ipc::{EventSink, IpcClient};
use serde::Serialize;
use std::fs::OpenOptions;
use std::process::Stdio;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex as StdMutex};
use std::time::{Duration, Instant};
use tokio::net::UnixStream;
use tokio::process::{Child, Command};
use tokio::sync::Mutex;

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct EngineStatus {
    /// "connecting" | "connected" | "starting" | "restarting" | "waiting" | "stopped"
    pub phase: String,
    pub connected: bool,
    /// true when this app spawned the running chromad (Quit will stop it)
    pub supervised: bool,
    pub pid: Option<u32>,
    pub fake: bool,
    pub message: String,
    pub socket: String,
}

pub trait StatusSink: Send + Sync + 'static {
    fn status(&self, s: &EngineStatus);
}

pub fn backoff(attempt: u32) -> Duration {
    const STEPS: [u64; 5] = [1, 2, 4, 8, 15];
    Duration::from_secs(STEPS[(attempt.saturating_sub(1) as usize).min(STEPS.len() - 1)])
}

pub struct Supervisor {
    /// switchable at runtime (Chroma → Devices); read with `cfg()`
    cfg: StdMutex<EngineConfig>,
    /// a device switch is in progress: the loop neither connects nor spawns
    switching: AtomicBool,
    /// held while deciding to spawn + spawning, and while a switch begins: a spawn either
    /// finishes first (and the switch stops it) or sees the switch and skips
    spawn_gate: Mutex<()>,
    /// the next connect/spawn starts a fresh backoff (after a switch)
    fresh: AtomicBool,
    pub client: Arc<IpcClient>,
    child: Mutex<Option<Child>>,
    /// An engine an earlier run of the app spawned (pid file) — still ours to stop.
    adopted: StdMutex<Option<u32>>,
    status: StdMutex<EngineStatus>,
    stopping: AtomicBool,
    events: Arc<dyn EventSink>,
    statuses: Arc<dyn StatusSink>,
}

impl Supervisor {
    pub fn new(cfg: EngineConfig, events: Arc<dyn EventSink>, statuses: Arc<dyn StatusSink>) -> Arc<Self> {
        let status = EngineStatus {
            phase: "connecting".into(),
            connected: false,
            supervised: false,
            pid: None,
            fake: cfg.fake,
            message: String::new(),
            socket: cfg.socket.to_string_lossy().into_owned(),
        };
        Arc::new(Supervisor {
            cfg: StdMutex::new(cfg),
            switching: AtomicBool::new(false),
            spawn_gate: Mutex::new(()),
            fresh: AtomicBool::new(false),
            client: IpcClient::new(),
            child: Mutex::new(None),
            adopted: StdMutex::new(None),
            status: StdMutex::new(status),
            stopping: AtomicBool::new(false),
            events,
            statuses,
        })
    }

    pub fn cfg(&self) -> EngineConfig {
        self.cfg.lock().unwrap().clone()
    }

    pub fn status(&self) -> EngineStatus {
        self.status.lock().unwrap().clone()
    }

    fn set(&self, f: impl FnOnce(&mut EngineStatus)) {
        let snapshot = {
            let mut s = self.status.lock().unwrap();
            let before = s.clone();
            f(&mut s);
            if *s == before {
                return;
            }
            s.clone()
        };
        self.statuses.status(&snapshot);
    }

    /// Is our spawned child still running? Reaps it if it exited.
    async fn child_alive(&self) -> bool {
        let mut guard = self.child.lock().await;
        match guard.as_mut() {
            None => false,
            Some(c) => match c.try_wait() {
                Ok(None) => true,
                _ => {
                    *guard = None;
                    false
                }
            },
        }
    }

    fn pid_file(&self) -> std::path::PathBuf {
        self.cfg().home.join("chromad.app.pid")
    }

    /// The pid this app recorded when it last spawned chromad, if that process is
    /// still alive and is a chromad (`ps` command line), i.e. left behind by a crash
    /// or Ctrl+C of an earlier app run.
    fn previous_spawn(&self) -> Option<u32> {
        let pid: u32 = std::fs::read_to_string(self.pid_file()).ok()?.trim().parse().ok()?;
        if unsafe { libc::kill(pid as i32, 0) } != 0 {
            return None;
        }
        let out = std::process::Command::new("ps").args(["-p", &pid.to_string(), "-o", "command="]).output().ok()?;
        String::from_utf8_lossy(&out.stdout).contains("chromad").then_some(pid)
    }

    fn rotate_log(&self) {
        let log = self.cfg().log_file();
        if std::fs::metadata(&log).map(|m| m.len() > 5 * 1024 * 1024).unwrap_or(false) {
            let _ = std::fs::rename(&log, log.with_extension("log.1"));
        }
    }

    async fn spawn_engine(&self) -> Result<u32, String> {
        let program = self.cfg().program.clone().ok_or("no engine command configured")?;
        std::fs::create_dir_all(&self.cfg().home).map_err(|e| e.to_string())?;
        self.rotate_log();
        let log = OpenOptions::new().create(true).append(true).open(self.cfg().log_file()).map_err(|e| e.to_string())?;
        let log2 = log.try_clone().map_err(|e| e.to_string())?;
        let child = Command::new(&program)
            .args(&self.cfg().args)
            .stdin(Stdio::null())
            .stdout(Stdio::from(log))
            .stderr(Stdio::from(log2))
            .process_group(0) // terminal Ctrl+C / app crash never takes the engine down
            .spawn()
            .map_err(|e| format!("could not start {}: {e}", program.display()))?;
        let pid = child.id().unwrap_or(0);
        *self.child.lock().await = Some(child);
        let _ = std::fs::write(self.pid_file(), pid.to_string());
        Ok(pid)
    }

    pub async fn run(self: Arc<Self>) {
        let mut attempt: u32 = 0;
        while !self.stopping.load(Ordering::SeqCst) {
            if self.switching.load(Ordering::SeqCst) {
                tokio::time::sleep(Duration::from_millis(50)).await;
                continue;
            }
            if self.fresh.swap(false, Ordering::SeqCst) {
                attempt = 0;
            }
            let generation = self.client.generation(); // a switch from here on ends this connection
            match UnixStream::connect(&self.cfg().socket).await {
                Ok(stream) => {
                    let mut supervised = self.child_alive().await;
                    let mut pid = if supervised { self.child.lock().await.as_ref().and_then(|c| c.id()) } else { None };
                    if !supervised {
                        let prev = self.previous_spawn();
                        *self.adopted.lock().unwrap() = prev;
                        if prev.is_some() {
                            supervised = true;
                            pid = prev;
                        }
                    }
                    self.set(|s| {
                        s.phase = "connected".into();
                        s.connected = true;
                        s.supervised = supervised;
                        s.pid = pid;
                        s.message = if supervised { "engine started by the app".into() } else { "attached to a running engine".into() };
                    });
                    let started = Instant::now();
                    self.client.serve_from(stream, self.events.clone(), generation).await;
                    if started.elapsed() > Duration::from_secs(30) {
                        attempt = 0;
                    }
                    if self.stopping.load(Ordering::SeqCst) {
                        break;
                    }
                    if self.switching.load(Ordering::SeqCst) {
                        continue;
                    }
                    self.set(|s| {
                        s.phase = "connecting".into();
                        s.connected = false;
                        s.message = "engine connection lost — reconnecting".into();
                    });
                }
                Err(_) => {
                    if self.cfg().supervise && !self.child_alive().await {
                        if attempt > 0 {
                            let wait = backoff(attempt);
                            self.set(|s| {
                                s.phase = "restarting".into();
                                s.connected = false;
                                s.message = format!("engine exited — restarting in {}s (see {})", wait.as_secs(), self.cfg().log_file().display());
                            });
                            tokio::time::sleep(wait).await;
                            if self.stopping.load(Ordering::SeqCst) {
                                break;
                            }
                        }
                        let gate = self.spawn_gate.lock().await;
                        if self.switching.load(Ordering::SeqCst) {
                            continue; // never spawn the engine we are switching away from
                        }
                        attempt += 1;
                        let spawned = self.spawn_engine().await;
                        drop(gate);
                        match spawned {
                            Ok(pid) => self.set(|s| {
                                s.phase = "starting".into();
                                s.supervised = true;
                                s.pid = Some(pid);
                                s.message = format!("starting engine (pid {pid})");
                            }),
                            Err(e) => self.set(|s| {
                                s.phase = "waiting".into();
                                s.message = e;
                            }),
                        }
                    } else if !self.cfg().supervise {
                        let why = if self.cfg().program.is_none() {
                            "no engine command configured (set CHROMAD_CMD, or start chromad; the bundled engine arrives in M7)"
                        } else {
                            "waiting for chromad (CHROMA_SUPERVISE=0: the app does not start it)"
                        };
                        self.set(|s| {
                            s.phase = "waiting".into();
                            s.connected = false;
                            s.message = why.into();
                        });
                    }
                    tokio::time::sleep(Duration::from_millis(200)).await;
                }
            }
        }
    }

    /// Restart a chromad we spawned (tray: Restart engine). Returns false if we
    /// are only attached to someone else's engine.
    pub async fn restart(&self) -> bool {
        let adopted = *self.adopted.lock().unwrap();
        if !self.child_alive().await && adopted.is_none() {
            self.set(|s| s.message = "Restart engine: this engine was not started by the app — restart it where it runs".into());
            return false;
        }
        self.terminate_child().await;
        true
    }

    /// Chroma → Devices: stop the engine we spawned (an engine someone else started keeps
    /// running), point at `new`, and let the loop connect to / start that one.
    pub async fn switch(&self, new: EngineConfig) {
        {
            let _gate = self.spawn_gate.lock().await;
            self.switching.store(true, Ordering::SeqCst);
        }
        self.client.disconnect();
        self.terminate_child().await; // uses the old home (pid file)
        *self.cfg.lock().unwrap() = new.clone();
        self.fresh.store(true, Ordering::SeqCst);
        let what = if new.fake { "fake" } else { "real" };
        self.set(|s| {
            s.phase = "connecting".into();
            s.connected = false;
            s.supervised = false;
            s.pid = None;
            s.fake = new.fake;
            s.socket = new.socket.to_string_lossy().into_owned();
            s.message = format!("switching to {what} devices ({})", new.home.display());
        });
        self.switching.store(false, Ordering::SeqCst);
    }

    /// Quit: stop reconnecting and stop the engine if (and only if) we spawned it.
    pub async fn shutdown(&self) {
        self.stopping.store(true, Ordering::SeqCst);
        self.terminate_child().await;
        self.set(|s| {
            s.phase = "stopped".into();
            s.connected = false;
        });
    }

    async fn terminate_child(&self) {
        let adopted = self.adopted.lock().unwrap().take();
        if let Some(pid) = adopted {
            unsafe {
                libc::kill(pid as i32, libc::SIGTERM);
            }
            for _ in 0..60 {
                if unsafe { libc::kill(pid as i32, 0) } != 0 {
                    break;
                }
                tokio::time::sleep(Duration::from_millis(50)).await;
            }
            let _ = std::fs::remove_file(self.pid_file());
        }
        let mut guard = self.child.lock().await;
        let Some(child) = guard.as_mut() else { return };
        let _ = std::fs::remove_file(self.pid_file());
        if let Some(pid) = child.id() {
            unsafe {
                libc::kill(pid as i32, libc::SIGTERM);
            }
            let deadline = Instant::now() + Duration::from_secs(3);
            while Instant::now() < deadline {
                if let Ok(Some(_)) = child.try_wait() {
                    *guard = None;
                    return;
                }
                tokio::time::sleep(Duration::from_millis(50)).await;
            }
            let _ = child.kill().await;
        }
        *guard = None;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;
    use std::path::PathBuf;

    #[test]
    fn backoff_steps_then_holds() {
        let s: Vec<u64> = (1..=7).map(|a| backoff(a).as_secs()).collect();
        assert_eq!(s, vec![1, 2, 4, 8, 15, 15, 15]);
        assert_eq!(backoff(0).as_secs(), 1);
    }

    struct Nop;
    impl EventSink for Nop {
        fn event(&self, _: Value) {}
    }
    struct Rec(StdMutex<Vec<EngineStatus>>);
    impl StatusSink for Rec {
        fn status(&self, s: &EngineStatus) {
            self.0.lock().unwrap().push(s.clone());
        }
    }

    fn cfg(home: PathBuf, program: &str, args: Vec<String>) -> EngineConfig {
        EngineConfig {
            socket: home.join("chromad.sock"),
            home,
            program: Some(PathBuf::from(program)),
            args,
            supervise: true,
            fake: true,
        }
    }

    /// A stand-in engine: a shell script that listens on the socket with python's stdlib.
    fn fake_engine_args(sock: &std::path::Path) -> Vec<String> {
        let code = format!(
            "import socket,os,time\np={:?}\nos.path.exists(p) and os.unlink(p)\ns=socket.socket(socket.AF_UNIX)\ns.bind(p)\ns.listen(1)\nc,_=s.accept()\nwhile c.recv(65536): pass\n",
            sock.to_string_lossy()
        );
        vec!["-c".into(), code]
    }

    async fn until(sup: &Supervisor, what: &str, f: impl Fn(&EngineStatus) -> bool) -> EngineStatus {
        let t0 = Instant::now();
        while t0.elapsed() < Duration::from_secs(10) {
            let st = sup.status();
            if f(&st) {
                return st;
            }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
        panic!("timed out waiting for {what}: {:?}", sup.status());
    }

    /// Devices → Fake | Real: our engine is stopped and the other one started; an engine
    /// someone else started is left running when we switch away from it.
    #[tokio::test]
    async fn switching_engines_stops_only_ours_and_starts_the_other() {
        let tmp = |p: &str| tempfile::Builder::new().prefix(p).tempdir_in("/tmp").unwrap();
        let (da, db, dc) = (tmp("cxa"), tmp("cxb"), tmp("cxc"));
        let (sa, sb, sc) = (da.path().join("chromad.sock"), db.path().join("chromad.sock"), dc.path().join("chromad.sock"));
        let sup = Supervisor::new(cfg(da.path().into(), "/usr/bin/python3", fake_engine_args(&sa)), Arc::new(Nop),
            Arc::new(Rec(StdMutex::new(vec![]))));
        let runner = tokio::spawn(sup.clone().run());
        let a = until(&sup, "engine A", |s| s.connected && s.supervised).await.pid.unwrap() as i32;

        let mut b_cfg = cfg(db.path().into(), "/usr/bin/python3", fake_engine_args(&sb));
        b_cfg.fake = false;
        sup.switch(b_cfg).await;
        assert_ne!(unsafe { libc::kill(a, 0) }, 0, "our engine A must be stopped by the switch");
        let st = until(&sup, "engine B", |s| s.connected && s.supervised).await;
        assert!(!st.fake && st.socket.ends_with("chromad.sock") && st.socket.contains(db.path().to_str().unwrap()), "{st:?}");
        let b = st.pid.unwrap() as i32;

        // an external engine on C (keeps accepting): attached, never stopped
        let code = format!("import socket\ns=socket.socket(socket.AF_UNIX)\ns.bind({:?})\ns.listen(4)\n\
            while True:\n  c,_=s.accept()\n  while c.recv(65536): pass\n", sc.to_string_lossy());
        // killed on drop (also when an assert fails) and off our stdout, so a failure never hangs `cargo test`
        struct KillOnDrop(std::process::Child);
        impl Drop for KillOnDrop {
            fn drop(&mut self) {
                let _ = self.0.kill();
                let _ = self.0.wait();
            }
        }
        let mut external = KillOnDrop(std::process::Command::new("/usr/bin/python3").args(["-c", &code])
            .stdout(Stdio::null()).stderr(Stdio::null()).spawn().unwrap());
        let t0 = Instant::now();
        while !sc.exists() && t0.elapsed() < Duration::from_secs(5) {
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
        sup.switch(cfg(dc.path().into(), "/usr/bin/python3", fake_engine_args(&sc))).await;
        assert_ne!(unsafe { libc::kill(b, 0) }, 0, "our engine B must be stopped by the switch");
        until(&sup, "attached to C", |s| s.connected && !s.supervised).await;
        sup.switch(cfg(da.path().into(), "/usr/bin/python3", fake_engine_args(&sa))).await;
        until(&sup, "engine A again", |s| s.connected && s.supervised).await;
        assert!(external.0.try_wait().unwrap().is_none(), "an engine we did not start must keep running");
        sup.shutdown().await;
        runner.await.unwrap();
    }

    #[tokio::test]
    async fn spawns_supervises_and_stops_only_its_own_child() {
        let d = tempfile::Builder::new().prefix("cx").tempdir_in("/tmp").unwrap();
        let home = d.path().to_path_buf();
        let sock = home.join("chromad.sock");
        let rec = Arc::new(Rec(StdMutex::new(vec![])));
        let sup = Supervisor::new(cfg(home.clone(), "/usr/bin/python3", fake_engine_args(&sock)), Arc::new(Nop), rec.clone());
        let runner = tokio::spawn(sup.clone().run());
        let t0 = Instant::now();
        while !sup.status().connected && t0.elapsed() < Duration::from_secs(10) {
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
        let st = sup.status();
        assert!(st.connected && st.supervised, "{st:?}");
        let pid = st.pid.unwrap() as i32;
        assert_eq!(unsafe { libc::kill(pid, 0) }, 0);
        sup.shutdown().await;
        runner.await.unwrap();
        assert_ne!(unsafe { libc::kill(pid, 0) }, 0, "supervised engine must be stopped on Quit");
        let phases: Vec<String> = rec.0.lock().unwrap().iter().map(|s| s.phase.clone()).collect();
        assert!(phases.contains(&"starting".to_string()) && phases.last() == Some(&"stopped".to_string()));
    }

    #[tokio::test]
    async fn attaches_to_an_external_engine_and_leaves_it_running() {
        let d = tempfile::Builder::new().prefix("cx").tempdir_in("/tmp").unwrap();
        let home = d.path().to_path_buf();
        let sock = home.join("chromad.sock");
        let mut external = std::process::Command::new("/usr/bin/python3").args(fake_engine_args(&sock)).spawn().unwrap();
        while !sock.exists() {
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
        let sup = Supervisor::new(cfg(home, "/bin/false", vec![]), Arc::new(Nop), Arc::new(Rec(StdMutex::new(vec![]))));
        let runner = tokio::spawn(sup.clone().run());
        let t0 = Instant::now();
        while !sup.status().connected && t0.elapsed() < Duration::from_secs(5) {
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
        assert!(sup.status().connected && !sup.status().supervised);
        assert!(!sup.restart().await, "must not restart an engine it does not own");
        sup.shutdown().await;
        runner.abort();
        assert!(external.try_wait().unwrap().is_none(), "external engine must keep running");
        external.kill().unwrap();
        let _ = external.wait();
    }

    #[tokio::test]
    async fn adopts_an_engine_left_behind_by_an_earlier_run_and_stops_it_on_quit() {
        let d = tempfile::Builder::new().prefix("cx").tempdir_in("/tmp").unwrap();
        let home = d.path().to_path_buf();
        let sock = home.join("chromad.sock");
        // an "earlier run" spawned this chromad and crashed; its argv mentions chromad
        let mut args = fake_engine_args(&sock);
        args.push("chromad".into());
        let mut orphan = std::process::Command::new("/usr/bin/python3").args(&args).spawn().unwrap();
        std::fs::write(home.join("chromad.app.pid"), orphan.id().to_string()).unwrap();
        while !sock.exists() {
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
        let sup = Supervisor::new(cfg(home, "/bin/false", vec![]), Arc::new(Nop), Arc::new(Rec(StdMutex::new(vec![]))));
        let runner = tokio::spawn(sup.clone().run());
        let t0 = Instant::now();
        while !sup.status().connected && t0.elapsed() < Duration::from_secs(5) {
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
        assert!(sup.status().supervised, "{:?}", sup.status());
        sup.shutdown().await;
        runner.abort();
        let t0 = Instant::now();
        while orphan.try_wait().unwrap().is_none() && t0.elapsed() < Duration::from_secs(3) {
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
        assert!(orphan.try_wait().unwrap().is_some(), "adopted engine must be stopped on Quit");
    }

    #[tokio::test]
    async fn a_dying_engine_is_restarted_with_backoff() {
        let d = tempfile::Builder::new().prefix("cx").tempdir_in("/tmp").unwrap();
        let rec = Arc::new(Rec(StdMutex::new(vec![])));
        let sup = Supervisor::new(cfg(d.path().to_path_buf(), "/bin/false", vec![]), Arc::new(Nop), rec.clone());
        let runner = tokio::spawn(sup.clone().run());
        tokio::time::sleep(Duration::from_millis(1600)).await;
        sup.shutdown().await;
        runner.abort();
        let msgs: Vec<String> = rec.0.lock().unwrap().iter().map(|s| s.message.clone()).collect();
        assert!(msgs.iter().any(|m| m.starts_with("engine exited — restarting in 1s")), "{msgs:?}");
    }
}
