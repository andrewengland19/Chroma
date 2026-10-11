/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import process from "node:process";

const host = process.env.TAURI_DEV_HOST;

// `npm run dev` serves the UI for `tauri dev` (real IPC through the Rust shell).
// `npm run dev:mock` (vite --mode mock) serves it in a normal browser with the in-browser mock engine
// (VITE_IPC=mock from .env.mock), so screens can be screenshotted and compared with docs/mvp/gui-*.png.
export default defineConfig(() => ({
  plugins: [react(), tailwindcss()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    host: host || false,
    hmr: host ? { protocol: "ws", host, port: 1421 } : undefined,
    watch: { ignored: ["**/src-tauri/**"] },
    fs: { allow: [".", "../contracts"] }, // mock scenarios import contracts/examples
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
}));
