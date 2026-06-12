import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import { lang } from "./i18n";
import "./index.css";

// index.html ships with lang="ar" dir="rtl"; flip both when English is chosen
// so the whole layout mirrors (Tailwind logical utilities follow `dir`).
document.documentElement.lang = lang;
document.documentElement.dir = lang === "ar" ? "rtl" : "ltr";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
