import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/inter/700.css";
import React from "react";
import { createRoot } from "react-dom/client";
import { Refine } from "@refinedev/core";
import App from "./App";
import { workflowProvider } from "./provider";
import "./theme.css";

// The data provider is the Workflow's own server (src/provider.ts): no mock data.
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Refine dataProvider={workflowProvider} options={{ disableTelemetry: true }}>
      <App />
    </Refine>
  </React.StrictMode>
);
