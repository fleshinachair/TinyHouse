/*
 * TinyHouse's parameter cards (Schwung `card_script`): a near full-screen picture of the wavetables, raised while one of the three
 * Wavetable Position knobs (wt1_pos .. wt3_pos) is held or turned, gone when you let go (the host takes it down; this file owns no timer).
 *
 * One knob held: its oscillator's wave at the current position fills the card, with the frame number and a position bar.
 * Two or three knobs touched in the same gesture: the card splits into that many panels side by side, each with its own wave, and the
 * oscillator you are moving now has its label inverted. The host hands a card only the knob it raised the card for, so "touched in the
 * same gesture" is remembered here: an oscillator joins when its card is drawn and the set is cleared when the card has been down
 * for a moment (the draws are a frame apart while the card is up).
 *
 * The wave comes from the shape digests the DSP publishes (wt1_shape .. wt3_shape, "frames,samples,data", 63 levels a sample), the same
 * strings canvas.js draws on the Tone page. Nothing here reads a parameter: the card is handed the page's value map. An oscillator that
 * is off (wt_on), or a digest not read yet, is drawn as a dash; a missing position as a dash too.
 *
 * COST. A page render has 1.68 ms and every primitive is a call across the JS bridge, so each wave is worked out once per (digest, frame,
 * size) into a short list of merged rectangles and a draw only replays the list. At most about 120 rectangles for the whole card.
 *
 * Schwung loads every script into its own closure, so the helpers are repeated here rather than shared with canvas.js.
 */
