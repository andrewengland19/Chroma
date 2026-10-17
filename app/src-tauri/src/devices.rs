//! Chroma → Devices → Fake | Real: which engine the app runs.
//!
//! Fake = FakeProvider + fake bulbs in `~/.chroma/dev` (`--fake`); Real = the Apple TV
//! and the LIFX bulbs in `~/.chroma`. A home given at launch (`CHROMA_HOME`, e.g.
//! `/tmp/chroma-hw` for real devices) is kept for the mode it was launched in. Other
//! launch arguments (`--no-lifx`, `--log-level …`) carry over; only `--fake` follows the
//! mode. The choice is saved and used at the next launch unless `CHROMA_HOME` or
//! `CHROMAD_ARGS` is set (an explicit environment always wins).

use crate::config::EngineConfig;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Devices {
    Fake,
    Real,
}

impl Devices {
    pub fn of(cfg: &EngineConfig) -> Devices {
        if cfg.fake { Devices::Fake } else { Devices::Real }
    }

    pub fn name(self) -> &'static str {
        match self {
            Devices::Fake => "fake",
            Devices::Real => "real",
        }
    }

    fn parse(s: &str) -> Option<Devices> {
        match s.trim() {
            "fake" => Some(Devices::Fake),
            "real" => Some(Devices::Real),
            _ => None,
        }
    }
}

/// Builds the engine config for either mode from what the app was launched with.
#[derive(Debug, Clone)]
pub struct DeviceModes {
    launch: EngineConfig,
    launch_extra: Vec<String>,
    debug: bool,
    repo_root: PathBuf,
    user_home: PathBuf,
    cmd: Option<String>,
    supervise: Option<String>,
}

impl DeviceModes {
    /// `get` reads the launch environment (as `EngineConfig::resolve`).
    pub fn new(get: impl Fn(&str) -> Option<String>, debug: bool, repo_root: &Path, user_home: &Path) -> DeviceModes {
        let launch = EngineConfig::resolve(&get, debug, repo_root, user_home);
        let launch_extra = match get("CHROMAD_ARGS") {
            Some(a) => a.split_whitespace().filter(|x| *x != "--fake").map(String::from).collect(),
            None => vec![],
        };
        DeviceModes {
            launch,
            launch_extra,
            debug,
            repo_root: repo_root.to_path_buf(),
            user_home: user_home.to_path_buf(),
            cmd: get("CHROMAD_CMD"),
            supervise: get("CHROMA_SUPERVISE"),
        }
    }

    pub fn launch(&self) -> &EngineConfig {
        &self.launch
    }

    pub fn home_for(&self, mode: Devices) -> PathBuf {
        if Devices::of(&self.launch) == mode {
            return self.launch.home.clone();
        }
        match mode {
            Devices::Fake => self.user_home.join(".chroma").join("dev"),
            Devices::Real => self.user_home.join(".chroma"),
        }
    }

    pub fn config_for(&self, mode: Devices) -> EngineConfig {
        let home = self.home_for(mode).to_string_lossy().into_owned();
        let mut args = self.launch_extra.clone();
        if mode == Devices::Fake {
            args.push("--fake".into());
        }
        let args = args.join(" ");
        let get = |k: &str| match k {
            "CHROMA_HOME" => Some(home.clone()),
            "CHROMAD_ARGS" => Some(args.clone()),
            "CHROMAD_CMD" => self.cmd.clone(),
            "CHROMA_SUPERVISE" => self.supervise.clone(),
            _ => None,
        };
        EngineConfig::resolve(get, self.debug, &self.repo_root, &self.user_home)
    }
}

/// The saved choice (`devices.json` in the app's config dir).
pub fn load_choice(path: &Path) -> Option<Devices> {
    let v: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(path).ok()?).ok()?;
    Devices::parse(v.get("devices")?.as_str()?)
}

pub fn save_choice(path: &Path, mode: Devices) -> std::io::Result<()> {
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    let tmp = path.with_extension("json.tmp");
    std::fs::write(&tmp, serde_json::json!({ "devices": mode.name() }).to_string())?;
    std::fs::rename(tmp, path)
}

