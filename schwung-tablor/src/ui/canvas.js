/*
 * TinyHouse's OLED widgets for the Schwung param pages (shared/param_pages/widget_registry.mjs).
 *
 * custom:tinyhouse_mac1 .. custom:tinyhouse_mac8: one cell each on the Macros page. A macro the preset uses is drawn as the stock arc dial
 * (so the page looks like every other); a macro the preset leaves unrouted is the same ring in the screen's grey (every other pixel) with no
 * pointer, so you can see which of the eight do anything. The cell, its label and the knob never move, and the knob still turns (nothing is
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
     * the top) with a pointer from the centre toward 0.68 of the radius at 225 deg + 270 deg x value. Drawn with the same one-pixel-per-row
     * and per-column ring so it matches the other pages. Radius 7 here: the cell is 15 px high. */
    const ARC_START = 230, ARC_SWEEP = 260, PT_START = 225, PT_SWEEP = 270, PT_OUT = 0.68;

    function drawDial(ctx, norm, dim) {
        const W = ctx.width, H = ctx.height;
        const r = Math.max(3, Math.min(7, Math.floor((Math.min(W, H) - 1) / 2)));
        const cx = Math.floor(W / 2), cy = Math.floor(H / 2);
        const px = (x, y) => {
            if (x < 0 || y < 0 || x >= W || y >= H) return;
            if (dim && ((x + y) & 1)) return;                              /* unused: the ring in the screen's grey */
            ctx.fillRect(x, y, 1, 1, 1);
        };
        const inSweep = (dx, dy) => {
            let a = Math.atan2(dx, -dy) * 180 / Math.PI;
            if (a < 0) a += 360;
            let d = a - ARC_START;
            if (d < 0) d += 360;
            return d <= ARC_SWEEP;
        };
        const plot = (dx, dy) => { if (inSweep(dx, dy)) px(cx + dx, cy + dy); };
        for (let dy = -r; dy <= r; dy++) {
            const dx = Math.round(Math.sqrt(r * r - dy * dy));
            plot(dx, dy); if (dx !== 0) plot(-dx, dy);
        }
        for (let dx = -r; dx <= r; dx++) {
            const dy = Math.round(Math.sqrt(r * r - dx * dx));
            plot(dx, dy); if (dy !== 0) plot(dx, -dy);
        }
        if (dim) return;                                                   /* an unused macro has no pointer: there is nothing to point at */
        const rad = (PT_START + norm * PT_SWEEP) * Math.PI / 180;
        const x1 = Math.round(cx + r * PT_OUT * Math.sin(rad)), y1 = Math.round(cy - r * PT_OUT * Math.cos(rad));
        let x0 = cx, y0 = cy;                                              /* Bresenham from the centre */
        const dx = Math.abs(x1 - x0), dy = -Math.abs(y1 - y0), sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1;
        let err = dx + dy;
        for (;;) {
            px(x0, y0);
            if (x0 === x1 && y0 === y1) break;
            const e2 = 2 * err;
            if (e2 >= dy) { err += dy; x0 += sx; }
            if (e2 <= dx) { err += dx; y0 += sy; }
        }
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
