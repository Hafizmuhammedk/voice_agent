import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const environment = loadEnv(mode, ".", "VITE_");
  const apiTarget = environment.VITE_DEV_PROXY_TARGET || "http://127.0.0.1:8000";

  return {
    base: "/app/",
    plugins: [react()],
    build: {
      chunkSizeWarningLimit: 750,
    },
    server: {
      host: "127.0.0.1",
      port: 5173,
      proxy: {
        "/api": {
          target: apiTarget,
          changeOrigin: true,
        },
        "/health": {
          target: apiTarget,
          changeOrigin: true,
        },
      },
    },
  };
});
