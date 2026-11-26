"use strict";
// The window's theme (#87). Loaded as a plain blocking script in <head>, before the stylesheet, so the first
// paint is already in the right theme (the CSP forbids the inline script that is the usual way).
// The pure part has no DOM and is run by `node` (tests/app/test_theme.py); `applyTheme` and `bootTheme` are the
// DOM part and run only where a `document` exists. Two states (Decision 7): light and dark; a stored choice wins
// over the system until the browser's storage for this origin is cleared.
(function (root) {
  const KEY = "wf-theme";
  const THEMES = ["light", "dark"];

  // The stored choice, or null for nothing, a value that is not a theme, a missing storage, a storage that throws.
  function readTheme(storage) {
    try {
      const value = storage.getItem(KEY);
      return THEMES.includes(value) ? value : null;
    } catch (e) {
      return null;
    }
  }

  // true when written; false when the storage is missing or refuses (private window, blocked site data).
  function saveTheme(storage, theme) {
    try {
      storage.setItem(KEY, theme);
      return true;
    } catch (e) {
      return false;
    }
  }

  const effectiveTheme = (stored, systemDark) =>
    THEMES.includes(stored) ? stored : (systemDark ? "dark" : "light");

  const nextTheme = (theme) => (theme === "dark" ? "light" : "dark");

  // Mermaid's `themeVariables` from the tokens of the theme (a plain object `{"--name": value}`), so the
  // diagram's pairs are the token pairs the contrast test already covers.
  function mermaidVariables(tokens) {
    return {
      primaryColor: tokens["--surface"], primaryTextColor: tokens["--fg"], textColor: tokens["--fg"],
      primaryBorderColor: tokens["--edge"], lineColor: tokens["--dim"], secondaryColor: tokens["--surface2"],
      tertiaryColor: tokens["--bg"], background: tokens["--bg"], edgeLabelBackground: tokens["--surface"],
    };
  }

  function applyTheme(doc, theme) {
    doc.documentElement.dataset.theme = theme;
    doc.dispatchEvent(new CustomEvent("wf-theme", { detail: { theme } }));
  }

  // The window's storage, or null: the accessor itself can throw (blocked site data).
  function storageOf(win) {
    try { return win.localStorage; } catch (e) { return null; }
  }

  function bootTheme(win) {
    const storage = storageOf(win);
    const system = win.matchMedia("(prefers-color-scheme: dark)");
    const stored = readTheme(storage);
    let applied = effectiveTheme(stored, system.matches);
    applyTheme(win.document, applied);
    if (stored === null) {   // nothing chosen: follow the system while it changes, until a choice is made
      system.addEventListener("change", () => {
        // A click changed the theme since the system last did: the choice stays for this page (Decision 7),
        // even where the storage refused to keep it.
        if (win.document.documentElement.dataset.theme !== applied) return;
        applied = effectiveTheme(null, system.matches);
        applyTheme(win.document, applied);
      });
    }
  }

  const api = { readTheme, saveTheme, storageOf, effectiveTheme, nextTheme, mermaidVariables, applyTheme, bootTheme };
  root.WfTheme = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document !== "undefined" && typeof window !== "undefined") bootTheme(window);
})(globalThis);
