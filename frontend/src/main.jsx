import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import { AssistantProvider } from "./state/AssistantContext.jsx";
import "./index.css";

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <AssistantProvider>
      <App />
    </AssistantProvider>
  </StrictMode>
);
