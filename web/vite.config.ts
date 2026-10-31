import { defineConfig } from "vite";

export default defineConfig({
  base: "/dashboard/",
  build: {
    outDir: "../src/cutback/dashboard",
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks: {
          "react-vendor": ["react", "react-dom", "react-dom/client"],
        },
      },
    },
  },
  server: { proxy: { "/v1": "http://127.0.0.1:8080" } },
});
