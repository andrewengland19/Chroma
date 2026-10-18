//! Run chromad headless at login: a per-user LaunchAgent (M7).
//!
//! The tray / Chroma menu item "Run engine at login" writes
//! `~/Library/LaunchAgents/com.chroma.chromad.plist` (RunAtLoad), and removes it again.
//! Nothing is written until the user clicks; installing does not start anything now (the
//! engine the app runs is already attached to), it takes effect at the next login. The app
//! then simply attaches to that engine (supervisor.rs: an engine it did not spawn is
//! attached to and never stopped by Quit).
//!
//! The plist is only written under the `user_home` passed in, and launchd is reached only
//! through the [`Launchd`] trait: tests use a recorder with a temp HOME and never touch the
//! real launchd; `SystemLaunchd` (the real `launchctl`) is built only by the menu handler.

use std::path::{Path, PathBuf};

pub const LABEL: &str = "com.chroma.chromad";

/// What the agent runs: the engine command and the files it writes.
#[derive(Debug, Clone, PartialEq)]
pub struct AgentSpec {
    pub program: PathBuf,
    /// arguments after the program (e.g. `--home ~/.chroma`)
    pub args: Vec<String>,
    pub log: PathBuf,
}

pub fn plist_path(user_home: &Path) -> PathBuf {
    user_home.join("Library/LaunchAgents").join(format!("{LABEL}.plist"))
}

pub fn is_installed(user_home: &Path) -> bool {
    plist_path(user_home).is_file()
}

fn esc(s: &str) -> String {
    s.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;")
}

/// The LaunchAgent property list. KeepAlive only on an unclean exit: `lite show stop` (SIGTERM,
/// exit 0) and a chromad that refuses to start next to the old hub (exit 2, throttled to one
/// retry per 30 s) never spin.
pub fn render(spec: &AgentSpec) -> String {
    let mut argv = vec![spec.program.to_string_lossy().into_owned()];
    argv.extend(spec.args.iter().cloned());
    let args: String = argv.iter().map(|a| format!("        <string>{}</string>\n", esc(a))).collect();
    let log = esc(&spec.log.to_string_lossy());
    format!(
        r#"<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LABEL}</string>
    <key>ProgramArguments</key>
    <array>
{args}    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>ThrottleInterval</key>
    <integer>30</integer>
    <key>StandardOutPath</key>
    <string>{log}</string>
    <key>StandardErrorPath</key>
    <string>{log}</string>
</dict>
</plist>
"#
    )
}

/// The only launchd operation we need: stop the loaded agent when it is removed.
pub trait Launchd {
    /// `launchctl bootout gui/<uid>/<label>`; "not loaded" is not an error.
    fn bootout(&self, label: &str);
}

pub struct SystemLaunchd;

impl Launchd for SystemLaunchd {
    fn bootout(&self, label: &str) {
        let target = format!("gui/{}/{label}", unsafe { libc::getuid() });
        let _ = std::process::Command::new("/bin/launchctl").args(["bootout", &target]).output();
    }
}

/// Write the plist (atomically, 0644). Does not load it.
pub fn install(user_home: &Path, spec: &AgentSpec) -> std::io::Result<PathBuf> {
    let path = plist_path(user_home);
    std::fs::create_dir_all(path.parent().unwrap())?;
    let tmp = path.with_extension("plist.tmp");
    std::fs::write(&tmp, render(spec))?;
    std::fs::rename(&tmp, &path)?;
    Ok(path)
}

/// Unload the agent (stops the engine launchd started) and delete the plist. Ok(false) = it
/// was not installed.
pub fn remove(user_home: &Path, launchd: &dyn Launchd) -> std::io::Result<bool> {
    let path = plist_path(user_home);
    if !path.is_file() {
        return Ok(false);
    }
    launchd.bootout(LABEL);
    std::fs::remove_file(&path)?;
    Ok(true)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;

    #[derive(Default)]
    struct Recorder(RefCell<Vec<String>>);
    impl Launchd for Recorder {
        fn bootout(&self, label: &str) {
            self.0.borrow_mut().push(label.to_string());
        }
    }

    fn spec() -> AgentSpec {
        AgentSpec {
            program: PathBuf::from("/Applications/Chroma.app/Contents/Resources/chromad/chromad"),
            args: vec!["--home".into(), "/Users/a/.chroma".into()],
            log: PathBuf::from("/Users/a/.chroma/chromad.log"),
        }
    }

    #[test]
    fn the_plist_runs_the_engine_at_login_and_is_xml_safe() {
        let p = render(&spec());
        assert!(p.contains("<string>com.chroma.chromad</string>"));
        assert!(p.contains("<key>RunAtLoad</key>\n    <true/>"));
        assert!(p.contains(
            "<string>/Applications/Chroma.app/Contents/Resources/chromad/chromad</string>\n        <string>--home</string>\n        <string>/Users/a/.chroma</string>"
        ));
        assert!(p.contains("<key>SuccessfulExit</key>\n        <false/>"));
        let mut s = spec();
        s.program = PathBuf::from("/Users/a & b/<x>/chromad");
        assert!(render(&s).contains("/Users/a &amp; b/&lt;x&gt;/chromad"));
    }

    #[test]
    fn install_writes_only_under_the_given_home_and_remove_unloads_then_deletes() {
        let home = tempfile::tempdir().unwrap();
        assert!(!is_installed(home.path()));
        // nothing exists until install is called
        assert!(!home.path().join("Library").exists());
        let path = install(home.path(), &spec()).unwrap();
        assert_eq!(path, home.path().join("Library/LaunchAgents/com.chroma.chromad.plist"));
        assert!(is_installed(home.path()));
        assert_eq!(std::fs::read_to_string(&path).unwrap(), render(&spec()));
        assert!(!path.with_extension("plist.tmp").exists());
        // installing again replaces it (the engine path may have changed)
        let mut s2 = spec();
        s2.args.push("--no-lifx".into());
        install(home.path(), &s2).unwrap();
        assert!(std::fs::read_to_string(&path).unwrap().contains("--no-lifx"));

        let rec = Recorder::default();
        assert!(remove(home.path(), &rec).unwrap());
        assert_eq!(*rec.0.borrow(), vec!["com.chroma.chromad"]);
        assert!(!is_installed(home.path()));
        // removing when absent touches neither launchd nor the disk
        assert!(!remove(home.path(), &rec).unwrap());
        assert_eq!(rec.0.borrow().len(), 1);
    }
}
