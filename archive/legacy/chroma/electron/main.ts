import { app, BrowserWindow, Tray, nativeImage, ipcMain, screen, shell, protocol, net } from "electron";
import * as path from "path";
import * as os from "os";
import { pathToFileURL } from "url";
import { startSidecar, stopSidecar, sendToSidecar, onSidecarEvent } from "./sidecar";

const ARTWORK_PATH = path.join(os.homedir(), ".chroma", "current_artwork.jpg");

// Register the custom scheme *before* app is ready so it's privileged/secure
// and bypasses cross-origin restrictions from the dev server.
protocol.registerSchemesAsPrivileged([
  {
    scheme: "chroma-art",
    privileges: { standard: true, secure: true, supportFetchAPI: true, bypassCSP: true },
  },
]);

const isDev = !app.isPackaged;
const POPOVER_WIDTH = 380;
const POPOVER_HEIGHT = 720;

let tray: Tray | null = null;
let popover: BrowserWindow | null = null;

function createPopover() {
  popover = new BrowserWindow({
    width: POPOVER_WIDTH,
    height: POPOVER_HEIGHT,
    show: false,
    frame: false,
    resizable: false,
    movable: false,
    fullscreenable: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    transparent: false,
    backgroundColor: "#0e0e0e",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  if (isDev) {
    popover.loadURL("http://localhost:5173/");
  } else {
    popover.loadFile(path.join(__dirname, "../renderer/index.html"));
  }

  popover.on("blur", () => {
    if (popover && !popover.webContents.isDevToolsOpened()) {
      popover.hide();
    }
  });
}

function positionPopoverUnderTray() {
  if (!tray || !popover) return;
  const trayBounds = tray.getBounds();
  const display = screen.getDisplayNearestPoint({ x: trayBounds.x, y: trayBounds.y });
  const x = Math.round(
    Math.min(
      Math.max(trayBounds.x + trayBounds.width / 2 - POPOVER_WIDTH / 2, display.workArea.x + 4),
      display.workArea.x + display.workArea.width - POPOVER_WIDTH - 4,
    ),
  );
  const y = Math.round(trayBounds.y + trayBounds.height + 4);
  popover.setPosition(x, y, false);
}

function togglePopover() {
  if (!popover) return;
  if (popover.isVisible()) {
    popover.hide();
  } else {
    positionPopoverUnderTray();
    popover.show();
    popover.focus();
  }
}

function createTray() {
  const iconPath = path.join(__dirname, "../../assets/iconTemplate.png");
  let image: Electron.NativeImage;
  try {
    image = nativeImage.createFromPath(iconPath);
    if (image.isEmpty()) throw new Error("empty");
    image.setTemplateImage(true);
  } catch {
    // Fallback: tiny transparent image; we set a title instead.
    image = nativeImage.createEmpty();
  }
  tray = new Tray(image);
  if (image.isEmpty()) tray.setTitle("♫");
  tray.setToolTip("CHROMA");
  tray.on("click", togglePopover);
  tray.on("right-click", togglePopover);
}

app.whenReady().then(() => {
  // Serve the current artwork file under chroma-art://current — bypasses
  // the cross-origin/file:// restrictions the renderer would otherwise hit.
  protocol.handle("chroma-art", (_req) => {
    return net.fetch(pathToFileURL(ARTWORK_PATH).toString());
  });

  if (app.dock) app.dock.hide();
  createTray();
  createPopover();
  startSidecar();

  // Forward sidecar events to the renderer.
  onSidecarEvent((event) => {
    if (popover && !popover.isDestroyed()) {
      popover.webContents.send("sidecar:event", event);
    }
  });

  // Renderer → Python commands.
  ipcMain.handle("sidecar:send", (_evt, cmd: unknown) => {
    sendToSidecar(cmd);
  });

  ipcMain.handle("app:openMusic", () => {
    shell.openExternal("music://");
  });
});

app.on("window-all-closed", (e: Event) => {
  // Keep the app alive in the menu bar.
  e.preventDefault();
});

app.on("before-quit", () => {
  stopSidecar();
});
