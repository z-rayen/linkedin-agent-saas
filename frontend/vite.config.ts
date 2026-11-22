import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// One variable sets the dev port; the backend's FRONTEND_ORIGIN must name the same port.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const port = Number(env.VITE_DEV_PORT ?? "5173");
  const target = env.VITE_DEV_API_TARGET ?? "http://127.0.0.1:8000";
  const apiPrefix = "/api";

  return {
    plugins: [react()],
    server: {
      port,
      strictPort: true,
      proxy: {
        [apiPrefix]: {
          target,
          changeOrigin: false,
          // Adds X-Forwarded-For with the browser's address, so the API's per-address login limit sees the client.
          xfwd: true,
          rewrite: (path: string) => path.replace(new RegExp(`^${apiPrefix}`), ""),
        },
      },
    },
    preview: { port, strictPort: true },
    test: {
      environment: "jsdom",
      globals: false,
      setupFiles: ["./src/test/setup.ts"],
      css: false,
    },
  };
});
