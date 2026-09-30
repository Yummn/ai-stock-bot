import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig(({ mode }) => ({
  plugins: [vue()],
  define: { "import.meta.env.VITE_INVESTOR": JSON.stringify(mode === "investor" ? "1" : "0") },
  build: {
    outDir:
      mode === "investor"
        ? "../src/ai_stock_bot/v4/static"
        : "../src/ai_stock_bot/v2/static",
    emptyOutDir: true,
  },
  server: { proxy: { "/api": "http://127.0.0.1:8765" } },
}));
