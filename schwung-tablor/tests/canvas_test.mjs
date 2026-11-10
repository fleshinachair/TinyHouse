// Runs src/ui/canvas.js (the Macros page cells) against a mock frame, the way Schwung's widget_registry hands one to a custom widget.
//
//   node tests/canvas_test.mjs
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import vm from "node:vm";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
vm.runInThisContext(readFileSync(join(root, "src/ui/canvas.js"), "utf8"), { filename: "canvas.js" });

let failures = 0, checks = 0;
const check = (cond, what) => { checks++; if (!cond) { failures++; console.log("  FAIL", what); } };

const W = 32, H = 15;
function mockCtx() {
    const texts = [], cleared = new Set();
    return {
        width: W, height: H, texts, cleared,
        fillRect() {}, print(x, y, s) { texts.push(String(s)); },
        textWidth(s) { return String(s).length * 6; },
        setPixel(x, y, c) { if (x < 0 || y < 0 || x >= W || y >= H) throw new Error("pixel out of frame"); if (!c) cleared.add(x + "," + y); },
    };
}
const kinds = globalThis.canvas_overlay.widgetKinds;
check(Object.keys(kinds).length === 8, "eight widget kinds");
for (let i = 1; i <= 8; i++) check(!!kinds["custom:tinyhouse_mac" + i] && kinds["custom:tinyhouse_mac" + i].nominal.w === 32, "kind mac" + i);
const run = (i, values) => { const c = mockCtx(); kinds["custom:tinyhouse_mac" + i].draw(c, { values }); return c; };

const T = globalThis.TINYHOUSE_CANVAS_FOR_TEST;
const runAt = (i, values, nowMs) => { const c = mockCtx(); c.prints = []; const pr = c.print; c.print = (x, y, s) => { c.prints.push([x, String(s)]); pr(x, y, s); }; kinds["custom:tinyhouse_mac" + i].draw(c, { values, nowMs }); return c; };

// names not read yet: the value, never dimmed
let c = run(1, { mac1: 64 });
check(c.texts[0] === "64" && c.cleared.size === 0, "names unread: value, not dimmed");
// a used macro shows its name (short: centred); an unused one is a dimmed N/A
const names = "Cut||Wet|||||";
c = run(1, { macro_names: names, mac1: "100" });
check(c.texts[0] === "Cut" && c.cleared.size === 0, "named macro 1: its name");
c = run(2, { macro_names: names, mac2: 30 });
check(c.texts[0] === "N/A" && c.cleared.size > 0, "unnamed macro 2: dimmed N/A");
c = run(3, { macro_names: names, mac3: 0 });
check(c.texts[0] === "Wet" && c.cleared.size === 0, "named macro 3: its name");
c = run(8, { macro_names: names, mac8: 5 });
check(c.texts[0] === "N/A", "macro 8 beyond the list: N/A");
// a sound with no macros: the list is empty once read, so every knob dims
for (let i = 1; i <= 8; i++) check(run(i, { macro_names: "", mac1: 1 }).texts[0] === "N/A", "no macros: knob " + i + " dims");
// a long name scrolls: rests at the start, runs left, rests at the end; never a pixel outside the frame
const long = "MORE DELAY||RETRO COLOR";
const w = "MORE DELAY".length * 6, over = w - (W - 2);
check(T.scrollX(over, 0) === 0 && T.scrollX(over, 800) === 0, "scroll: rests at the start");
const run_ms = (over / 22) * 1000;
check(T.scrollX(over, 900 + run_ms / 2) < 0 && T.scrollX(over, 900 + run_ms / 2) > -over, "scroll: moves in between");
check(T.scrollX(over, 900 + run_ms + 100) === -over, "scroll: rests at the end");
check(T.scrollX(over, 1800 + run_ms + 5) === 0, "scroll: repeats");
check(runAt(1, { macro_names: long }, 0).prints[0][1] === "MORE DELAY" && runAt(1, { macro_names: long }, 0).prints[0][0] === 1, "long name: whole text, starts at the left");
check(runAt(1, { macro_names: long }, 900 + run_ms + 100).prints[0][0] === 1 - over, "long name: ends with its last letter in view");
check(run(1, { macro_names: long }).texts[0].length < "MORE DELAY".length, "long name without a clock: clipped, not scrolled");
// junk: no throw
check(run(1, { macro_names: "a", mac1: "x" }).texts[0] === "a", "junk value does not matter once named");
check(run(1, { mac1: "x" }).texts[0] === "0", "unread + junk value reads 0");
check(run(1, { mac1: 400 }).texts[0] === "127", "unread value clipped to 127");
check(run(1, undefined) !== null, "no payload does not throw");
for (let i = 1; i <= 8; i++) { const cc = mockCtx(); kinds["custom:tinyhouse_mac" + i].draw(cc, undefined); }

console.log(failures ? `canvas_test: ${failures} of ${checks} checks FAILED` : `canvas_test: ${checks} checks passed`);
process.exit(failures ? 1 : 0);
