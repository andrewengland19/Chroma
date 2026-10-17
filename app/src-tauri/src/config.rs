//! Where the engine lives and how to start it.
//!
//! Environment (all optional):
//!   CHROMA_HOME       state dir shared with chromad (socket, artwork)
//!   CHROMAD_CMD       program to run (default: the repo's hub venv python + hub/chromad.py)
//!   CHROMAD_ARGS      extra args, whitespace separated
//!   CHROMA_SUPERVISE  "0" = only attach to a running chromad, never spawn one
//!
//! Debug builds default to a **fake** engine in `~/.chroma/dev` (FakeProvider + fake
//! bulbs), so `npm run tauri dev` never touches the Apple TV, the bulbs or the live
//! hub's files. Release builds default to the real engine in `~/.chroma`.

use std::path::{Path, PathBuf};

#[derive(Debug, Clone, PartialEq)]
pub struct EngineConfig {
    pub home: PathBuf,
    pub socket: PathBuf,
    pub program: Option<PathBuf>,
    pub args: Vec<String>,
    pub supervise: bool,
    pub fake: bool,
}

impl EngineConfig {
    /// `get` reads an environment variable; `debug` is the build profile; `repo_root`
    /// is the checkout (dev command); `user_home` is $HOME.
    pub fn resolve(
        get: impl Fn(&str) -> Option<String>,
        debug: bool,
        repo_root: &Path,
        user_home: &Path,
    ) -> EngineConfig {
        let nonempty = |k: &str| get(k).filter(|v| !v.trim().is_empty());
        let home = match nonempty("CHROMA_HOME") {
            Some(h) => expand(&h, user_home),
            None if debug => user_home.join(".chroma").join("dev"),
            None => user_home.join(".chroma"),
        };
        let args: Vec<String> = match get("CHROMAD_ARGS") {
            Some(a) => a.split_whitespace().map(String::from).collect(),
            None if debug => vec!["--fake".into()],
            None => vec![],
        };
        let program = match nonempty("CHROMAD_CMD") {
            Some(p) => Some(expand(&p, user_home)),
            None if debug => Some(repo_root.join("hub/.venv/bin/python")),
            None => None, // M7: the bundled sidecar
        };
        let mut full_args = Vec::new();
        if nonempty("CHROMAD_CMD").is_none() && debug {
            full_args.push(repo_root.join("hub/chromad.py").to_string_lossy().into_owned());
        }
        full_args.push("--home".into());
        full_args.push(home.to_string_lossy().into_owned());
        let fake = args.iter().any(|a| a == "--fake");
        full_args.extend(args);
        let supervise = get("CHROMA_SUPERVISE").map(|v| v != "0").unwrap_or(true) && program.is_some();
        EngineConfig { socket: home.join("chromad.sock"), home, program, args: full_args, supervise, fake }
    }

    pub fn artwork(&self) -> PathBuf {
        self.home.join("current_artwork.jpg")
    }

    pub fn log_file(&self) -> PathBuf {
        self.home.join("chromad.log")
    }
}

fn expand(p: &str, user_home: &Path) -> PathBuf {
    match p.strip_prefix("~/") {
        Some(rest) => user_home.join(rest),
        None if p == "~" => user_home.to_path_buf(),
        None => PathBuf::from(p),
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
    fn debug_defaults_to_a_fake_engine_in_a_dev_home() {
        let c = EngineConfig::resolve(env(&[]), true, Path::new("/repo"), Path::new("/Users/a"));
        assert_eq!(c.home, PathBuf::from("/Users/a/.chroma/dev"));
        assert_eq!(c.socket, PathBuf::from("/Users/a/.chroma/dev/chromad.sock"));
        assert_eq!(c.program, Some(PathBuf::from("/repo/hub/.venv/bin/python")));
        assert_eq!(c.args, vec!["/repo/hub/chromad.py", "--home", "/Users/a/.chroma/dev", "--fake"]);
        assert!(c.supervise && c.fake);
    }

    #[test]
    fn release_defaults_to_the_real_home_and_no_dev_command() {
        let c = EngineConfig::resolve(env(&[]), false, Path::new("/repo"), Path::new("/Users/a"));
        assert_eq!(c.home, PathBuf::from("/Users/a/.chroma"));
        assert_eq!(c.program, None);
        assert!(!c.supervise && !c.fake);
    }

    #[test]
    fn environment_overrides() {
        let c = EngineConfig::resolve(
            env(&[("CHROMA_HOME", "~/x"), ("CHROMAD_ARGS", "--no-lifx  --log-level DEBUG"), ("CHROMA_SUPERVISE", "0")]),
            true,
            Path::new("/repo"),
            Path::new("/Users/a"),
        );
        assert_eq!(c.home, PathBuf::from("/Users/a/x"));
        assert_eq!(c.args, vec!["/repo/hub/chromad.py", "--home", "/Users/a/x", "--no-lifx", "--log-level", "DEBUG"]);
        assert!(!c.supervise && !c.fake);
        let c = EngineConfig::resolve(
            env(&[("CHROMAD_CMD", "/opt/chromad"), ("CHROMAD_ARGS", "")]),
            true,
            Path::new("/repo"),
            Path::new("/Users/a"),
        );
        assert_eq!(c.program, Some(PathBuf::from("/opt/chromad")));
        assert_eq!(c.args, vec!["--home", "/Users/a/.chroma/dev"]);
        assert!(!c.fake);
    }
}
