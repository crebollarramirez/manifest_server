import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./design-system/fonts.css";
import "./design-system/tailwind.css";
import { ThemeProvider } from "./design-system";

const container = document.getElementById("root");
if (!container) {
  throw new Error("Missing #root element.");
}

createRoot(container).render(
  <StrictMode>
    <ThemeProvider>
      <App />
    </ThemeProvider>
  </StrictMode>,
);
