import "@fontsource-variable/inter";
import "@fontsource-variable/jetbrains-mono";
// Serifa itálica da prancha: só em momentos de assinatura (Login e estados vazios). Ver DESIGN.md.
import "@fontsource/instrument-serif/400-italic.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App";
import { AuthProvider } from "./auth/AuthProvider";
import { PlateBackdrop } from "./components/PlateBackdrop";
import { ToastProvider } from "./components/Toaster";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <PlateBackdrop />
    <ToastProvider>
      <AuthProvider>
        <App />
      </AuthProvider>
    </ToastProvider>
  </StrictMode>,
);
