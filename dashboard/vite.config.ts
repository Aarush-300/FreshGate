import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const apiOrigin = process.env.FRESHGATE_API_ORIGIN ?? "http://127.0.0.1:8000";

export default defineConfig(({ command }) => ({
  base: command === "serve" ? "/" : "/static/",
  plugins: [react(), tailwindcss()],
  build: { outDir: "dist", emptyOutDir: true },
  server: { proxy: { "/api": apiOrigin, "/health": apiOrigin, "/verify": apiOrigin, "/metrics": apiOrigin } },
}));
