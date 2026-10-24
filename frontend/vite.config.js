import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const api = "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: { "/session": api, "/metrics": api, "/graph": api, "/health": api },
  },
});
