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
    pub cfg: EngineConfig,
    pub client: Arc<IpcClient>,
    child: Mutex<Option<Child>>,
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
            cfg,
            client: IpcClient::new(),
            child: Mutex::new(None),
            status: StdMutex::new(status),
            stopping: AtomicBool::new(false),
            events,
            statuses,
        })
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

    async fn spawn_engine(&self) -> Result<u32, String> {
        let program = self.cfg.program.clone().ok_or("no engine command configured")?;
        std::fs::create_dir_all(&self.cfg.home).map_err(|e| e.to_string())?;
        let log = OpenOptions::new().create(true).append(true).open(self.cfg.log_file()).map_err(|e| e.to_string())?;
        let log2 = log.try_clone().map_err(|e| e.to_string())?;
        let child = Command::new(&program)
            .args(&self.cfg.args)
            .stdin(Stdio::null())
            .stdout(Stdio::from(log))
            .stderr(Stdio::from(log2))
            .process_group(0) // terminal Ctrl+C / app crash never takes the engine down
            .spawn()
            .map_err(|e| format!("could not start {}: {e}", program.display()))?;
        let pid = child.id().unwrap_or(0);
        *self.child.lock().await = Some(child);
        Ok(pid)
    }

    pub async fn run(self: Arc<Self>) {
        let mut attempt: u32 = 0;
        while !self.stopping.load(Ordering::SeqCst) {
            match UnixStream::connect(&self.cfg.socket).await {
                Ok(stream) => {
                    let supervised = self.child_alive().await;
                    let pid = if supervised { self.child.lock().await.as_ref().and_then(|c| c.id()) } else { None };
                    self.set(|s| {
                        s.phase = "connected".into();
                        s.connected = true;
                        s.supervised = supervised;
                        s.pid = pid;
                        s.message = if supervised { "engine started by the app".into() } else { "attached to a running engine".into() };
                    });
                    let started = Instant::now();
                    self.client.serve(stream, self.events.clone()).await;
                    if started.elapsed() > Duration::from_secs(30) {
                        attempt = 0;
                    }
                    if self.stopping.load(Ordering::SeqCst) {
                        break;
                    }
                    self.set(|s| {
                        s.phase = "connecting".into();
                        s.connected = false;
                        s.message = "engine connection lost — reconnecting".into();
                    });
                }
                Err(_) => {
                    if self.cfg.supervise && !self.child_alive().await {
                        if attempt > 0 {
                            let wait = backoff(attempt);
                            self.set(|s| {
                                s.phase = "restarting".into();
                                s.connected = false;
                                s.message = format!("engine exited — restarting in {}s (see {})", wait.as_secs(), self.cfg.log_file().display());
                            });
                            tokio::time::sleep(wait).await;
                            if self.stopping.load(Ordering::SeqCst) {
                                break;
                            }
                        }
                        attempt += 1;
                        match self.spawn_engine().await {
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
                    } else if !self.cfg.supervise {
                        self.set(|s| {
                            s.phase = "waiting".into();
                            s.connected = false;
                            s.message = "waiting for chromad (not started by the app)".into();
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
        if !self.child_alive().await {
            return false;
        }
        self.terminate_child().await;
        true
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
        let mut guard = self.child.lock().await;
        let Some(child) = guard.as_mut() else { return };
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
            "import socket,os,time\ns=socket.socket(socket.AF_UNIX)\ns.bind({:?})\ns.listen(1)\nc,_=s.accept()\nwhile c.recv(65536): pass\n",
            sock.to_string_lossy()
        );
        vec!["-c".into(), code]
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
