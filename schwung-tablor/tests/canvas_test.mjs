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
check(Object.keys(kinds).length === 14, "fourteen widget kinds (8 macros, 3 positions, waves, fx chain, grain)");
for (let i = 1; i <= 8; i++) check(!!kinds["custom:tinyhouse_mac" + i] && kinds["custom:tinyhouse_mac" + i].nominal.w === 32, "kind mac" + i);
const run = (i, values) => { const c = mockCtx(); kinds["custom:tinyhouse_mac" + i].draw(c, { values }); return c; };

const names = "Cutoff||More Delay|||||";
// names not read yet or a used macro: a live dial; an unused one: an empty slot (a single dash, no ring, no pointer)
const live0 = run(1, { mac1: 0 }), live127 = run(1, { mac1: 127 }), live64 = run(1, { mac1: 64 });
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
check(same(run(1, { macro_names: "a", mac1: 400 }), run(1, { macro_names: "a", mac1: 127 })), "value clipped to 127");
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

// ---------------------------------------------------------------- the macro cell with its name, and the pictures across a row
function ctxOf(w, h) {
    const px = new Uint8Array(w * h);
    const texts = [];
    let calls = 0, oob = 0;
    return {
        width: w, height: h, px, texts,
        get calls() { return calls; }, get outOfBounds() { return oob; },
        fillRect(x, y, ww, hh, c) {
            calls++;
            if (x < 0 || y < 0 || x + ww > w || y + hh > h) oob++;
            for (let j = Math.max(0, y); j < Math.min(h, y + hh); j++) for (let i = Math.max(0, x); i < Math.min(w, x + ww); i++) px[j * w + i] = c ? 1 : 0;
        },
        print(x, y, t, c) { texts.push([x, y, String(t), c]); },
        textWidth(t) { return String(t).length * 6; }, setPixel() {},
    };
}
const show = (c, title) => { if (process.env.SHOW) { console.log(title); for (let j = 0; j < c.height; j++) console.log("  " + Array.from(c.px.slice(j * c.width, (j + 1) * c.width)).map((v) => (v ? "#" : ".")).join("")); for (const t of c.texts) console.log("  text", JSON.stringify(t)); } };
const kd = (name) => kinds["custom:tinyhouse_" + name];

// a macro with a name: the name (cut to the cell, upper case) and a bar; names not read yet or no macro: as before
{
    const c = ctxOf(32, 15);
    kd("mac1").draw(c, { values: { macro_names: "Cutoff||More Delay|||||", mac1: 64 } });
    check(c.texts.length === 1 && c.texts[0][2].length >= 1 && c.texts[0][2] === c.texts[0][2].toUpperCase(), "a named macro prints its name once, upper case");
    check(c.texts[0][2].length * 6 <= 31, "the name is cut to the cell (" + c.texts[0][2] + ")");
    check(c.calls <= 8 && c.outOfBounds === 0, "a named macro costs " + c.calls + " calls (<= 8), inside the frame");
    const lo = ctxOf(32, 15), hi = ctxOf(32, 15);
    kd("mac1").draw(lo, { values: { macro_names: "Cutoff", mac1: 0 } }); kd("mac1").draw(hi, { values: { macro_names: "Cutoff", mac1: 127 } });
    check(hi.px.reduce((a, v) => a + v, 0) > lo.px.reduce((a, v) => a + v, 0), "the bar fills with the value");
    show(c, "macro 1 'Cutoff' at 64");
    const u = ctxOf(32, 15);
    kd("mac2").draw(u, { values: { macro_names: "Cutoff||More Delay|||||", mac2: 64 } });
    check(u.texts.length === 0 && u.calls === 1, "an unused macro is still the dash, no text");
}

// the wavetable digest the DSP publishes: "frames,samples,data" over the 64-level alphabet
const Qs = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-_";
function waveDigest(frames, samples, fn) {
    let t = frames + "," + samples + ",";
    for (let f = 0; f < frames; f++) for (let i = 0; i < samples; i++) t += Qs[Math.max(0, Math.min(63, Math.round(((fn(f / Math.max(1, frames - 1), i / samples) + 1) / 2) * 63)))];
    return t;
}
// frame 0 a sine, the last frame a square: a wave that visibly changes with the position
const morph = waveDigest(24, 64, (p, x) => (1 - p) * Math.sin(2 * Math.PI * x) + p * (x < 0.5 ? 1 : -1));
const saw = waveDigest(24, 64, (p, x) => 2 * x - 1);
{
    const T = globalThis.TINYHOUSE_CANVAS_FOR_TEST;
    check(T.parseDigest(morph, true) && T.parseDigest(morph, true).frames === 24, "a wavetable digest parses");
    check(T.parseDigest("", true) === null && T.parseDigest("junk", true) === null && T.parseDigest(undefined, true) === null, "an empty or junk digest is no wave");
    const base = { wt1_shape: morph, wt2_shape: saw, wt3_shape: morph, wt_on: "1,1,1" };
    const draw = (v) => { const c = ctxOf(96, 15); kd("waves").draw(c, { values: Object.assign({}, base, v) }); return c; };
    const a = draw({ wt1_pos: 0, wt2_pos: 0, wt3_pos: 127 });
    show(a, "waves: sine / saw / square");
    const lit1 = (c) => c.px.reduce((x, v) => x + v, 0);
    check(lit1(a) > 40 && a.outOfBounds === 0, "three waves draw inside the frame (" + lit1(a) + " pixels)");
    check(a.calls <= 90, "three waves cost " + a.calls + " calls (<= 90)");
    const b0 = draw({ wt1_pos: 0, wt2_pos: 0, wt3_pos: 0 }), b1 = draw({ wt1_pos: 127, wt2_pos: 0, wt3_pos: 0 });
    check(!same(b0, b1), "turning WT1's position changes the picture");
    const third = (c, i) => Array.from(c.px).filter((v, k) => v && k % 96 >= i * 32 && k % 96 < i * 32 + 30).length;
    check(third(b0, 1) === third(b1, 1) && third(b0, 2) === third(b1, 2), "the other two waves do not move");
    const off = draw({ wt_on: "1,0,1", wt1_pos: 0, wt2_pos: 0, wt3_pos: 0 });
    check(third(off, 1) === 5, "an oscillator that is off is a five pixel dash (" + third(off, 1) + ")");
    const none = ctxOf(96, 15); kd("waves").draw(none, { values: { wt1_pos: 3 } });
    check(none.outOfBounds === 0 && none.calls === 3, "digests not read yet: three dashes");
    kd("waves").draw(ctxOf(96, 15), undefined);
    const warm = a.calls; const again = draw({ wt1_pos: 0, wt2_pos: 0, wt3_pos: 127 });
    check(again.calls === warm && same(again, a), "the same inputs draw the same picture (cached)");
}

