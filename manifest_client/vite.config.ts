import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// html.cspNonce lets Vite/plugin-react tag their injected dev scripts so the
// CSP meta in index.html (script-src 'self' 'nonce-manifest-dev') stays strict
// in production while dev-mode HMR keeps working.
export default defineConfig({
  plugins: [tailwindcss(), react()],
  html: { cspNonce: "manifest-dev" },
});
