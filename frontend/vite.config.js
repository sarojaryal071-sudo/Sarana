import { resolve } from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Plain local dev config — no backend proxy needed since the backend
// already sends permissive-but-explicit CORS headers for the Vite dev
// origin (see dashboard/server.py's _cors_allowed_origins()). The backend
// URL itself comes from VITE_JARVIS_BACKEND_URL (see src/lib/api.js).
//
// Desktop Presentation Engine integration: a second build entry
// (desktop.html -> src/desktop-presentation-main.jsx), a standard Vite
// multi-page-app pattern — NOT a second frontend project, NOT a second
// Presentation Engine. It shares every component/lib module with the main
// app (ContentPanel, PresentationSurface, registry.js, audioFx.js,
// AssistantContext) — only the entry point and transport differ (see that
// file's own header). `npm run build` emits both dist/index.html (the web
// app) and dist/desktop.html (loaded locally by ui.py's QWebEngineView) in
// one pass.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
  },
  build: {
    rollupOptions: {
      input: {
        main: resolve(import.meta.dirname, "index.html"),
        desktop: resolve(import.meta.dirname, "desktop.html"),
      },
    },
  },
});