/// The engine to start with: an explicit environment wins, else the saved choice, else
/// the build default (debug = fake).
pub fn initial(modes: &DeviceModes, env_pinned: bool, saved: Option<Devices>) -> EngineConfig {
    match saved {
        Some(mode) if !env_pinned => modes.config_for(mode),
        _ => modes.launch().clone(),
    }
}

/// `lite show` (the old hub) and a real-device chromad would drive the same bulbs, and
/// chromad refuses to start next to it: stop it. Ok(true) = it was running and stopped.
pub fn stop_lite_show(user_home: &Path) -> Result<bool, String> {
    let local = user_home.join(".local/bin/lite");
    let lite = if local.is_file() { local } else { PathBuf::from("lite") };
    let status = std::process::Command::new(&lite).args(["show", "status"]).output();
    let Ok(status) = status else { return Ok(false) }; // no `lite`: nothing to stop
    let text = String::from_utf8_lossy(&status.stdout).to_lowercase();
    if !text.contains("running") || text.contains("not running") {
        return Ok(false);
    }
    let out = std::process::Command::new(&lite).args(["show", "stop"]).output().map_err(|e| e.to_string())?;
    if out.status.success() {
        Ok(true)
    } else {
        Err(format!("lite show stop failed: {}", String::from_utf8_lossy(&out.stderr).trim()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn env(pairs: &[(&str, &str)]) -> impl Fn(&str) -> Option<String> {
        let m: HashMap<String, String> = pairs.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect();
        move |k| m.get(k).cloned()
    }

    #[test]
    fn dev_launch_switches_between_the_dev_home_and_the_real_home() {
        let m = DeviceModes::new(env(&[]), true, Path::new("/repo"), Path::new("/Users/a"));
        assert_eq!(Devices::of(m.launch()), Devices::Fake);
        let real = m.config_for(Devices::Real);
        assert_eq!(real.home, PathBuf::from("/Users/a/.chroma"));
        assert_eq!(real.args, vec!["/repo/hub/chromad.py", "--home", "/Users/a/.chroma"]);
        assert!(!real.fake && real.supervise);
        assert_eq!(m.config_for(Devices::Fake), *m.launch());
    }

    #[test]
    fn a_launch_home_is_kept_for_its_mode_and_other_args_carry_over() {
        let m = DeviceModes::new(
            env(&[("CHROMA_HOME", "/tmp/chroma-hw"), ("CHROMAD_ARGS", "--log-level DEBUG")]),
            true,
            Path::new("/repo"),
            Path::new("/Users/a"),
        );
        assert_eq!(Devices::of(m.launch()), Devices::Real);
        assert_eq!(m.config_for(Devices::Real).home, PathBuf::from("/tmp/chroma-hw"));
        let fake = m.config_for(Devices::Fake);
        assert_eq!(fake.home, PathBuf::from("/Users/a/.chroma/dev"));
        assert_eq!(fake.args, vec!["/repo/hub/chromad.py", "--home", "/Users/a/.chroma/dev", "--log-level", "DEBUG", "--fake"]);
        assert!(fake.fake);
    }

    #[test]
    fn saved_choice_round_trips_and_an_explicit_environment_wins() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("cfg/devices.json");
        assert_eq!(load_choice(&p), None);
        save_choice(&p, Devices::Real).unwrap();
        assert_eq!(load_choice(&p), Some(Devices::Real));
        std::fs::write(&p, "{nope").unwrap();
        assert_eq!(load_choice(&p), None);
        let m = DeviceModes::new(env(&[]), true, Path::new("/repo"), Path::new("/Users/a"));
        assert!(!initial(&m, false, Some(Devices::Real)).fake);
        assert!(initial(&m, true, Some(Devices::Real)).fake); // CHROMA_HOME/CHROMAD_ARGS set
        assert!(initial(&m, false, None).fake);
    }
}
