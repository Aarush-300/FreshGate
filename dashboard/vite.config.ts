import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const apiOrigin = process.env.FRESHGATE_API_ORIGIN ?? (process.env.FRESHGATE_PORT ? `http://127.0.0.1:${process.env.FRESHGATE_PORT}` : "http://127.0.0.1:8127");

export default defineConfig(({ command }) => ({
  base: command === "serve" ? "/" : "/static/",
  plugins: [react(), tailwindcss()],
  build: { outDir: "dist", emptyOutDir: true },
  server: { proxy: { "/api": apiOrigin, "/health": apiOrigin, "/verify": apiOrigin, "/metrics": apiOrigin } },
}));
