import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build straight into the package's static/ directory, which api.py mounts as
// the SPA. No copy step, no separate web server in production.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../src/streetclip/static", emptyOutDir: true },
  server: { proxy: { "/api": "http://127.0.0.1:8080" } },
});
