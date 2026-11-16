// Runs the granulator picture of the remote UI (the grainview block of src/web_ui.html) against a recording canvas: it must not throw for any setting,
// must draw more grains for a higher Density, wider ones for a longer Length, and must keep inside a sensible cost per frame.
//
//   node tests/grainview_test.mjs
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import vm from "node:vm";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const html = readFileSync(join(root, "src/web_ui.html"), "utf8");
const a = html.indexOf("/* ------------------------------------------------------- the granulator picture");
const b = html.indexOf("function buildSample(page) {");
if (a < 0 || b < 0 || b < a) { console.log("  FAIL could not find the granulator block in web_ui.html"); process.exit(1); }
const block = html.slice(a, b);

let failures = 0, checks = 0;
const check = (cond, what) => { checks++; if (!cond) { failures++; console.log("  FAIL", what); } };

const V = {};
function recorder(w, h) {
    const rec = { fills: 0, strokes: 0, lines: 0, texts: [], calls: 0 };
    const g = new Proxy({}, {
        get(_, k) {
            if (k === "fillRect") return () => { rec.fills++; rec.calls++; };
            if (k === "stroke") return () => { rec.strokes++; rec.calls++; };
            if (k === "lineTo") return () => { rec.lines++; rec.calls++; };
            if (k === "fillText") return (t) => { rec.texts.push(t); rec.calls++; };
            return () => { rec.calls++; };
        },
        set() { return true; },
    });
    const c = { clientWidth: w, clientHeight: h, width: 0, height: 0, getContext: () => g };
    return { c, rec };
}
const ctx = vm.createContext({
    val: (k) => (k in V ? V[k] : 0),
    setVal: (k, v) => { V[k] = v; },
    label: (g, t) => g.fillText(t),
    GREEN: "91,229,132",
    el: () => ({}),
    performance: { now: () => 12345 },
    window: { devicePixelRatio: 1 },
    requestAnimationFrame: () => 0,
    Math, Number, String, Object, Array, parseInt, parseFloat, isFinite,
});
vm.runInContext(block + "\nthis.grainDraw = grainDraw; this.grainWin = grainWin; this.parseSmpDigest = parseSmpDigest; this.SMPSHAPE = SMPSHAPE;", ctx);

const Q = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-_";
let dig = "96,"; for (let i = 0; i < 96; i++) dig += Q[Math.round(63 * Math.exp(-i / 40) * (0.5 + 0.5 * Math.abs(Math.sin(i / 3))))];
const base = { smp_gdens: 64, smp_glen: 64, smp_gscan: 100, smp_gstart: 10, smp_gend: 90, smp_gloop: 0, smp_gloops: 0, smp_gloope: 100, smp_gman: 0, smp_gpos: 0, smp_gwin: 0, smp_gwinp: 100, smp_gskew: 0, smp_groff: 0, smp_grgain: 0 };
const run = (over, digest = dig) => {
    for (const k of Object.keys(V)) delete V[k];
    Object.assign(V, base, over);
    ctx.SMPSHAPE.smp = ctx.parseSmpDigest(digest);
    const { c, rec } = recorder(800, 168);
    const t0 = process.hrtime.bigint();
    ctx.grainDraw(c, "smp");
    rec.ms = Number(process.hrtime.bigint() - t0) / 1e6;
    return rec;
};
const alive = (rec) => parseInt((rec.texts.find((t) => /grains alive/.test(t)) || "0").split(" ")[0], 10);

check(ctx.parseSmpDigest("96," + "0".repeat(96)).length === 96 && ctx.parseSmpDigest("") === null && ctx.parseSmpDigest(undefined) === null, "the sample digest parses; empty and missing are null");
const r0 = run({});
check(r0.calls > 100 && alive(r0) > 0, "a default granular slot draws a picture with grains (" + r0.calls + " calls, " + alive(r0) + " alive)");
check(r0.texts.some((t) => /illustration/.test(t)), "the picture says it is an illustration");
check(alive(run({ smp_gdens: 120 })) > alive(run({ smp_gdens: 20 })), "more Density: more grains alive");
check(alive(run({ smp_gdens: 0 })) === 0, "Density 0: no grains");
check(run({ smp_glen: 127 }).lines >= run({ smp_glen: 20 }).lines - 1, "a longer Length does not draw fewer grain outlines");
check(run({}, "").texts.some((t) => /no sample/.test(t)), "no sample digest: it says so");
for (const win of [0, 1, 2, 3, 4]) check(run({ smp_gwin: win, smp_gwinp: 50, smp_gskew: 40 }).calls > 50, "window " + win + " draws");
for (const over of [{ smp_gscan: -200 }, { smp_gscan: 0 }, { smp_gscan: 200 }, { smp_gloop: 2, smp_gloops: 20, smp_gloope: 60 }, { smp_gman: 1, smp_gpos: 50 }, { smp_gstart: 100, smp_gend: 100 }, { smp_gstart: 80, smp_gend: 20 }, { smp_groff: 100, smp_grgain: 100 }, { smp_gdens: 127, smp_glen: 127 }, { smp_gdens: 127, smp_glen: 0 }])
    { const r = run(over); check(r.calls > 20 && Number.isFinite(r.ms), "settings " + JSON.stringify(over) + " draw without throwing (" + r.calls + " calls)"); }
check(run({}).ms < 25 && run({ smp_gdens: 127, smp_glen: 127 }).ms < 40, "a frame costs " + run({ smp_gdens: 127, smp_glen: 127 }).ms.toFixed(2) + " ms at the worst setting (< 40 ms in node)");
check(run({ smp_gdens: 127, smp_glen: 127 }).lines < 48 * 22 + 200, "the grain count is capped");
const w = ctx.grainWin;
check(Math.abs(w(0, 100, 0, 0.5) - 1) < 1e-6 && w(0, 100, 0, 0) < 1e-6 && w(1, 50, 0, 0) === 1 && w(1, 50, 0, 1) < 0.05 && Math.abs(w(2, 100, 0, 0.5) - 1) < 1e-6, "the window formulas have their known points (Tukey/Hann peak and ends, ExpDec decay, Triangle peak)");

console.log(failures ? `grainview_test: ${failures} of ${checks} checks FAILED` : `grainview_test: ${checks} checks passed`);
process.exit(failures ? 1 : 0);
