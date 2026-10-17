//! CHROMA → Force Update: quit, end every CHROMA process, stop `lite show`, relaunch the
//! latest dev version (`npm run tauri dev` in the main checkout). The work is done by
//! `force_update.sh`, started detached (own process group) before the app quits, so it
//! survives the app and the `tauri dev` it may have been started from.

use std::io;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

const SCRIPT: &str = include_str!("../force_update.sh");

/// The main checkout (where local `main` lives): `CHROMA_DEV_CHECKOUT`, else the parent of
/// the git common dir of `repo_root` (a worktree resolves to its main checkout), else
/// `repo_root` itself.
pub fn dev_checkout(repo_root: &Path) -> PathBuf {
    if let Some(p) = std::env::var_os("CHROMA_DEV_CHECKOUT") {
        return PathBuf::from(p);
    }
    let out = Command::new("git")
        .arg("-C")
        .arg(repo_root)
        .args(["rev-parse", "--path-format=absolute", "--git-common-dir"])
        .output();
    if let Ok(o) = out {
        if o.status.success() {
            let common = PathBuf::from(String::from_utf8_lossy(&o.stdout).trim());
            if let Some(parent) = common.parent() {
                return parent.to_path_buf();
            }
        }
    }
    repo_root.to_path_buf()
}

pub fn log_path() -> PathBuf {
    let home = std::env::var_os("HOME").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("/tmp"));
    home.join("Library/Logs/CHROMA/force-update.log")
}

/// Write the helper and start it detached; it waits for `app_pid` to exit.
pub fn launch(app_pid: u32, checkout: &Path, log: &Path) -> io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    use std::os::unix::process::CommandExt;
    let script = std::env::temp_dir().join(format!("chroma-force-update-{app_pid}.sh"));
    std::fs::write(&script, SCRIPT)?;
    std::fs::set_permissions(&script, std::fs::Permissions::from_mode(0o700))?;
    Command::new("/bin/bash")
        .arg(&script)
        .arg(app_pid.to_string())
        .arg(checkout)
        .arg(log)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .process_group(0) // not ended with the app / tauri dev process group
        .spawn()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::process::Child;
    use std::time::{Duration, Instant};

    #[test]
    fn dev_checkout_is_the_main_checkout() {
        let repo = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let c = dev_checkout(&repo);
        assert!(c.join(".git").is_dir(), "{c:?} should be a main checkout (.git directory)");
        assert!(c.join("app/package.json").is_file());
    }

    fn named(name: &str, cwd: &Path) -> Child {
        Command::new("/bin/bash")
            .arg("-c")
            .arg(format!("exec -a '{name}' /bin/sleep 30"))
            .current_dir(cwd)
            .spawn()
            .unwrap()
    }

    fn gone(c: &mut Child) -> bool {
        let t = Instant::now();
        while t.elapsed() < Duration::from_secs(8) {
            if c.try_wait().unwrap().is_some() {
                return true;
            }
            std::thread::sleep(Duration::from_millis(50));
        }
        false
    }

    /// A real (not dry) pass, limited to dummy processes under a temp dir that look like
    /// every kind of CHROMA process; a look-alike outside a Chroma checkout survives.
    #[test]
    fn ends_every_kind_of_chroma_process_and_nothing_else() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().canonicalize().unwrap();
        let co = root.join("Chroma-fu");
        std::fs::create_dir_all(co.join("app")).unwrap();
        std::fs::create_dir_all(root.join("Other/app")).unwrap();
        std::fs::create_dir_all(root.join("target/debug")).unwrap();
        let bin = root.join("target/debug/chroma");
        std::fs::copy("/bin/sleep", &bin).unwrap();
        let r = root.display();
        let mut victims = vec![
            named(&format!("python {r}/hub/chromad.py --home {r}/h"), &root),
            Command::new(&bin).arg("30").spawn().unwrap(),
            named(&format!("node {r}/Chroma-fu/app/node_modules/.bin/tauri dev"), &root),
            named(&format!("node {r}/Chroma-fu/app/node_modules/vite/bin/vite.js"), &root),
            named("npm run tauri dev", &co.join("app")),
        ];
        let mut decoy = named(&format!("node {r}/Other/app/node_modules/.bin/tauri dev"), &root);
        let mut decoy_npm = named("npm run tauri dev", &root.join("Other/app"));
        let mut app = named("app-that-quits", &root);
        let app_pid = app.id();
        let _ = app.kill();
        std::thread::sleep(Duration::from_millis(300));

        let log = root.join("fu.log");
        let only = regex_escape(&r.to_string()); // command line or cwd under the temp dir only
        let status = Command::new("/bin/bash")
            .arg(Path::new(env!("CARGO_MANIFEST_DIR")).join("force_update.sh"))
            .arg(app_pid.to_string())
            .arg(root.join("no-checkout"))
            .arg(&log)
            .env("FORCE_UPDATE_ONLY", only)
            .status()
            .unwrap();
        let text = std::fs::read_to_string(&log).unwrap();
        assert!(!status.success(), "no checkout: must not relaunch\n{text}");
        for (k, v) in victims.iter_mut().enumerate() {
            assert!(gone(v), "victim {k} still running\n{text}");
        }
        assert!(decoy.try_wait().unwrap().is_none(), "decoy was killed\n{text}");
        assert!(decoy_npm.try_wait().unwrap().is_none(), "npm outside a Chroma checkout was killed\n{text}");
        assert!(text.contains("not relaunching"), "{text}");
        let _ = decoy.kill();
        let _ = decoy_npm.kill();
        let _ = app.wait();
    }

    fn regex_escape(s: &str) -> String {
        s.chars().map(|c| if "\\.^$|?*+()[]{}".contains(c) { format!("\\{c}") } else { c.to_string() }).collect()
    }
}
