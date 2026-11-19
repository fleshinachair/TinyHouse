// Runs src/ui/cards.js (the wavetable card raised by the Tone page's position knobs) against a mock frame.
//
//   node tests/cards_test.mjs [--ascii]
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import vm from "node:vm";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
vm.runInThisContext(readFileSync(join(root, "src/ui/cards.js"), "utf8"), { filename: "cards.js" });
const T = globalThis.TINYHOUSE_CARDS_FOR_TEST;

let failures = 0;
const check = (cond, what) => { if (!cond) { failures++; console.log("  FAIL", what); } };

const W = 118, H = 52;                              // inside the 124 x 58 card
function mockCtx() {
    const px = new Uint8Array(W * H);
    let oob = 0, calls = 0; const texts = [];
    return { width: W, height: H, px, texts,
        get calls() { return calls; }, get oob() { return oob; },
        fillRect(x, y, w, h, c) { calls++; if (x < 0 || y < 0 || x + w > W || y + h > H) oob++;
            for (let j = Math.max(0, y); j < Math.min(H, y + h); j++) for (let i = Math.max(0, x); i < Math.min(W, x + w); i++) px[j * W + i] = c ? 1 : 0; },
        print(x, y, s, c) { calls++; texts.push(s); }, textWidth(s) { return String(s).length * 6; }, setPixel() {} };
}
const Q = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-_";
// a table of 16 frames x 64 samples: frame f is a sine with f+1 cycles (so each frame looks different)
function digest(kind) {
    const F = 16, S = 64; let s = "";
    for (let f = 0; f < F; f++) for (let i = 0; i < S; i++) {
        const v = kind === "saw" ? (i / S) : 0.5 + 0.5 * Math.sin(2 * Math.PI * (f + 1) * i / S);
        s += Q[Math.max(0, Math.min(63, Math.round(v * 63)))];
    }
    return F + "," + S + "," + s;
}
const values = { wt1_pos: 10, wt2_pos: 64, wt3_pos: 127, wt1_shape: digest("sin"), wt2_shape: digest("saw"), wt3_shape: digest("sin"), wt_on: "1,1,1" };
const run = (which, now, o = {}) => { const c = mockCtx(); T.draw(c, { values, nowMs: now, value: "10", raw: values["wt" + (which + 1) + "_pos"], ...o }, which); return c; };
const lit = (c) => c.px.reduce((a, v) => a + v, 0);
const same = (a, b) => a.px.every((v, i) => v === b.px[i]);

T.reset();
const one = run(0, 1000);
check(lit(one) > 200 && one.oob === 0, "one knob: a wave fills the card, inside it");
check(one.texts[0].startsWith("WT1"), "one knob: header names the oscillator, got " + one.texts[0]);
check(one.calls <= 140, "one knob costs " + one.calls + " draw calls (<= 140)");
// the lone panel is a stacked waterfall: lit pixels in the upper rows too (the later frames sit behind and above the front one)
check(one.px.slice(10 * W, 24 * W).some((v) => v), "one knob: the waterfall reaches up behind the front frame");
// every plan stays inside the rectangle budget, whatever the table
for (const kind of ["sin", "saw"]) { T.reset(); const c = run(kind === "sin" ? 0 : 1, 3000, { raw: 77 }); check(c.calls <= 140, kind + " waterfall costs " + c.calls + " calls"); }
// the wave follows the position
check(!same(run(0, 1016, { raw: 10 }), run(0, 1032, { raw: 60 })), "the wave follows the position");
// a second knob in the same gesture: the card splits in two, both waves drawn
T.reset(); run(0, 2000); const two = run(1, 2016);
check(two.oob === 0 && two.calls <= 180, "two knobs: inside the card, " + two.calls + " calls");
check(two.texts.length >= 2 && two.texts[0].includes("1") && two.texts[1].includes("2"), "two knobs: both oscillators are labelled " + JSON.stringify(two.texts));
// a second knob turned while the first one's card is the one up (the host only draws the last touched): it joins through its position
T.reset(); run(0, 2500); const v2 = { ...values, wt2_pos: 90 };
{ const c = mockCtx(); T.draw(c, { values: v2, nowMs: 2516, raw: 10, value: "10" }, 0);
  check(c.texts.length >= 2 && c.texts.some((x) => x.includes("2")), "a knob that moved in the value map joins the card " + JSON.stringify(c.texts)); }
{ const c = mockCtx(); T.draw(c, { values: v2, nowMs: 2532, raw: 10, value: "10" }, 0);
  check(c.texts.length >= 2, "a panel stays while the card is up even if its knob has stopped"); }
// a position that was only ever static never joins
T.reset(); run(0, 2600); const still = run(0, 2616);
check(still.texts.filter((x) => x.startsWith("WT")).length === 1, "unmoved knobs do not join " + JSON.stringify(still.texts));
// all three
T.reset(); run(0, 2800); run(1, 2816); const three = run(2, 2832);
check(three.oob === 0 && three.calls <= 200, "three knobs: inside the card, " + three.calls + " calls");
check(three.texts.length >= 3, "three knobs: three headers " + JSON.stringify(three.texts));
// a quiet gap ends the gesture: the next draw is a lone panel again
const again = run(1, 5000);
check(again.texts[0].includes("2") && !again.texts.some((s) => s.includes("WT1") || s.includes("WT3")), "after a pause the card is one panel again " + JSON.stringify(again.texts));
// an oscillator that is off, a missing digest, a missing position: a dash, no throw
T.reset(); const off = mockCtx(); T.draw(off, { values: { ...values, wt_on: "0,1,1" }, nowMs: 9000, raw: 5 }, 0);
check(lit(off) < 40 && off.oob === 0, "an oscillator that is off is a dash");
const nod = mockCtx(); T.reset(); T.draw(nod, { values: {}, nowMs: 9000, raw: null }, 0);
check(lit(nod) < 40 && nod.oob === 0, "no digest and no position: a dash");
check((() => { try { T.reset(); T.draw(mockCtx(), undefined, 0); return true; } catch (e) { return false; } })(), "no payload does not throw");
// prefixed keys from the host
T.reset(); const pre = mockCtx(); T.draw(pre, { values: { "synth:wt1_shape": digest("sin"), "synth:wt1_pos": 30 }, nowMs: 9500, raw: null }, 0);
check(lit(pre) > 100, "host-prefixed keys are found");

if (process.argv.includes("--ascii")) {
    for (const [name, c] of [["one", one], ["two", two], ["three", three]]) {
        console.log("--- " + name);
        for (let y = 0; y < H; y += 2) console.log([...Array(W).keys()].map((x) => (c.px[y * W + x] || c.px[(y + 1) * W + x]) ? "#" : ".").join(""));
    }
}
console.log(failures ? failures + " failures" : "cards ok");
process.exit(failures ? 1 : 0);
