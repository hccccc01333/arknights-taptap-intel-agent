import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5180,
    proxy: { "/api": "http://127.0.0.1:8203" },   // 直连 FastAPI
  },
  build: { outDir: "dist", chunkSizeWarningLimit: 1600 },
});
