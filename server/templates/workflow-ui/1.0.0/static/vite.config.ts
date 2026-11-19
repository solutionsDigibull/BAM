import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Relative base: the bundle is served from whatever path the host gives it.
export default defineConfig({ base: "./", plugins: [react()], server: { port: 5190 } });