// the Effects page's chain strip
{
    const draw = (v) => { const c = ctxOf(128, 15); kd("fxchain").draw(c, { values: v }); return c; };
    const allOn = draw({ eq_on: "On", wide_on: "On", dly_on: "On", rev_on: "On" }), allOff = draw({ eq_on: "Off", wide_on: "Off", dly_on: "Off", rev_on: "Off" });
    show(allOn, "chain all on"); show(draw({ eq_on: 1, wide_on: 0, dly_on: "On", rev_on: "Off" }), "chain: EQ on, WID off, DLY on, REV off");
    check(allOn.texts.map((t) => t[2]).join(",") === "EQ,WID,DLY,REV", "four labels in chain order");
    check(allOn.texts.every((t) => t[3] === 0) && allOff.texts.every((t) => t[3] === 1), "a stage that is on prints its label dark on a filled box, off lit on an outline");
    check(allOn.px.reduce((a, v) => a + v, 0) > allOff.px.reduce((a, v) => a + v, 0) + 100, "on is filled, off is an outline");
    check(allOn.outOfBounds === 0 && allOff.outOfBounds === 0 && allOn.calls <= 12 && allOff.calls <= 24, "inside the frame, cheap (" + allOn.calls + " / " + allOff.calls + " calls)");
    check(draw({}).texts.length === 4, "values not read yet: the four boxes are drawn (on)");
    kd("fxchain").draw(ctxOf(128, 15), undefined);
}

// a Grain page's first line
{
    const cols = 96; let t = cols + ","; for (let i = 0; i < cols; i++) t += Qs[Math.round(63 * Math.exp(-i / 30) * (0.6 + 0.4 * Math.abs(Math.sin(i))))];
    const draw = (v, keys) => { const c = ctxOf(128, 15); kd("grain").draw(c, { values: v, group: { keys: keys || ["smp_gdens", "smp_glen", "smp_gscan", "smp_gstart"] } }); return c; };
    const gran = { smp_shape: t, smp_kind: "Granular", smp_level: 100, smp_gstart: 25, smp_gscan: 100 };
    const g = draw(gran); show(g, "grain: start 25 %, scan forward");
    check(g.outOfBounds === 0 && g.px.reduce((a, v) => a + v, 0) > 80, "a granular slot draws inside the frame");
    check(g.calls <= 120, "a granular slot costs " + g.calls + " calls (<= 120)");
    const plain = draw(Object.assign({}, gran, { smp_kind: "Sample" })); show(plain, "plain sample: no marker");
    check(!same(g, plain), "Granular adds the start marker and the scan arrow");
    check(!same(draw(Object.assign({}, gran, { smp_gstart: 70 })), g), "the start marker follows Start");
    check(!same(draw(Object.assign({}, gran, { smp_gscan: -100 })), g), "the arrow turns with the scan direction");
    const still = draw(Object.assign({}, gran, { smp_gscan: 0 })); check(!same(still, g), "a scan rate of 0 has no arrow");
    const idx = draw(Object.assign({}, gran, { smp_kind: 1 })); check(same(idx, g), "Kind as an index reads the same as its name");
    const off = draw(Object.assign({}, gran, { smp_level: 0 })); check(off.calls === 1, "level 0: a dash");
    const none = draw({ smp_kind: "Granular", smp_level: 100 }); check(none.calls === 1, "no sample read: a dash");
    const s2 = draw({ smp2_shape: t, smp2_kind: "Granular", smp2_level: 80, smp2_gstart: 10 }, ["smp2_gdens", "smp2_glen", "smp2_gscan", "smp2_gstart"]); check(s2.calls > 5, "slot 2 reads its own keys");
    kd("grain").draw(ctxOf(128, 15), undefined);
}

console.log(failures ? `canvas_test: ${failures} of ${checks} checks FAILED` : `canvas_test: ${checks} checks passed`);
process.exit(failures ? 1 : 0);
