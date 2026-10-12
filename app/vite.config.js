import { defineConfig } from "vite";

// Tauri expects the dev server on 1420 (tauri.conf.json devUrl).
// Watch must ignore the Rust target dir or chokidar dies on cargo's EBUSY
// file swaps while the build script runs.
export default defineConfig({
  server: {
    port: 1420,
    strictPort: true,
    // chokidar v4 (vite 6) has no glob support for `ignored` — function form:
    watcher: {
      ignored: (path) => String(path).replace(/\\/g, "/").includes("/src-tauri/"),
    },
  },
  clearScreen: false,
  build: { rollupOptions: { external: /^tauri:/ } },
});
