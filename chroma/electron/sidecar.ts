import { spawn, ChildProcessWithoutNullStreams } from "child_process";
import * as path from "path";
import * as readline from "readline";

type Listener = (event: any) => void;

let proc: ChildProcessWithoutNullStreams | null = null;
const listeners = new Set<Listener>();

function pythonExecutable(): string {
  return process.env.CHROMA_PYTHON || "python3";
}

function sidecarScriptPath(): string {
  // __dirname = dist/electron
  return path.join(__dirname, "..", "..", "python", "lifx_music_app.py");
}

export function startSidecar(): void {
  if (proc) return;
  const script = sidecarScriptPath();
  proc = spawn(pythonExecutable(), ["-u", script], {
    stdio: ["pipe", "pipe", "pipe"],
    env: { ...process.env, PYTHONUNBUFFERED: "1" },
  });

  const rl = readline.createInterface({ input: proc.stdout });
  rl.on("line", (line: string) => {
    const trimmed = line.trim();
    if (!trimmed) return;
    try {
      const event = JSON.parse(trimmed);
      for (const l of listeners) l(event);
    } catch {
      // Non-JSON stdout: surface as a status message.
      for (const l of listeners) l({ event: "status", message: trimmed });
    }
  });

  proc.stderr.on("data", (chunk: Buffer) => {
    process.stderr.write(`[sidecar] ${chunk.toString()}`);
  });

  proc.on("exit", (code, signal) => {
    for (const l of listeners) {
      l({ event: "status", message: `Sidecar exited (code=${code}, signal=${signal})` });
    }
    proc = null;
  });
}

export function stopSidecar(): void {
  if (!proc) return;
  try {
    proc.stdin.end();
  } catch {}
  try {
    proc.kill("SIGTERM");
  } catch {}
  proc = null;
}

export function sendToSidecar(cmd: unknown): void {
  if (!proc) return;
  try {
    proc.stdin.write(JSON.stringify(cmd) + "\n");
  } catch (e) {
    for (const l of listeners) l({ event: "error", message: `Failed to send: ${e}` });
  }
}

export function onSidecarEvent(cb: Listener): () => void {
  listeners.add(cb);
  return () => listeners.delete(cb);
}
