//! `chroma-art://current?rev=N` → the engine's current artwork file.
//!
//! Serves exactly one file ($CHROMA_HOME/current_artwork.jpg); the URL's path and
//! query are ignored except as a cache buster. Content type comes from the bytes
//! (the fake engine writes PNG under the .jpg name).

use std::path::Path;

pub fn sniff(bytes: &[u8]) -> &'static str {
    match bytes {
        [0x89, b'P', b'N', b'G', ..] => "image/png",
        [0xFF, 0xD8, 0xFF, ..] => "image/jpeg",
        [b'G', b'I', b'F', b'8', ..] => "image/gif",
        [b'R', b'I', b'F', b'F', _, _, _, _, b'W', b'E', b'B', b'P', ..] => "image/webp",
        _ => "application/octet-stream",
    }
}

/// (status, content type, body)
pub fn load(path: &Path) -> (u16, &'static str, Vec<u8>) {
    match std::fs::read(path) {
        Ok(bytes) if !bytes.is_empty() => (200, sniff(&bytes), bytes),
        _ => (404, "text/plain", b"no artwork".to_vec()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sniffs_common_formats() {
        assert_eq!(sniff(b"\x89PNG\r\n\x1a\n...."), "image/png");
        assert_eq!(sniff(&[0xFF, 0xD8, 0xFF, 0xE0, 0]), "image/jpeg");
        assert_eq!(sniff(b"RIFF\0\0\0\0WEBPVP8 "), "image/webp");
        assert_eq!(sniff(b"hello"), "application/octet-stream");
    }

    #[test]
    fn missing_or_empty_file_is_404() {
        let d = tempfile::tempdir().unwrap();
        assert_eq!(load(&d.path().join("nope.jpg")).0, 404);
        let p = d.path().join("a.jpg");
        std::fs::write(&p, b"").unwrap();
        assert_eq!(load(&p).0, 404);
        std::fs::write(&p, [0xFF, 0xD8, 0xFF, 1, 2]).unwrap();
        let (status, ct, body) = load(&p);
        assert_eq!((status, ct, body.len()), (200, "image/jpeg", 5));
    }
}
