import React from "react";
import ReactDOM from "react-dom/client";

import App from "@/App";
import { bootstrapDesktopBackend } from "@/lib/desktop";
import "@/styles/globals.css";

const container = document.getElementById("root");

if (!container) {
  throw new Error("Root container #root was not found in index.html");
}

const root = ReactDOM.createRoot(container);

async function start(): Promise<void> {
  // The packaged app learns the sidecar port from the Rust supervisor; the
  // browser dev build keeps the fixed VITE_BACKEND_PORT. Await it before the
  // first render so no request can hit the wrong port.
  await bootstrapDesktopBackend();

  // Dev-only fixture: `?demo` seeds a sample project so the timeline, table and
  // overlay can be worked on without a clip. Dropped from production builds.
  if (import.meta.env.DEV && new URLSearchParams(window.location.search).has("demo")) {
    await import("@/dev/demoProject").then((module) => module.seedDemoProject());
  }

  root.render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
}

void start();
