//! NDJSON client for chromad's Unix socket (contracts/ipc-*.schema.json).
//!
//! One connection carries every request from the UI. Each request gets a fresh
//! integer id; the reader task routes replies back by id and forwards events
//! (`{"ev": ...}`) to an [`EventSink`]. The webview never touches the socket.

use serde_json::{json, Value};
use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::unix::OwnedWriteHalf;
use tokio::net::UnixStream;
use tokio::sync::{oneshot, Mutex, Notify};

pub const REQUEST_TIMEOUT: Duration = Duration::from_secs(30);
/// Same cap as chromad (hub/ipc.py): a line longer than this is a protocol error.
pub const MAX_LINE: usize = 1 << 22;

pub trait EventSink: Send + Sync + 'static {
    fn event(&self, ev: Value);
}

#[derive(Default)]
pub struct IpcClient {
    writer: Mutex<Option<OwnedWriteHalf>>,
    pending: Mutex<HashMap<u64, oneshot::Sender<Value>>>,
    next_id: AtomicU64,
    hangup: Notify,
    hangup_gen: AtomicU64,
}

impl IpcClient {
    pub fn new() -> Arc<Self> {
        Arc::new(IpcClient { next_id: AtomicU64::new(1), ..Default::default() })
    }

    /// End the current connection (`serve` returns); used when switching engines.
    pub fn disconnect(&self) {
        self.hangup_gen.fetch_add(1, Ordering::SeqCst);
        self.hangup.notify_waiters(); // no stored permit: a later connection is unaffected
    }

    #[cfg(test)]
    pub async fn connected(&self) -> bool {
        self.writer.lock().await.is_some()
    }

    /// Send `req` (any `id` it carries is replaced) and wait for the matching reply.
    /// Returns the full reply, including `{"ok": false, "error"}`; Err is transport-level.
    pub async fn request(&self, mut req: Value) -> Result<Value, String> {
        let obj = req.as_object_mut().ok_or("request must be a JSON object")?;
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        obj.insert("id".into(), json!(id));
        let (tx, rx) = oneshot::channel();
        self.pending.lock().await.insert(id, tx);
        if let Err(e) = self.write(&req).await {
            self.pending.lock().await.remove(&id);
            return Err(e);
        }
        match tokio::time::timeout(REQUEST_TIMEOUT, rx).await {
            Ok(Ok(reply)) => Ok(reply),
            Ok(Err(_)) => Err("engine disconnected".into()),
            Err(_) => {
                self.pending.lock().await.remove(&id);
                Err("engine did not answer in time".into())
            }
        }
    }

    async fn write(&self, v: &Value) -> Result<(), String> {
        let mut line = serde_json::to_vec(v).map_err(|e| e.to_string())?;
        line.push(b'\n');
        let mut guard = self.writer.lock().await;
        let w = guard.as_mut().ok_or("engine not connected")?;
        w.write_all(&line).await.map_err(|e| format!("write failed: {e}"))
    }

    /// Own `stream` until it closes: subscribe, then route replies and events.
    /// Pending requests fail when the connection ends.
    pub async fn serve(&self, stream: UnixStream, sink: Arc<dyn EventSink>) {
        let (read, write) = stream.into_split();
        *self.writer.lock().await = Some(write);
        let _ = self.write(&json!({"id": 0, "op": "subscribe"})).await;
        let mut reader = BufReader::new(read);
        let mut buf = Vec::with_capacity(4096);
        let generation = self.hangup_gen.load(Ordering::SeqCst);
        loop {
            buf.clear();
            let hangup = self.hangup.notified();
            tokio::pin!(hangup);
            hangup.as_mut().enable(); // registered before the check below: no lost wake-up
            if self.hangup_gen.load(Ordering::SeqCst) != generation {
                break;
            }
            let line = tokio::select! {
                r = read_line(&mut reader, &mut buf) => r,
                _ = &mut hangup => break,
            };
            match line {
                Ok(true) => {}
                Ok(false) | Err(_) => break,
            }
            let Ok(msg) = serde_json::from_slice::<Value>(&buf) else { continue };
            if msg.get("ev").is_some() {
                sink.event(msg);
            } else if let Some(id) = msg.get("id").and_then(Value::as_u64) {
                if let Some(tx) = self.pending.lock().await.remove(&id) {
                    let _ = tx.send(msg);
                }
            }
        }
        *self.writer.lock().await = None;
        self.pending.lock().await.clear(); // dropping the senders fails every waiter
    }
}

