/*
 * TinyHouse's OLED widgets for the Schwung param pages (shared/param_pages/widget_registry.mjs).
 *
 * custom:tinyhouse_mac1 .. custom:tinyhouse_mac8: one cell each on the Macros page. A knob whose macro the preset
 * uses shows its value as the stock knob would. A macro the preset leaves unrouted is drawn as a dimmed "N/A" (every
 * other pixel cleared: the 1-bit screen's grey), so you can see which of the eight do anything. The cell, its name and
 * the knob never move, and the knob still turns (nothing is routed to it, so it changes no sound).
 *
 * The preset's macro names come from the `macro_names` key (extra_keys): "Cutoff||Wet|||||", eight names between
 * bars, an empty one is a macro that moves nothing. The host reads extra keys one at a time on its own rotation
 * (~5 Hz), so the dimming follows a preset change within a moment. Until `macro_names` has been read (undefined)
 * every knob shows its value; once read, an empty name (or a name list that is empty altogether: a sound with no
 * macros) dims.
 */
(function () {
    const TEXT_H = 8;                                                 /* the display font, with room */

    /* the preset's names, or null when they have not been read yet */
    function names(values) {
        const v = values && values.macro_names;
        if (typeof v !== "string") return null;
        return v.split("|");
    }

    function valueText(v) {
        const n = Number(v);
        return String(Number.isFinite(n) ? Math.round(Math.max(0, Math.min(127, n))) : 0);
    }

    function drawMacro(ctx, payload, i) {
        const values = (payload && payload.values) || {};
        const W = ctx.width, H = ctx.height;
        const tw = (s) => (typeof ctx.textWidth === "function" ? ctx.textWidth(s) : s.length * 6);
        const ty = Math.max(0, Math.min(H - TEXT_H, Math.floor((H - TEXT_H - 3) / 2)));
        const nm = names(values);
        const dim = nm !== null && !(nm[i] && nm[i].trim());
        let txt = dim ? "N/A" : valueText(values["mac" + (i + 1)]);
        while (tw(txt) > W - 2 && txt.length > 1) txt = txt.slice(0, -1);   /* a narrow cell: clip */
        const x = Math.max(0, Math.floor((W - tw(txt)) / 2));
        ctx.print(x, ty, txt, 1);
        if (dim && typeof ctx.setPixel === "function")
            for (let y = ty; y < Math.min(H, ty + TEXT_H); y++)
                for (let xx = x; xx < Math.min(W, x + tw(txt)); xx++)
                    if ((xx + y) & 1) ctx.setPixel(xx, y, 0);
    }

    const widgetKinds = {};
    for (let i = 0; i < 8; i++)
        widgetKinds["custom:tinyhouse_mac" + (i + 1)] = { draw: (ctx, payload) => drawMacro(ctx, payload, i), nominal: { w: 32, h: 15 } };
    globalThis.canvas_overlay = { widgetKinds };
    /* test seam (tests/canvas_test.mjs) */
    globalThis.TINYHOUSE_CANVAS_FOR_TEST = { drawMacro, names, valueText };
})();
