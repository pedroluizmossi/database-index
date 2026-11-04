import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Em produção o FastAPI serve o build em /ui. Em dev (`pnpm dev`), /api vai para o servidor
// `code-index serve`. Troque o destino com CODE_INDEX_API se a porta for outra.
const apiTarget = process.env.CODE_INDEX_API ?? "http://127.0.0.1:8765";

export default defineConfig({
  base: "/ui/",
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/api": { target: apiTarget, changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