/// Read one `\n`-terminated line into `buf` (without the newline). Ok(false) = EOF.
async fn read_line<R: tokio::io::AsyncBufRead + Unpin>(r: &mut R, buf: &mut Vec<u8>) -> std::io::Result<bool> {
    loop {
        let chunk = r.fill_buf().await?;
        if chunk.is_empty() {
            return Ok(false);
        }
        if let Some(i) = chunk.iter().position(|&b| b == b'\n') {
            buf.extend_from_slice(&chunk[..i]);
            r.consume(i + 1);
            return Ok(true);
        }
        let n = chunk.len();
        buf.extend_from_slice(chunk);
        r.consume(n);
        if buf.len() > MAX_LINE {
            return Err(std::io::Error::new(std::io::ErrorKind::InvalidData, "line too long"));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex as StdMutex;
    use tokio::net::UnixListener;

    struct Collect(StdMutex<Vec<Value>>);
    impl EventSink for Collect {
        fn event(&self, ev: Value) {
            self.0.lock().unwrap().push(ev);
        }
    }

    /// A tiny fake chromad: answers get_state, echoes intents, emits a state event on subscribe.
    async fn fake_engine(listener: UnixListener, slow_first: bool) {
        let (stream, _) = listener.accept().await.unwrap();
        let (r, mut w) = stream.into_split();
        let mut lines = BufReader::new(r).lines();
        let mut first = true;
        while let Ok(Some(line)) = lines.next_line().await {
            let req: Value = serde_json::from_str(&line).unwrap();
            let id = req["id"].clone();
            let reply = match req["op"].as_str().unwrap() {
                "subscribe" => {
                    let a = json!({"id": id, "ok": true}).to_string() + "\n";
                    let b = json!({"ev": "state", "state": {"n": 1}}).to_string() + "\n";
                    format!("{a}{b}")
                }
                "get_state" => json!({"id": id, "ok": true, "state": {"n": 1}}).to_string() + "\n",
                "intent" => {
                    if slow_first && first {
                        first = false;
                        // answer the NEXT request first to prove correlation is by id
                        let next = lines.next_line().await.unwrap().unwrap();
                        let req2: Value = serde_json::from_str(&next).unwrap();
                        let r2 = json!({"id": req2["id"], "ok": true, "which": "second"}).to_string() + "\n";
                        w.write_all(r2.as_bytes()).await.unwrap();
                    }
                    json!({"id": id, "ok": true, "which": req["intent"]["v"]}).to_string() + "\n"
                }
                "fail" => json!({"id": id, "ok": false, "error": "nope"}).to_string() + "\n",
                "hangup" => return,
                _ => continue,
            };
            w.write_all(reply.as_bytes()).await.unwrap();
        }
    }

    fn sock() -> (tempfile::TempDir, std::path::PathBuf) {
        let d = tempfile::Builder::new().prefix("cx").tempdir_in("/tmp").unwrap();
        let p = d.path().join("s.sock");
        (d, p)
    }

    #[tokio::test]
    async fn requests_are_correlated_by_id_and_events_forwarded() {
        let (_d, path) = sock();
        let listener = UnixListener::bind(&path).unwrap();
        tokio::spawn(fake_engine(listener, true));
        let client = IpcClient::new();
        let sink = Arc::new(Collect(StdMutex::new(vec![])));
        let stream = UnixStream::connect(&path).await.unwrap();
        let c2 = client.clone();
        let s2: Arc<dyn EventSink> = sink.clone();
        tokio::spawn(async move { c2.serve(stream, s2).await });
        while !client.connected().await {
            tokio::task::yield_now().await;
        }
        let a = client.request(json!({"op": "intent", "intent": {"v": "first"}}));
        let b = client.request(json!({"op": "get_state", "id": 999}));
        let (a, b) = tokio::join!(a, b);
        assert_eq!(a.unwrap()["which"], "first");
        assert_eq!(b.unwrap()["which"], "second"); // answered out of order, still matched
        let r = client.request(json!({"op": "fail"})).await.unwrap();
        assert_eq!(r, json!({"id": r["id"], "ok": false, "error": "nope"}));
        assert_eq!(sink.0.lock().unwrap()[0], json!({"ev": "state", "state": {"n": 1}}));
    }

    #[tokio::test]
    async fn pending_requests_fail_when_the_engine_goes_away() {
        let (_d, path) = sock();
        let listener = UnixListener::bind(&path).unwrap();
        tokio::spawn(fake_engine(listener, false));
        let client = IpcClient::new();
        let stream = UnixStream::connect(&path).await.unwrap();
        let c2 = client.clone();
        let sink: Arc<dyn EventSink> = Arc::new(Collect(StdMutex::new(vec![])));
        let serving = tokio::spawn(async move { c2.serve(stream, sink).await });
        while !client.connected().await {
            tokio::task::yield_now().await;
        }
        let r = client.request(json!({"op": "hangup"})).await;
        assert_eq!(r, Err("engine disconnected".to_string()));
        serving.await.unwrap();
        assert!(!client.connected().await);
        assert_eq!(client.request(json!({"op": "get_state"})).await, Err("engine not connected".to_string()));
    }

    #[tokio::test]
    async fn read_line_splits_and_caps() {
        let data = b"{\"a\":1}\n{\"b\":2}\npartial".to_vec();
        let mut r = BufReader::new(&data[..]);
        let mut buf = vec![];
        assert!(read_line(&mut r, &mut buf).await.unwrap());
        assert_eq!(buf, b"{\"a\":1}");
        buf.clear();
        assert!(read_line(&mut r, &mut buf).await.unwrap());
        assert_eq!(buf, b"{\"b\":2}");
        buf.clear();
        assert!(!read_line(&mut r, &mut buf).await.unwrap());
        let big = vec![b'x'; MAX_LINE + 10];
        let mut r = BufReader::new(&big[..]);
        buf.clear();
        assert!(read_line(&mut r, &mut buf).await.is_err());
    }
}
