/*
 * TinyHouse's parameter cards (Schwung `card_script`): a near full-screen picture of the wavetables, raised while one of the three
 * Wavetable Position knobs (wt1_pos .. wt3_pos) is held or turned, gone when you let go (the host takes it down; this file owns no timer).
 *
 * One knob held: its oscillator's whole table fills the card as a 3D waterfall like the remote panel's (frame 0 in front, the later frames
 * stacked behind it, nearer frames hiding the ones behind), with the frame at the knob's position drawn thick, and the frame number in the header.
 * Two or three knobs moved in the same gesture: the card becomes that many rows, one under the other, each with the frame at its knob's
 * position drawn flat and full width (a waterfall that small is unreadable), and the oscillator you are moving now has its label inverted. The host hands a card only the knob it raised the card for (the last one touched),
 * so "moved in the same gesture" is worked out here: an oscillator joins when its card is drawn, or when its position changes in the page's
 * value map while another knob's card is up, and the set is cleared when the card has been down for a moment (the draws are a frame apart
 * while the card is up). A knob whose position is driven by modulation moves in that map too, so it can join without being touched.
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
    let active = [false, false, false], lastPos = [null, null, null], lastMs = -1e12, lastWho = 0;

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

    /*
     * THE WATERFALL: a lone panel draws the whole table as the remote panel does (frame 0 in front at the lower right, the later frames
     * stacked behind it up and to the left, each hiding what is behind it) with the frame at the knob's position drawn thick.
     * It is worked out into a bitmap here once per (digest, frame, size), then covered with as few rectangles as it can be, because
     * every rectangle is one bridge call: the plan steps down to fewer rows and wider steps until it fits RECT_BUDGET.
     */
    const FAINT = [3, 1];                                                         /* the frames that are not the chosen one are drawn faint, dotted: FAINT[1] columns on in every FAINT[0] (the display is one bit, so fainter means sparser) */
    const RECT_BUDGET = 130, TIERS = [[8, 1], [7, 2], [6, 2], [5, 3], [4, 4], [3, 6]];

    function rowPlan(F, frame, R) {
        const idx = [];
        for (let k = 0; k < R; k++) idx.push(F === 1 ? 0 : Math.round((k * (F - 1)) / (R - 1)));
        let near = 0, best = 1e9;
        for (let k = 0; k < R; k++) { const dd = Math.abs(idx[k] - frame); if (dd < best) { best = dd; near = k; } }
        idx[near] = frame;
        return idx.filter((v, k) => idx.indexOf(v) === k);
    }

    function waterfallBitmap(d, frame, w, h, R, step, faint) {
        const F = d.frames, S = d.samples, rows = rowPlan(F, frame, R);
        const dx = Math.round(w * 0.2), dy = Math.round(h * 0.34), fw = w - dx - 1, fh = h - dy - 1, amp = fh * 0.46;
        const bm = new Uint8Array(w * h), hor = new Int16Array(w).fill(h + 8), ys = new Int16Array(fw);
        for (let k = 0; k < rows.length; k++) {                                   /* front (frame 0) first, so what is nearer is already on the page */
            const i = rows[k], t = F === 1 ? 1 : i / (F - 1), x0 = Math.round(dx * (1 - t)), yMid = Math.round(dy * (1 - t) + fh * 0.5), cur = i === frame;
            for (let x = 0; x < fw; x++) {
                const gx = step > 1 ? Math.floor(x / step) * step + (step >> 1) : x;
                const sx = (Math.min(gx, fw - 1) * (S - 1)) / (fw - 1), a = Math.floor(sx), b = Math.min(S - 1, a + 1), f = sx - a;
                ys[x] = Math.round(yMid - ((d.data[i * S + a] * (1 - f) + d.data[i * S + b] * f) / 63 * 2 - 1) * amp);
            }
            const top = new Int16Array(fw);
            const span = [];
            for (let x = 0; x < fw; x++) {
                const X = x0 + x, y0 = x > 0 && (step === 1 || x % step === 0) ? Math.min(ys[x], ys[x - 1]) : ys[x], y1 = x > 0 && (step === 1 || x % step === 0) ? Math.max(ys[x], ys[x - 1]) : ys[x];
                if (cur) { span.push([X, y0, y1]); continue; }                      /* the chosen frame is drawn after the others, whole, over them */
                if (!faint || X % faint[0] < faint[1]) for (let y = y0; y <= y1; y++) if (y >= 0 && y < h && y < hor[X]) bm[y * w + X] = 1;
                top[x] = y0;
            }
            if (cur) {                                                              /* a frame behind others stays whole: a 1 px moat cleared round it, then the line drawn 2 px thick on top */
                for (const [X, y0, y1] of span) for (let y = Math.max(0, y0 - 2); y <= Math.min(h - 1, y1 + 3); y++) bm[y * w + X] = 0;
                for (const [X, y0, y1] of span) for (let y = y0; y <= y1 + 1; y++) if (y >= 0 && y < h) bm[y * w + X] = 1;
                for (let x = 0; x < fw; x++) top[x] = span[x][1];
            }
            for (let x = 0; x < fw; x++) if (top[x] < hor[x0 + x]) hor[x0 + x] = top[x];
        }
        return bm;
    }

    /* horizontal runs per pixel row, a run that repeats on the next row is stretched down instead of drawn again */
    function bitmapRects(bm, w, h) {
        const out = []; let open = new Map();
        for (let y = 0; y < h; y++) {
            const next = new Map(); let x = 0;
            while (x < w) {
                if (!bm[y * w + x]) { x++; continue; }
                let e = x; while (e < w && bm[y * w + e]) e++;
                const key = x * 256 + (e - x), o = open.get(key);
                if (o) { o[3]++; next.set(key, o); } else { const r = [x, y, e - x, 1]; out.push(r); next.set(key, r); }
                x = e;
            }
            open = next;
        }
        return out;
    }

    function waterfallRects(d, frame, w, h, tag, budget) {
        const key = "w" + tag + ":" + frame + ":" + w + "x" + h, hit = waveCache[key];
        if (hit && hit.d === d) return hit.r;
        d.tiers = d.tiers || {};
        let r = null, tier = d.tiers[w] || 0;                                    /* a table that needed a coarser plan once starts there: one pass per frame, not six */
        for (; tier < TIERS.length; tier++) {
            r = bitmapRects(waterfallBitmap(d, frame, w, h, TIERS[tier][0], TIERS[tier][1], FAINT), w, h);
            if (r.length <= budget) break;
        }
        d.tiers[w] = Math.min(tier, TIERS.length - 1);
        const keys = Object.keys(waveCache);
        if (keys.length > 60) delete waveCache[keys[0]];
        waveCache[key] = { d, r };
        return r;
    }

    /* one wavetable frame as a line across w x h (a column spans from the previous column's level to its own, so a steep edge is solid), as merged rectangles */
    function flatRects(d, frame, w, h, tag) {
        const key = "f" + tag + ":" + frame + ":" + w + "x" + h, hit = waveCache[key];
        if (hit && hit.d === d) return hit.r;
        const base = frame * d.samples, out = [];
        let prev = null;
        for (let x = 0; x < w; x++) {
            const a = Math.floor((x * d.samples) / w), b = Math.max(a + 1, Math.floor(((x + 1) * d.samples) / w));
            let lo = 63, hi = 0;
            for (let i = a; i < b; i++) { const v = d.data[base + i]; if (v < lo) lo = v; if (v > hi) hi = v; }
            let y0 = Math.round((1 - hi / 63) * (h - 1)), y1 = Math.round((1 - lo / 63) * (h - 1));
            if (prev !== null) { if (prev < y0) y0 = prev; else if (prev > y1) y1 = prev; }
            prev = Math.round((1 - ((lo + hi) / 2) / 63) * (h - 1));
            const last = out[out.length - 1];
            if (last && last[3] === y1 - y0 + 1 && last[1] === y0 && last[0] + last[2] === x) last[2]++;
            else out.push([x, y0, 1, y1 - y0 + 1]);
        }
        const keys = Object.keys(waveCache);
        if (keys.length > 60) delete waveCache[keys[0]];
        waveCache[key] = { d, r: out };
        return out;
    }

    function draw(ctx, o, which) {
        o = o || {};
        const W = ctx.width, H = ctx.height, values = o && o.values;
        const now = num(o && o.nowMs, Date.now());
        const fresh = now - lastMs > GAP_MS || now < lastMs;
        if (fresh) { active = [false, false, false]; lastPos = [null, null, null]; }
        /* The host raises the card for ONE knob (the last touched), so a second knob is noticed by its position moving in the page's value map. */
        for (let i = 0; i < 3; i++) {
            const v = i === which && o.raw !== null && o.raw !== undefined && Number.isFinite(Number(o.raw)) ? Number(o.raw) : num(val(values, "wt" + (i + 1) + "_pos"), null);
            if (!fresh && i !== which && v !== null && lastPos[i] !== null && Math.abs(v - lastPos[i]) >= 0.5) active[i] = true;
            if (v !== null) lastPos[i] = v;
        }
        active[which] = true; lastMs = now; lastWho = which;
        const idx = [0, 1, 2].filter((i) => active[i]);
        const n = idx.length;
        const on = val(values, "wt_on");
        const onList = typeof on === "string" ? on.split(",") : null;
        const state = (i) => {
            const cur = i === which;
            const pos = cur && o.raw !== null && o.raw !== undefined && Number.isFinite(Number(o.raw)) ? Number(o.raw) : num(val(values, "wt" + (i + 1) + "_pos"), null);   /* the knob in your hand is read live */
            const d = parseDigest(val(values, "wt" + (i + 1) + "_shape"));
            const frame = d && pos !== null ? Math.max(0, Math.min(d.frames - 1, Math.round((Math.max(0, Math.min(127, pos)) / 127) * (d.frames - 1)))) : 0;
            return { cur, pos, d, frame, off: onList !== null && onList[i] === "0" };
        };
        if (n === 1) {                                                                  /* one knob: the whole table as a waterfall, the frame number and the knob's reading on top */
            const i = idx[0], st = state(i);
            const tail = st.d && st.pos !== null ? " " + (st.frame + 1) : "";
            ctx.print(0, 0, "WT" + (i + 1) + tail, 1);
            const right = (typeof o.value === "string" ? o.value : "") || "";
            if (right && st.d) ctx.print(W - ctx.textWidth(right), 0, right, 1);
            if (st.off || !st.d || st.pos === null) { ctx.fillRect((W >> 1) - 3, 9 + ((H - 9) >> 1), 7, 1, 1); return; }   /* off, not read yet, or no position: a dash */
            for (const q of waterfallRects(st.d, st.frame, W, H - 9, "c" + i, RECT_BUDGET)) ctx.fillRect(q[0], 9 + q[1], q[2], q[3], 1);
            return;
        }
        /* two or three knobs: one row each, stacked, the frame in your hand drawn flat and full width (a waterfall that small is unreadable), the knob you are moving now with its label inverted */
        const gap = 2, rh = Math.floor((H - gap * (n - 1)) / n), lw = Math.min(ctx.textWidth("1:00") + 2, 28);
        for (let k = 0; k < n; k++) {
            const i = idx[k], st = state(i), y0 = k * (rh + gap);
            const text = (i + 1) + (st.d && st.pos !== null ? ":" + (st.frame + 1) : "");
            if (st.cur) ctx.fillRect(0, y0, lw - 1, 8, 1);
            ctx.print(st.cur ? 1 : 0, y0, text, st.cur ? 0 : 1);
            if (rh >= 20) ctx.fillRect(0, y0 + 9, lw - 3, 1, 1);
            const x0 = lw + 1, ww = W - x0, hh = rh;
            ctx.fillRect(x0 - 1, y0, 1, hh, 1);                                         /* a rule between the label and the wave */
            if (st.off || !st.d || st.pos === null) { ctx.fillRect(x0 + (ww >> 1) - 3, y0 + (hh >> 1), 7, 1, 1); continue; }
            for (const q of flatRects(st.d, st.frame, ww, hh, "r" + i)) ctx.fillRect(x0 + q[0], y0 + q[1], q[2], q[3], 1);
        }
    }

    globalThis.tinyhouse_wave_card_1 = (ctx, o) => draw(ctx, o, 0);
    globalThis.tinyhouse_wave_card_2 = (ctx, o) => draw(ctx, o, 1);
    globalThis.tinyhouse_wave_card_3 = (ctx, o) => draw(ctx, o, 2);
    /* test seam (tests/cards_test.mjs) */
    globalThis.TINYHOUSE_CARDS_FOR_TEST = { draw, waterfallBitmap, bitmapRects, TIERS, FAINT, parseDigest, reset: () => { active = [false, false, false]; lastPos = [null, null, null]; lastMs = -1e12; } };
})();
