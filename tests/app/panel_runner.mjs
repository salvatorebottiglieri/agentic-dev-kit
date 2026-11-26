// Runs functions of app/static/panel.js on JSON cases: stdin `[[fn, [args...]], ...]`, stdout the
// JSON list of results. A `{"$date": iso}` value in the arguments becomes a Date, a `{"$set": [...]}` a Set
// (and a Set in a result is written back as `{"$set": [...]}`). A name `$observe:fn` runs `fn` and answers
// `{result, args}`: the arguments as they are AFTER the call, so an in-place change is visible. Built-ins only.
import { createRequire } from "node:module";
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const file = resolve(here, "../../app/static/panel.js");
if (!existsSync(file)) {
  console.error("panel.js missing: " + file);
  process.exit(2);
}
const WfPanel = createRequire(import.meta.url)(file);
const revive = (_key, value) => {
  if (value === null || typeof value !== "object") return value;
  if ("$date" in value) return new Date(value.$date);
  return "$set" in value ? new Set(value.$set) : value;
};
const unset = (_key, value) => (value instanceof Set ? { $set: [...value] } : value);
const cases = JSON.parse(readFileSync(0, "utf8"), revive);
const results = cases.map(([rawName, args]) => {
  const observe = rawName.startsWith("$observe:");
  const name = observe ? rawName.slice("$observe:".length) : rawName;
  if (!(name in WfPanel)) return { $error: `WfPanel.${name} is not exported` };
  try {
    const out = typeof WfPanel[name] === "function" ? WfPanel[name](...args) : WfPanel[name];   // a constant is read as it is
    if (observe) return { result: out === undefined ? null : out, args };
    return out === undefined ? { $undefined: true } : out;
  } catch (error) {
    return { $error: String(error && error.stack || error) };
  }
});
process.stdout.write(JSON.stringify(results, unset));
