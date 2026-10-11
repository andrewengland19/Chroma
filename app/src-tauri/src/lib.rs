//! CHROMA Console — Tauri shell.
//!
//! M0: an empty window. M2 adds the chromad supervisor, the Unix-socket IPC proxy
//! (`chromad_request` command + `chromad://event`), the tray and the art protocol.

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("error while running the CHROMA app");
}

#[cfg(test)]
mod tests {
    #[test]
    fn crate_builds() {
        assert_eq!(env!("CARGO_PKG_NAME"), "chroma");
    }
}
