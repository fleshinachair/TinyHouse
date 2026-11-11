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
    const px = new Uint8Array(W * H);
    let outOfBounds = 0, calls = 0;
    return {
        width: W, height: H, px,
        get calls() { return calls; },
        get outOfBounds() { return outOfBounds; },
        fillRect(x, y, w, h, c) {
            calls++;
            if (x < 0 || y < 0 || x + w > W || y + h > H) outOfBounds++;
            for (let j = Math.max(0, y); j < Math.min(H, y + h); j++) for (let i = Math.max(0, x); i < Math.min(W, x + w); i++) px[j * W + i] = c ? 1 : 0;
        },
        print() {}, textWidth(s) { return String(s).length * 6; }, setPixel() {},
    };
}
const lit = (c) => c.px.reduce((a, v) => a + v, 0);
const kinds = globalThis.canvas_overlay.widgetKinds;
check(Object.keys(kinds).length === 11, "eleven widget kinds (8 macros, 3 positions)");
for (let i = 1; i <= 8; i++) check(!!kinds["custom:tinyhouse_mac" + i] && kinds["custom:tinyhouse_mac" + i].nominal.w === 32, "kind mac" + i);
const run = (i, values) => { const c = mockCtx(); kinds["custom:tinyhouse_mac" + i].draw(c, { values }); return c; };

const names = "Cutoff||More Delay|||||";
// names not read yet or a used macro: a live dial; an unused one: an empty slot (a single dash, no ring, no pointer)
const live0 = run(1, { mac1: 0 }), live127 = run(1, { macro_names: names, mac1: 127 }), live64 = run(1, { macro_names: names, mac1: 64 });
const grey = run(2, { macro_names: names, mac2: 64 });
check(lit(live0) > 20 && lit(live127) > 20, "a live dial draws a ring and a pointer");
check(lit(grey) === 5 && grey.calls === 1, "an unused macro is one dash (" + lit(grey) + " pixels in " + grey.calls + " call)");
// the cost: the host turns every primitive into a bridge call and a knob turn redraws the page, so a dial must stay cheap
for (const c of [live0, live127, live64]) check(c.calls <= 20, "a live dial costs " + c.calls + " draw calls (<= 20)");
// the pointer follows the value: 0 and 127 and 64 point to different places
const same = (a, b) => a.px.every((v, i) => v === b.px[i]);
check(!same(live0, live127) && !same(live127, live64) && !same(live0, live64), "the pointer follows the value");
// the dial sits centred in the cell and stays inside it
check(live64.px[7 * W + 16] === 1, "the pointer starts at the centre of the cell");
check(grey.px[7 * W + 16] === 1 && grey.px[7 * W + 14] === 1 && grey.px[7 * W + 18] === 1, "the dash is centred in the cell");
for (const c of [live0, live127, live64, grey]) check(c.outOfBounds === 0, "nothing drawn outside the frame");
// an unused macro's value is irrelevant
check(same(run(2, { macro_names: names, mac2: 0 }), run(2, { macro_names: names, mac2: 127 })), "unused: value does not matter");
// no macros at all: every dial is grey; macro 8 beyond the list: grey
for (let i = 1; i <= 8; i++) check(same(run(i, { macro_names: "", mac1: 1 }), grey), "no macros: dial " + i + " is grey");
check(same(run(8, { macro_names: names }), grey), "macro 8 beyond the list: grey");
// junk: no throw, a value past the range is clipped
check(same(run(1, { macro_names: "a", mac1: 400 }), live127), "value clipped to 127");
check(same(run(1, { macro_names: "a", mac1: "x" }), run(1, { macro_names: "a", mac1: 0 })), "junk reads as 0");
check(run(1, undefined) !== null, "no payload does not throw");
for (let i = 1; i <= 8; i++) { const cc = mockCtx(); kinds["custom:tinyhouse_mac" + i].draw(cc, undefined); }

// the Tone page's positions: the same dial, an empty slot while the oscillator's level is 0 (live until the level has been read)
const pos = (i, values) => { const c = mockCtx(); kinds["custom:tinyhouse_pos" + i].draw(c, { values }); return c; };
for (let i = 1; i <= 3; i++) {
    check(kinds["custom:tinyhouse_pos" + i].nominal.w === 32, "kind pos" + i);
    const on = pos(i, { ["wt" + i + "_pos"]: 127, ["wt" + i + "_level"]: "100" });
    const off = pos(i, { ["wt" + i + "_pos"]: 127, ["wt" + i + "_level"]: "0" });
    check(same(on, live127), "pos" + i + ": level > 0 is a live dial");
    check(same(off, grey) && off.calls === 1, "pos" + i + ": level 0 is the empty slot");
    check(same(pos(i, { ["wt" + i + "_pos"]: 127 }), live127), "pos" + i + ": level not read yet is a live dial");
    check(same(pos(i, { ["wt" + i + "_pos"]: 0, ["wt" + i + "_level"]: 0 }), off), "pos" + i + ": the value does not matter when off");
}
check(same(pos(3, { wt3_pos: 127, wt1_level: "0", wt3_level: "50" }), live127), "pos3 follows its own level, not another's");
kinds["custom:tinyhouse_pos1"].draw(mockCtx(), undefined);

console.log(failures ? `canvas_test: ${failures} of ${checks} checks FAILED` : `canvas_test: ${checks} checks passed`);
process.exit(failures ? 1 : 0);
