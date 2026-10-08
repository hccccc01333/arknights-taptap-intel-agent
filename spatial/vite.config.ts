import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "VITE_");

  return {
    base: env.VITE_BASE_PATH || "/",
    plugins: [react()],
    server: {
      port: 5180,
      proxy: { "/api": env.VITE_DEV_API_TARGET || "http://127.0.0.1:8200" },
    },
    build: {
      outDir: "dist",
      emptyOutDir: false,
      chunkSizeWarningLimit: 1600,
    },
  };
});
