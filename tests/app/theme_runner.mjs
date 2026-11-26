// Runs functions of app/static/theme.js on JSON cases: stdin `[[fn, [args...]], ...]`, stdout the JSON list of
// results. In the arguments, `{"$storage": {"items": {k: v}, "throws": "get"|"set"|"both"}}` becomes a fake
// Storage; a case that holds one answers `{"$result": out, "$items": {...}}` (the items after the call, so a
// write can be seen). `{"$undefined": true}` is an undefined argument. `bootScenario(spec, steps)` boots theme.js
// on a fake window (`spec`: {items, throws, systemDark}) and plays `steps` ("system:dark", "system:light",
// "click"), answering the data-theme after each step. Built-ins only.
import { createRequire } from "node:module";
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const file = resolve(here, "../../app/static/theme.js");
if (!existsSync(file)) {
  console.error("theme.js missing: " + file);
  process.exit(2);
}
const WfTheme = createRequire(import.meta.url)(file);

function fakeStorage(spec) {
  const items = { ...(spec.items || {}) };
  const refuses = (op) => spec.throws === op || spec.throws === "both";
  return {
    items,
    getItem(key) {
      if (refuses("get")) throw new Error("storage refuses to read");
      return Object.prototype.hasOwnProperty.call(items, key) ? items[key] : null;
    },
    setItem(key, value) {
      if (refuses("set")) throw new Error("storage refuses to write");
      items[key] = String(value);
    },
  };
}

function bootScenario(spec, steps) {
  const storage = fakeStorage(spec);
  const listeners = [];
  const system = { matches: Boolean(spec.systemDark), addEventListener: (_t, fn) => listeners.push(fn) };
  const doc = { documentElement: { dataset: {} }, dispatchEvent() {} };
  globalThis.CustomEvent = class { constructor(type, init) { this.type = type; this.detail = init && init.detail; } };
  WfTheme.bootTheme({ document: doc, localStorage: storage, matchMedia: () => system });
  const seen = [doc.documentElement.dataset.theme];
  for (const step of steps) {
    if (step === "click") {   // what the switch does: the other theme, remembered if the storage lets us, applied
      const next = WfTheme.nextTheme(doc.documentElement.dataset.theme);
      WfTheme.saveTheme(storage, next);
      WfTheme.applyTheme(doc, next);
    } else {
      system.matches = step === "system:dark";
      listeners.forEach((fn) => fn());
    }
    seen.push(doc.documentElement.dataset.theme);
  }
  return seen;
}
const scenarios = { bootScenario };

const storages = [];
const revive = (_key, value) => {
  if (value !== null && typeof value === "object" && "$storage" in value) {
    const made = fakeStorage(value.$storage);
    storages.push(made);
    return made;
  }
  if (value !== null && typeof value === "object" && "$undefined" in value) return undefined;
  return value;
};
const cases = JSON.parse(readFileSync(0, "utf8"), revive);
const results = cases.map(([name, args]) => {
  if (scenarios[name]) return scenarios[name](...args);
  if (typeof WfTheme[name] !== "function") return { $error: `WfTheme.${name} is not a function` };
  const held = args.filter((a) => a !== null && typeof a === "object" && typeof a.getItem === "function");
  try {
    const out = WfTheme[name](...args);
    const shown = out === undefined ? { $undefined: true } : out;
    return held.length ? { $result: shown, $items: held[0].items } : shown;
  } catch (error) {
    return { $error: String(error && error.stack || error) };
  }
});
process.stdout.write(JSON.stringify(results));
