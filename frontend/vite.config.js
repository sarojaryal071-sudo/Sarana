import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Plain local dev config — no backend proxy needed since the backend
// already sends permissive-but-explicit CORS headers for the Vite dev
// origin (see dashboard/server.py's _cors_allowed_origins()). The backend
// URL itself comes from VITE_JARVIS_BACKEND_URL (see src/lib/api.js).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
  },
});
