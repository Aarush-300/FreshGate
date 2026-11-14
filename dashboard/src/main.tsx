import "@fontsource-variable/ibm-plex-sans";
import "@fontsource-variable/source-serif-4";
import "./styles.css";
import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