(function () {
    const Q = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-_";
    const digestCache = {}, waveCache = {};
    const GAP_MS = 800;                       /* the card is redrawn every frame while it is up; a longer silence is a new gesture */
    let active = [false, false, false], lastMs = -1e12, lastWho = 0;

    /* the host may prefix keys ("synth:wt1_pos") or add ":effective" */
    function val(values, key) {
        if (!values) return undefined;
        if (values[key] !== undefined && values[key] !== null) return values[key];
        for (const k in values) {
            if (values[k] === null || values[k] === undefined) continue;
            const bare = k.indexOf(":") >= 0 ? k.slice(k.indexOf(":") + 1) : k;
            if (bare === key) return values[k];
        }
        return undefined;
    }
    const num = (v, d) => { const n = Number(v); return v !== undefined && v !== null && v !== "" && Number.isFinite(n) ? n : d; };

    function parseDigest(str) {
        if (typeof str !== "string" || !str) return null;
        const ck = str.length + ":" + str.slice(0, 24) + str.slice(-24);
        const hit = digestCache[ck];
        if (hit && hit.str === str) return hit.d;
        const parts = str.split(",");
        let d = null;
        if (parts.length >= 3) {
            const frames = parseInt(parts[0], 10), samples = parseInt(parts[1], 10), txt = parts.slice(2).join(",");
            if (frames > 0 && samples > 0 && txt.length >= frames * samples) {
                const data = new Uint8Array(frames * samples);
                for (let i = 0; i < data.length; i++) data[i] = Math.max(0, Q.indexOf(txt[i]));
                d = { frames, samples, data };
            }
        }
        const keys = Object.keys(digestCache);
        if (keys.length > 6) delete digestCache[keys[0]];
        digestCache[ck] = { str, d };
        return d;
    }

    function colsToRects(cols) {
        const out = [];
        for (const c of cols) {
            const x = c[0], y0 = c[1], y1 = c[2], last = out[out.length - 1];
            if (last && last[3] === y1 - y0 + 1 && last[1] === y0 && last[0] + last[2] === x) last[2]++;
            else out.push([x, y0, 1, y1 - y0 + 1]);
        }
        return out;
    }

    /* one wavetable frame as a line across w x h (a column spans from the previous column's level to its own, so a steep edge is solid) */
    function waveRects(d, frame, w, h, tag) {
        const key = tag + ":" + frame + ":" + w + "x" + h;
        const hit = waveCache[key];
        if (hit && hit.d === d) return hit.r;
        const base = frame * d.samples, cols = [];
        let prev = null;
        for (let x = 0; x < w; x++) {
            const a = Math.floor((x * d.samples) / w), b = Math.max(a + 1, Math.floor(((x + 1) * d.samples) / w));
            let lo = 63, hi = 0;
            for (let i = a; i < b; i++) { const v = d.data[base + i]; if (v < lo) lo = v; if (v > hi) hi = v; }
            let y0 = Math.round((1 - hi / 63) * (h - 1)), y1 = Math.round((1 - lo / 63) * (h - 1));
            if (prev !== null) { if (prev < y0) y0 = prev; else if (prev > y1) y1 = prev; }
            prev = Math.round((1 - ((lo + hi) / 2) / 63) * (h - 1));
            cols.push([x, y0, y1]);
        }
        const r = colsToRects(cols);
        const keys = Object.keys(waveCache);
        if (keys.length > 60) delete waveCache[keys[0]];
        waveCache[key] = { d, r };
        return r;
    }

    function draw(ctx, o, which) {
        o = o || {};
        const W = ctx.width, H = ctx.height, values = o && o.values;
        const now = num(o && o.nowMs, Date.now());
        if (now - lastMs > GAP_MS || now < lastMs) active = [false, false, false];
        active[which] = true; lastMs = now; lastWho = which;
        const idx = [0, 1, 2].filter((i) => active[i]);
        const n = idx.length, gap = 3, pw = Math.floor((W - gap * (n - 1)) / n);
        const on = val(values, "wt_on");
        const onList = typeof on === "string" ? on.split(",") : null;
        for (let k = 0; k < n; k++) {
            const i = idx[k], x0 = k * (pw + gap), cur = i === which;
            const pos = cur && o.raw !== null && o.raw !== undefined && Number.isFinite(Number(o.raw)) ? Number(o.raw) : num(val(values, "wt" + (i + 1) + "_pos"), null);   /* the knob in your hand is read live */
            const d = parseDigest(val(values, "wt" + (i + 1) + "_shape"));
            const off = onList !== null && onList[i] === "0";
            const frame = d && pos !== null ? Math.max(0, Math.min(d.frames - 1, Math.round((Math.max(0, Math.min(127, pos)) / 127) * (d.frames - 1)))) : 0;
            /* the header: "WT1" with the frame number, or just the digit when the panel is narrow; the one being turned is inverted */
            const head = "WT" + (i + 1), tail = d && pos !== null ? String(frame + 1) : "";
            let label = head;
            if (ctx.textWidth(head + " " + tail) > pw) label = String(i + 1);
            const text = tail && ctx.textWidth(label + " " + tail) <= pw ? label + " " + tail : label;
            const tw = ctx.textWidth(text);
            if (cur && n > 1) ctx.fillRect(x0, 0, Math.min(pw, tw + 2), 8, 1);
            ctx.print(x0 + (cur && n > 1 ? 1 : 0), 0, text, cur && n > 1 ? 0 : 1);
            if (n === 1 && d) {                                                         /* a lone panel has room for "of N" and the knob's own reading */
                const right = (typeof o.value === "string" ? o.value : "") || "";
                if (right) ctx.print(W - ctx.textWidth(right), 0, right, 1);
            }
            const top = 10, bar = 5, wh = H - top - bar - 2;
            if (off || !d || pos === null) { ctx.fillRect(x0 + (pw >> 1) - 3, top + (wh >> 1), 7, 1, 1); continue; }   /* off, not read yet, or no position: a dash */
            for (const q of waveRects(d, frame, pw, wh, "c" + i)) ctx.fillRect(x0 + q[0], top + q[1], q[2], q[3], 1);
            const by = H - bar;                                                         /* the position bar: an outline, a block at the frame */
            ctx.fillRect(x0, by, pw, 1, 1); ctx.fillRect(x0, by + bar - 1, pw, 1, 1);
            ctx.fillRect(x0, by, 1, bar, 1); ctx.fillRect(x0 + pw - 1, by, 1, bar, 1);
            const t = d.frames > 1 ? frame / (d.frames - 1) : 0, bw = 3;
            ctx.fillRect(x0 + 1 + Math.round(t * (pw - 2 - bw)), by + 1, bw, bar - 2, 1);
        }
    }

    globalThis.tinyhouse_wave_card_1 = (ctx, o) => draw(ctx, o, 0);
    globalThis.tinyhouse_wave_card_2 = (ctx, o) => draw(ctx, o, 1);
    globalThis.tinyhouse_wave_card_3 = (ctx, o) => draw(ctx, o, 2);
    /* test seam (tests/cards_test.mjs) */
    globalThis.TINYHOUSE_CARDS_FOR_TEST = { draw, parseDigest, reset: () => { active = [false, false, false]; lastMs = -1e12; } };
})();
