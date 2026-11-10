/*
 * TinyHouse's OLED widgets for the Schwung param pages (shared/param_pages/widget_registry.mjs).
 *
 * custom:tinyhouse_mac1 .. custom:tinyhouse_mac8: one cell each on the Macros page. A macro the preset uses is drawn as the stock arc dial
 * (so the page looks like every other); a macro the preset leaves unrouted is an empty slot (a short dash where the dial would be), so you can see
 * which of the eight do anything. The cell, its label and the knob never move, and the knob still turns (nothing is
 * routed to it, so it changes no sound). The macro's NAME is not drawn here (a 32 px cell cannot hold it): the DSP serves it as the knob's
 * `unit`, so touching the knob shows "64 More Delay" in the top bar while the cell label stays "MCR 1".
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
        const dim = nm !== null && !(nm[i] && nm[i].trim());
        const n = Number(values["mac" + (i + 1)]);
        drawDial(ctx, Number.isFinite(n) ? Math.max(0, Math.min(127, n)) / 127 : 0, dim);
    }

    const widgetKinds = {};
    for (let i = 0; i < 8; i++)
        widgetKinds["custom:tinyhouse_mac" + (i + 1)] = { draw: (ctx, payload) => drawMacro(ctx, payload, i), nominal: { w: 32, h: 15 } };
    globalThis.canvas_overlay = { widgetKinds };
    /* test seam (tests/canvas_test.mjs) */
    globalThis.TINYHOUSE_CANVAS_FOR_TEST = { drawMacro, drawDial, names };
})();
