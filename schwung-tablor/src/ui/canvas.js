/*
 * TinyHouse's OLED widgets for the Schwung param pages (shared/param_pages/widget_registry.mjs).
 *
 * custom:tinyhouse_mac1 .. custom:tinyhouse_mac8: one cell each on the Macros page. A knob whose macro the preset
 * uses shows the macro's name (scrolled when it is wider than the cell). A macro the preset leaves unrouted is drawn as a dimmed "N/A" (every
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

    /* A macro in use shows its NAME (the stock label is only "MC1"): centred if it fits the cell, otherwise scrolled
     * along it (rests at the start, runs to the end, rests, repeats) so the whole name can be read. The value is not
     * drawn: the host shows it when the knob is turned. Without a clock (no nowMs) a long name is clipped instead. */
    const REST_MS = 900, SPEED_PX_S = 22;
    function scrollX(over, nowMs) {                                /* x offset (<= 0) of a name `over` px wider than the cell */
        const run = (over / SPEED_PX_S) * 1000, cycle = REST_MS * 2 + run;
        const t = nowMs % cycle;
        if (t < REST_MS) return 0;
        if (t > REST_MS + run) return -over;
        return -Math.round(((t - REST_MS) / 1000) * SPEED_PX_S);
    }

    function drawMacro(ctx, payload, i) {
        const values = (payload && payload.values) || {};
        const nowMs = payload && typeof payload.nowMs === "number" ? payload.nowMs : null;
        const W = ctx.width, H = ctx.height;
        const tw = (s) => (typeof ctx.textWidth === "function" ? ctx.textWidth(s) : s.length * 6);
        const ty = Math.max(0, Math.min(H - TEXT_H, Math.floor((H - TEXT_H - 3) / 2)));
        const nm = names(values);
        const name = nm !== null && nm[i] ? nm[i].trim() : "";
        const dim = nm !== null && !name;
        if (dim) {
            const txt = "N/A", x = Math.max(0, Math.floor((W - tw(txt)) / 2));
            ctx.print(x, ty, txt, 1);
            if (typeof ctx.setPixel === "function")
                for (let y = ty; y < Math.min(H, ty + TEXT_H); y++)
                    for (let xx = x; xx < Math.min(W, x + tw(txt)); xx++)
                        if ((xx + y) & 1) ctx.setPixel(xx, y, 0);
            return;
        }
        let txt = name || valueText(values["mac" + (i + 1)]);          /* names unread: the value */
        const w = tw(txt);
        if (w <= W - 2) { ctx.print(Math.max(0, Math.floor((W - w) / 2)), ty, txt, 1); return; }
        if (nowMs === null) {                                          /* no clock: clip the name */
            while (tw(txt) > W - 2 && txt.length > 1) txt = txt.slice(0, -1);
            ctx.print(Math.max(0, Math.floor((W - tw(txt)) / 2)), ty, txt, 1);
            return;
        }
        ctx.print(1 + scrollX(w - (W - 2), nowMs), ty, txt, 1);        /* the frame clips what runs past the edge */
    }

    const widgetKinds = {};
    for (let i = 0; i < 8; i++)
        widgetKinds["custom:tinyhouse_mac" + (i + 1)] = { draw: (ctx, payload) => drawMacro(ctx, payload, i), nominal: { w: 32, h: 15 } };
    globalThis.canvas_overlay = { widgetKinds };
    /* test seam (tests/canvas_test.mjs) */
    globalThis.TINYHOUSE_CANVAS_FOR_TEST = { drawMacro, names, valueText, scrollX };
})();
