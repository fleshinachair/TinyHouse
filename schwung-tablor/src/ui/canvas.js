/*
 * TinyHouse's OLED widgets for the Schwung param pages (shared/param_pages/widget_registry.mjs).
 *
 * custom:tinyhouse_mac1 .. custom:tinyhouse_mac8: one cell each on the Macros page. A macro the preset uses is drawn as the stock arc dial
 * (so the page looks like every other); a macro the preset leaves unrouted is an empty slot (a short dash where the dial would be), so you can see
 * which of the eight do anything. The cell, its label and the knob never move, and the knob still turns (nothing is
 * routed to it, so it changes no sound). The macro's NAME is not drawn here (a 32 px cell cannot hold it): the DSP serves it as the knob's
 * `unit`, so touching the knob shows "64 More Delay" in the top bar while the cell label stays "MCR 1".
 *
 * custom:tinyhouse_waves: the Tone page's three wavetable positions as ONE picture across their three cells: each oscillator's wave at its position,
 *   from the shape digests the DSP publishes (wt1_shape .. wt3_shape, extra keys); an oscillator that is off (wt_on) is drawn as a dash.
 * custom:tinyhouse_fxchain: a row of effect switches as a strip of boxes, filled when the stage is on: the Effects page's EQ / width / delay / reverb, its compressor
 *   and master switch, and the Mod FX page's chorus / flanger / phaser.
 * custom:tinyhouse_grain: a Grain page's first line: the sample's shape (smpN_shape) with the start point and the scan direction marked.
 * custom:tinyhouse_pos1 .. 3: the single-cell position dial (no page uses it now; kept for the tests and for a layout that wants it).
 *
 * The Macros cells show the preset's macro NAME with a value bar. The names are read from `macro_names`, an extra key: the host reads a widget's extra keys only while
 * its page is on screen, so the names cost nothing while you browse presets (they used to ride in chain_params as units, which changed the contract on every preset).
 *
 * The preset's macro names come from the `macro_names` key (extra_keys): "Cutoff||Wet|||||", eight names between bars, an empty one is a macro
 * that moves nothing. The host reads extra keys one at a time on its own rotation (~5 Hz), so the dimming follows a preset change within a
 * moment. Until `macro_names` has been read (undefined) every dial is drawn live; once read, an empty name (or a name list that is empty
 * altogether: a sound with no macros) greys it.
 */
