import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// in development the API runs separately: python -m schema_engine.app --serve
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: { outDir: "../src/schema_engine/app/static", emptyOutDir: true },
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8765",
        // the API refuses other origins, and this page's origin is the dev server
        headers: { origin: "http://127.0.0.1:8765" },
      },
    },
  },
});