(function () {

    /* the preset's names, or null when they have not been read yet */
    function names(values) {
        const v = values && values.macro_names;
        if (typeof v !== "string") return null;
        return v.split("|");
    }

    /* The stock arc dial (shared/param_pages/render_page_movy.mjs drawArcKnob): a ring open at the bottom (230 deg + 260 deg, clockwise from
     * the top) with a pointer from the centre toward 0.68 of the radius at 225 deg + 270 deg x value. Same one-pixel-per-row and per-column
     * ring, so it matches the other pages. Radius 7: the cell is 15 px high.
     *
     * COST. A page render has 1.68 ms, the host's drawing context turns every primitive into fillRect calls across the JS bridge, and a
     * knob turn redraws the page continuously: eight dials drawn pixel by pixel were ~360 calls a frame (against ~16 for the stock dials,
     * which call the host's native arc) and the page glitched the audio while a macro knob was turned. So the shapes are worked out ONCE
     * per cell size (the ring, and each pointer angle the first time it is needed), merged into the fewest rectangles (vertical runs, then
     * horizontal ones), and a draw only replays the rectangle list. An unused macro is a single dash: one call. */
    const ARC_START = 230, ARC_SWEEP = 260, PT_START = 225, PT_SWEEP = 270, PT_OUT = 0.68;
    const shapeCache = {};

    /* pixels [[x, y], ...] -> rectangles [[x, y, w, h], ...], merged: columns first, then rows of what is left */
    function merge(pixels) {
        const set = new Set(pixels.map((q) => q[0] + "," + q[1]));
        const rects = [];
        const take = (x, y) => set.delete(x + "," + y);
        const has = (x, y) => set.has(x + "," + y);
        const sorted = pixels.slice().sort((p, q) => p[0] - q[0] || p[1] - q[1]);
        for (const [x, y] of sorted) {                                   /* vertical runs of 2 or more */
            if (!has(x, y) || !has(x, y + 1)) continue;
            let h = 0;
            while (has(x, y + h)) { take(x, y + h); h++; }
            rects.push([x, y, 1, h]);
        }
        const rows = [...set].map((k) => k.split(",").map(Number)).sort((p, q) => p[1] - q[1] || p[0] - q[0]);
        for (let i = 0; i < rows.length;) {                              /* horizontal runs of the rest */
            let j = i;
            while (j + 1 < rows.length && rows[j + 1][1] === rows[i][1] && rows[j + 1][0] === rows[j][0] + 1) j++;
            rects.push([rows[i][0], rows[i][1], j - i + 1, 1]);
            i = j + 1;
        }
        return rects;
    }

    function ringShape(W, H) {
        const key = "r" + W + "x" + H;
        if (shapeCache[key]) return shapeCache[key];
        const r = Math.max(3, Math.min(7, Math.floor((Math.min(W, H) - 1) / 2)));
        const cx = Math.floor(W / 2), cy = Math.floor(H / 2);
        const inSweep = (dx, dy) => {
            let a = Math.atan2(dx, -dy) * 180 / Math.PI;
            if (a < 0) a += 360;
            let d = a - ARC_START;
            if (d < 0) d += 360;
            return d <= ARC_SWEEP;
        };
        const seen = new Set(), px = [];
        const plot = (dx, dy) => {
            const x = cx + dx, y = cy + dy, k = x + "," + y;
            if (inSweep(dx, dy) && x >= 0 && y >= 0 && x < W && y < H && !seen.has(k)) { seen.add(k); px.push([x, y]); }
        };
        for (let dy = -r; dy <= r; dy++) {
            const dx = Math.round(Math.sqrt(r * r - dy * dy));
            plot(dx, dy); if (dx !== 0) plot(-dx, dy);
        }
        for (let dx = -r; dx <= r; dx++) {
            const dy = Math.round(Math.sqrt(r * r - dx * dx));
            plot(dx, dy); if (dy !== 0) plot(dx, -dy);
        }
        return (shapeCache[key] = { r, cx, cy, ring: merge(px) });
    }

    function pointerRects(sh, W, H, step) {                              /* step 0..32: the value in 1/32ths */
        const key = "p" + W + "x" + H + ":" + step;
        if (shapeCache[key]) return shapeCache[key];
        const rad = (PT_START + (step / 32) * PT_SWEEP) * Math.PI / 180;
        const x1 = Math.round(sh.cx + sh.r * PT_OUT * Math.sin(rad)), y1 = Math.round(sh.cy - sh.r * PT_OUT * Math.cos(rad));
        let x0 = sh.cx, y0 = sh.cy;                                      /* Bresenham from the centre */
        const dx = Math.abs(x1 - x0), dy = -Math.abs(y1 - y0), sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1;
        let err = dx + dy;
        const px = [];
        for (;;) {
            if (x0 >= 0 && y0 >= 0 && x0 < W && y0 < H) px.push([x0, y0]);
            if (x0 === x1 && y0 === y1) break;
            const e2 = 2 * err;
            if (e2 >= dy) { err += dy; x0 += sx; }
            if (e2 <= dx) { err += dx; y0 += sy; }
        }
        return (shapeCache[key] = merge(px));
    }

    function drawDial(ctx, norm, dim) {
        const W = ctx.width, H = ctx.height;
        const sh = ringShape(W, H);
        if (dim) {                                                       /* an unused macro: an empty slot, one dash through the middle of the ring's place */
            ctx.fillRect(sh.cx - 2, sh.cy, 5, 1, 1);
            return;
        }
        for (const q of sh.ring) ctx.fillRect(q[0], q[1], q[2], q[3], 1);
        for (const q of pointerRects(sh, W, H, Math.round(norm * 32))) ctx.fillRect(q[0], q[1], q[2], q[3], 1);
    }

    function drawMacro(ctx, payload, i) {
        const values = (payload && payload.values) || {};
        const nm = names(values);
        const label = nm !== null && nm[i] ? nm[i].trim() : "";
        const dim = nm !== null && !label;
        const n = Number(values["mac" + (i + 1)]);
        const norm = Number.isFinite(n) ? Math.max(0, Math.min(127, n)) / 127 : 0;
        if (nm === null || dim) { drawDial(ctx, norm, dim); return; }          /* names not read yet: the dial; a macro that moves nothing: the empty slot */
        const W = ctx.width, H = ctx.height;
        let t = label.toUpperCase();                                            /* the name, cut to the cell */
        while (t.length > 1 && ctx.textWidth(t) > W - 1) t = t.slice(0, -1);
        ctx.print(Math.max(0, (W - ctx.textWidth(t)) >> 1), 0, t, 1);
        const by = H - 5, bw = W - 2;                                           /* and a bar for the value */
        ctx.fillRect(1, by, bw, 1, 1); ctx.fillRect(1, by + 4, bw, 1, 1); ctx.fillRect(1, by, 1, 5, 1); ctx.fillRect(bw, by, 1, 5, 1);
        const fill = Math.round(norm * (bw - 4));
        if (fill > 0) ctx.fillRect(3, by + 1, fill, 3, 1);
    }

    /* The Tone page's wavetable positions (custom:tinyhouse_pos1 .. 3): the same dial, an empty slot when the oscillator is off. An oscillator
     * is off when its level (wtN_level, read as an extra key) is 0; until that has been read (undefined) the dial is drawn live. */
    function drawPos(ctx, payload, i) {
        const values = (payload && payload.values) || {};
        const lv = values["wt" + (i + 1) + "_level"];
        const dim = lv !== undefined && lv !== null && lv !== "" && !(Number(lv) > 0);
        const n = Number(values["wt" + (i + 1) + "_pos"]);
        drawDial(ctx, Number.isFinite(n) ? Math.max(0, Math.min(127, n)) / 127 : 0, dim);
    }

    /* ------------------------------------------------------------------ pictures across a row (the DSP's digests, drawn as rectangles)
     * A page render has 1.68 ms and every primitive is a call across the JS bridge, so a picture is worked out once per input (the digest, the
     * frame, the size) into a short list of rectangles and a draw only replays the list. */
    const Q = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-_";
    const digestCache = {};
    /* "frames,samples,data" (a wavetable) or "cols,data" (a sample) -> { frames, samples, data } / { n, data } (data: Uint8Array of 0..63), or null */
    function parseDigest(str, wave) {
        if (typeof str !== "string" || !str) return null;
        const ck = (wave ? "w" : "s") + str.length + ":" + str.slice(0, 24) + str.slice(-24);
        const hit = digestCache[ck];
        if (hit && hit.str === str) return hit.d;
        const parts = str.split(",");
        let d = null;
        if (wave && parts.length >= 3) {
            const frames = parseInt(parts[0], 10), samples = parseInt(parts[1], 10), txt = parts.slice(2).join(",");
            if (frames > 0 && samples > 0 && txt.length >= frames * samples) {
                const data = new Uint8Array(frames * samples);
                for (let i = 0; i < data.length; i++) data[i] = Math.max(0, Q.indexOf(txt[i]));
                d = { frames, samples, data };
            }
        } else if (!wave && parts.length >= 2) {
            const txt = parts.slice(1).join(",");
            const data = new Uint8Array(txt.length);
            for (let i = 0; i < txt.length; i++) data[i] = Math.max(0, Q.indexOf(txt[i]));
            if (data.length) d = { n: data.length, data };
        }
        const keys = Object.keys(digestCache);
        if (keys.length > 24) delete digestCache[keys[0]];
        digestCache[ck] = { str, d };
        return d;
    }

    /* columns [[x, y0, y1], ...] (a vertical span each) -> rectangles, neighbouring columns with the same span merged into one */
    function colsToRects(cols) {
        const out = [];
        for (const c of cols) {
            const x = c[0], y0 = c[1], y1 = c[2];
            const last = out[out.length - 1];
            if (last && last[3] === y1 - y0 + 1 && last[1] === y0 && last[0] + last[2] === x) last[2]++;
            else out.push([x, y0, 1, y1 - y0 + 1]);
        }
        return out;
    }

    const waveCache = {};
    /* One wavetable frame as a line across w x h: each column spans from the previous column's level to its own, so a steep edge is solid. */
    function waveRects(d, pos, w, h, tag) {
        const f = Math.max(0, Math.min(d.frames - 1, Math.round((pos / 127) * (d.frames - 1))));
        const key = tag + ":" + f + ":" + w + "x" + h;
        const hit = waveCache[key];
        if (hit && hit.d === d) return hit.r;
        const base = f * d.samples, cols = [];
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
        waveCache[key] = { d, r };
        return r;
    }

    const isOn = (v) => (typeof v === "string" ? /^(on|1|true)$/i.test(v.trim()) : Number(v) > 0);
    const num = (v, dflt) => { const n = Number(v); return v !== undefined && v !== null && v !== "" && Number.isFinite(n) ? n : dflt; };

    /* The Tone page: three waves side by side across the three cells. */
    function drawWaves(ctx, payload) {
        const values = (payload && payload.values) || {};
        const W = ctx.width, H = ctx.height;
        const on = typeof values.wt_on === "string" ? values.wt_on.split(",") : null;
        const gap = 2, pw = Math.max(8, Math.floor((W - gap * 2) / 3));
        for (let i = 0; i < 3; i++) {
            const x0 = i * (pw + gap);
            const off = on !== null && on[i] === "0";
            const d = parseDigest(values["wt" + (i + 1) + "_shape"], true);
            if (off || !d) { ctx.fillRect(x0 + (pw >> 1) - 2, H >> 1, 5, 1, 1); continue; }     /* off, or not read yet: a dash where the wave would be */
            for (const q of waveRects(d, num(values["wt" + (i + 1) + "_pos"], 0), pw, H, "o" + i)) ctx.fillRect(x0 + q[0], q[1], q[2], q[3], 1);
        }
    }

    /* The switches of the effect pages as boxes in a row (the group's keys, in order), a stage that is on is a filled box. The EQ / width / delay / reverb chain and
     * the three modulation effects have a line between the boxes (one stage feeds the next); the compressor and the master switch (group "fxmaster") do not. */
    const SWITCH_LABELS = { eq_on: "EQ", wide_on: "WID", dly_on: "DLY", rev_on: "REV", comp_on: "CMP", fx_on: "FX", chor_on: "CHO", flan_on: "FLA", phas_on: "PHA" };
    const CHAIN_DEFAULT = ["eq_on", "wide_on", "dly_on", "rev_on"];
    function drawFxChain(ctx, payload) {
        const values = (payload && payload.values) || {};
        const grp = payload && payload.group;
        const keys = grp && Array.isArray(grp.keys) && grp.keys.length ? grp.keys : CHAIN_DEFAULT;
        const lines = !(grp && grp.group === "fxmaster");
        const W = ctx.width, H = ctx.height, n = keys.length, gap = 3;
        const bw = Math.floor((W - gap * (n - 1)) / n), bh = Math.min(H, 13), by = (H - bh) >> 1;
        for (let i = 0; i < n; i++) {
            const x = i * (bw + gap), v = values[keys[i]], on = v === undefined || v === null ? true : isOn(v);
            if (i && lines) ctx.fillRect(x - gap, by + (bh >> 1), gap, 1, 1);             /* the line from the stage before */
            if (on) ctx.fillRect(x, by, bw, bh, 1);
            else { ctx.fillRect(x, by, bw, 1, 1); ctx.fillRect(x, by + bh - 1, bw, 1, 1); ctx.fillRect(x, by, 1, bh, 1); ctx.fillRect(x + bw - 1, by, 1, bh, 1); }
            const label = SWITCH_LABELS[keys[i]] || String(keys[i]).slice(0, 3).toUpperCase(), tw = ctx.textWidth(label);
            ctx.print(x + Math.max(1, (bw - tw) >> 1), by + Math.max(0, (bh - 7) >> 1), label, on ? 0 : 1);
        }
    }

    const grainCache = {};
    /* A sample's loudness as bars mirrored about the middle line, across w x h. */
    function sampleRects(d, w, h) {
        const key = w + "x" + h;
        const hit = grainCache[key];
        if (hit && hit.d === d) return hit.r;
        const mid = h >> 1, half = Math.max(1, mid), cols = [];
        for (let x = 0; x < w; x++) {
            const a = Math.floor((x * d.n) / w), b = Math.max(a + 1, Math.floor(((x + 1) * d.n) / w));
            let m = 0;
            for (let i = a; i < b && i < d.n; i++) if (d.data[i] > m) m = d.data[i];
            const r = Math.round((m / 63) * half);
            cols.push([x, mid - r, mid + r]);
        }
        const r = colsToRects(cols);
        grainCache[key] = { d, r };
        return r;
    }

    /* The scan head: the point of the sample the grains are read from, moving as the remote UI's picture does (the same law: the module serves a slot's End, Loop, Loop start/end, Manual
     * position and Position as `<slot>_gv`, the page's own Start and Scan rate come with the cell). The file is drawn as 4 s wide, so a Scan rate of 100 % crosses it in 4 s; the head
     * walks the scan stretch (Start..End), or the loop's when one is on, and wraps; in Manual position it stands at Position. It is an illustration of the settings, not the audio (the clock is
     * the screen's, not the voice's). A dotted line along the bottom marks the stretch it walks. */
    const GRAIN_FILE_S = 4;
    function parseGv(v) {
        if (typeof v !== "string") return null;
        const a = v.split(",").map(Number);
        return a.length >= 6 && a.every(Number.isFinite) ? { end: a[0], loop: a[1], ls: a[2], le: a[3], man: a[4], pos: a[5] } : null;
    }
    function headPos(values, slot, now) {
        const g = parseGv(values[slot + "_gv"]);
        if (!g) return null;
        const st = num(values[slot + "_gstart"], 0) / 100, scan = num(values[slot + "_gscan"], 100);
        let en = g.end / 100;
        if (en <= st) en = Math.min(1, st + 0.01);
        let ls = g.ls / 100, le = g.le / 100;
        if (le <= ls) le = Math.min(1, ls + 0.01);
        const lo = g.loop >= 1 ? ls : st, hi = g.loop >= 1 ? le : en;
        if (g.man >= 1) return { x: Math.max(0, Math.min(1, g.pos / 100)), lo: lo, hi: hi };
        const rate = scan / 100 / GRAIN_FILE_S, span = hi - lo;
        const f = ((now * rate) % span + span) % span;
        return { x: rate >= 0 ? lo + f : hi - (span - f) % span, lo: lo, hi: hi };
    }
    function drawHead(ctx, values, slot, startX) {
        const h = headPos(values, slot, Date.now() / 1000);
        if (!h) return;
        const W = ctx.width, H = ctx.height, hx = Math.max(0, Math.min(W - 1, Math.round(h.x * (W - 1))));
        for (let x = Math.round(h.lo * (W - 1)); x <= Math.round(h.hi * (W - 1)); x += 2) ctx.fillRect(x, H - 1, 1, 1, 1);        /* the stretch it walks, dotted */
        ctx.fillRect(hx, 0, 1, H, 1);                                                                                           /* the head: a line with a block at its middle */
        ctx.fillRect(Math.max(0, hx - 1), (H >> 1) - 1, 3, 3, 1);
    }

    /* A Grain page: the sample's shape across the row. A granular slot also gets the start point (a vertical line with a tick above and below) and an arrow in
     * the corner the scan runs toward (the right for a positive scan rate, the left for a negative one, none for 0). */
    function drawGrain(ctx, payload) {
        const values = (payload && payload.values) || {};
        const W = ctx.width, H = ctx.height;
        const keys = (payload && payload.group && payload.group.keys) || [];
        const at = keys.length ? String(keys[0]).indexOf("_g") : -1;
        const slot = at > 0 ? String(keys[0]).slice(0, at) : "smp";
        const kind = values[slot + "_kind"], gran = kind !== undefined && kind !== null && (typeof kind === "string" ? /gran/i.test(kind) : Number(kind) >= 1);
        const level = values[slot + "_level"];
        const off = level !== undefined && level !== null && level !== "" && !(Number(level) > 0);
        const d = parseDigest(values[slot + "_shape"], false);
        if (off || !d) { ctx.fillRect((W >> 1) - 3, H >> 1, 7, 1, 1); return; }          /* no sample, or the slot is off: a dash */
        for (const q of sampleRects(d, W, H)) ctx.fillRect(q[0], q[1], q[2], q[3], 1);
        ctx.fillRect(0, H >> 1, W, 1, 1);                                             /* the middle line, so a quiet stretch still shows its length */
        if (!gran) return;
        const sx = Math.max(0, Math.min(W - 1, Math.round((num(values[slot + "_gstart"], 0) / 100) * (W - 1))));
        ctx.fillRect(Math.max(0, sx - 1), 0, 3, H, 0);                                /* the marker stands out of the bars: a gap, then the line */
        ctx.fillRect(sx, 0, 1, H, 1);
        ctx.fillRect(Math.max(0, sx - 1), 0, 3, 1, 1); ctx.fillRect(Math.max(0, sx - 1), H - 1, 3, 1, 1);
        drawHead(ctx, values, slot, sx);
        const sc = num(values[slot + "_gscan"], 100);
        if (sc !== 0) {                                                                 /* the scan arrow, in the corner it points to */
            const right = sc > 0, ax = right ? W - 8 : 1;
            ctx.fillRect(ax - 1, 0, 9, 5, 0);
            ctx.fillRect(ax, 2, 7, 1, 1);
            for (let k = 0; k < 3; k++) { const hx = right ? ax + 6 - k : ax + k; ctx.fillRect(hx, 2 - k, 1, 1 + 2 * k, 1); }
        }
    }

    const widgetKinds = {};
    for (let i = 0; i < 8; i++)
        widgetKinds["custom:tinyhouse_mac" + (i + 1)] = { draw: (ctx, payload) => drawMacro(ctx, payload, i), nominal: { w: 32, h: 15 } };
    for (let i = 0; i < 3; i++)
        widgetKinds["custom:tinyhouse_pos" + (i + 1)] = { draw: (ctx, payload) => drawPos(ctx, payload, i), nominal: { w: 32, h: 15 } };
    widgetKinds["custom:tinyhouse_waves"] = { draw: drawWaves, nominal: { w: 96, h: 15 } };
    widgetKinds["custom:tinyhouse_fxchain"] = { draw: drawFxChain, nominal: { w: 128, h: 15 } };
    widgetKinds["custom:tinyhouse_grain"] = { draw: drawGrain, nominal: { w: 128, h: 15 } };
    globalThis.canvas_overlay = { widgetKinds };
    /* test seam (tests/canvas_test.mjs) */
    globalThis.TINYHOUSE_CANVAS_FOR_TEST = { drawMacro, drawPos, drawDial, names, drawWaves, drawFxChain, drawGrain, parseDigest, headPos, parseGv };
})();
