/*
 * chain_params: the static head from params.h plus the entries whose options come from what is on the Move (wavetable packs and names, banks, demo packs and
 * parts). The result must fit the Remote UI: schwung-manager reads a value into a 65536-byte buffer, and a list cut off there is not JSON, so the manager hands
 * the page an EMPTY list: no knobs and no preset bar (it also learns the data_feed extra keys from this list). The option names are the only part a user's
 * library can grow without bound, and every one of these enums is read and written by INDEX, so they are shortened until the whole list fits.
 *
 * No I/O and no globals: tests/test_dsp.cpp runs it with oversized libraries.
 */
#ifndef TB_CHAIN_PARAMS_H
#define TB_CHAIN_PARAMS_H

#include <cstddef>
#include <cstdio>
#include <string>
#include <vector>

namespace tb {

/* the longest list to publish: under the manager's 65536, with room for the macro units (kMacroUnits) */
constexpr size_t kChainParamsMax = 65000;

/* JSON-escape into a std::string. The option names are filenames, so a quote or a backslash is unlikely but entirely legal on disk -- and one
 * unescaped byte turns the whole contract into a failed read. */
inline void jsonAppendName(std::string &out, const std::string &in)
{
    for (char c : in) {
        if (c == '"' || c == '\\') { out += '\\'; out += c; }
        else if ((unsigned char) c < 0x20) continue;
        else out += c;
    }
}

/* `name` in at most `maxChars` characters (0: as it is). The middle goes, not the end: numbered variants of one name differ at the end. Cuts between
 * UTF-8 characters, never inside one. */
inline std::string shortLabel(const std::string &name, size_t maxChars)
{
    if (!maxChars) return name;
    std::vector<size_t> at;                                          /* where each character starts */
    for (size_t i = 0; i < name.size(); i++)
        if (((unsigned char) name[i] & 0xC0) != 0x80) at.push_back(i);
    if (at.size() <= maxChars) return name;
    const size_t tail = (maxChars - 1) * 2 / 5, head = maxChars - 1 - tail;
    std::string out = name.substr(0, at[head]);
    out += '~';
    if (tail) out += name.substr(at[at.size() - tail]);
    return out;
}

struct LabelFit {
    size_t chars;       /* longest option name, 0: whole names */
    size_t count;       /* most options in one list, 0: all of them */
};

/* Tried in this order until the list fits. Whole names first, so a library that already fits is published exactly as before. Dropping options is the last
 * resort (it takes thousands of banks or demo parts). */
constexpr LabelFit kLabelFits[] = { { 0, 0 }, { 32, 0 }, { 24, 0 }, { 18, 0 }, { 14, 0 }, { 10, 0 }, { 7, 0 }, { 4, 0 }, { 4, 64 }, { 4, 32 }, { 4, 16 }, { 4, 1 } };

struct ChainParamLists {
    std::vector<std::string> packs;       /* wt_pack, "All" first */
    std::vector<std::string> tables;      /* wtN_select: the chosen pack's wavetables, "Init" first */
    std::vector<std::string> banks;       /* preset folders */
    std::vector<std::string> demoPacks;
    std::vector<std::string> demoParts;   /* of the chosen demo pack */
};

inline std::string chainParamsAt(const char *staticHead, size_t prioLen, int wtSlots, const ChainParamLists &l, const LabelFit &fit)
{
    std::string out = staticHead;
    if (out.size() < 2 || out.back() != ']') return std::string();    /* not the shape we expect */
    out.pop_back();                                                  /* drop the closing ] */

    /* "a","b",... ; `none` stands in for an empty list */
    auto options = [&fit](std::string &to, const std::vector<std::string> &names, const char *none) {
        if (names.empty() && none) { to += '"'; to += none; to += '"'; return; }
        const size_t n = fit.count && names.size() > fit.count ? fit.count : names.size();
        for (size_t i = 0; i < n; i++) {
            if (i) to += ",";
            to += "\""; jsonAppendName(to, shortLabel(names[i], fit.chars)); to += "\"";
        }
    };

    /* The keys the Move turns (the wavetable selects, bank, the demo switch, pack and part, preset) go in `hand` and are inserted right after the
     * page keys at the end, not appended: the host caches only the first 256 declared keys, and a set of one past that makes it re-fetch and re-parse
     * this whole list on the SPI callback (23 ms, four times a second). See tools/gen_params.py. */
    std::string hand;

    out += ",{\"key\":\"wt_pack\",\"name\":\"WT Pack\",\"type\":\"enum\",\"options\":[";
    options(out, l.packs, nullptr);
    out += "]}";

    for (int osc = 0; osc < wtSlots; osc++) {
        char head[96];
        snprintf(head, sizeof head,
                 ",{\"key\":\"wt%d_select\",\"name\":\"WT%d Table\",\"type\":\"enum\",\"options\":[",
                 osc + 1, osc + 1);
        hand += head;
        options(hand, l.tables, nullptr);
        hand += "]}";
    }

    /* The Bank knob: one option per preset folder in the store (scanned before the second publish). */
    hand += ",{\"key\":\"bank\",\"name\":\"Bank\",\"type\":\"enum\",\"options\":[";
    options(hand, l.banks, "User");
    hand += "]}";

    /* The demo mode: the switch, the pack and the part inside it (enums whose options are what the scan found), and the list and the current pick for the web panel. */
    hand += ",{\"key\":\"demo_on\",\"name\":\"Demo\",\"type\":\"enum\",\"options\":[\"Off\",\"On\"],\"default\":\"Off\"}";
    hand += ",{\"key\":\"demo_pack\",\"name\":\"Demo Pack\",\"type\":\"enum\",\"options\":[";
    options(hand, l.demoPacks, "None");
    hand += "]},{\"key\":\"demo_part\",\"name\":\"Demo Part\",\"type\":\"enum\",\"options\":[";
    options(hand, l.demoParts, "None");
    hand += "]}";
    out += ",{\"key\":\"demo_now\",\"name\":\"Demo Now\",\"type\":\"string\",\"access\":\"read\"}";
    /* The Macros page cells read macro_names on every redraw, and the Tone and Grain pages read the shapes below: keys the Move reads belong
     * inside the host's 256-key cache (a key past it makes the host parse this whole list again, ~20 ms, on every read: the Move UI ran at
     * 5-10 fps while the Macros page was open, and a preset scroll hung for over a minute). */
    hand += ",{\"key\":\"macro_names\",\"name\":\"Macro Names\",\"type\":\"string\",\"access\":\"read\"}"
            ",{\"key\":\"macro_routes\",\"name\":\"Macro Routes\",\"type\":\"string\",\"access\":\"read\"}"
            /* the Grain pages' moving head: each sample slot's End, Loop, Loop start, Loop end, Manual position and Position in one short string ("100,1,22,81,0,0") */
            ",{\"key\":\"smp_gv\",\"name\":\"Sample 1 Grain View\",\"type\":\"string\",\"access\":\"read\"}"
            ",{\"key\":\"smp2_gv\",\"name\":\"Sample 2 Grain View\",\"type\":\"string\",\"access\":\"read\"}"
            ",{\"key\":\"smp3_gv\",\"name\":\"Sample 3 Grain View\",\"type\":\"string\",\"access\":\"read\"}";

    /* The shape digests are DECLARED here so they reach a browser at all:
     * schwung-manager only streams values for keys chain_params declares
     * (its standalone getParam answers from that cache and never asks the
     * device), so an undeclared key reads as undefined forever.
     *
     * They are STRINGS -- a shape digest reads "24,64,Wjvx..." and wt_paths
     * is a path list -- and they are READ-ONLY, so that is what they now say.
     * They were declared as an int with min==max==0, which was an invented
     * way to mean "no knob can turn this" from before `access` existed. It
     * described them wrongly twice over: not integers, and an empty numeric
     * range is a defect in anything that really is a number, which is what
     * Schwung's contract checker correctly flagged (issue #2).
     *
     * They remain on no ui_hierarchy page deliberately: they are a data
     * channel for the web panel, not a control. */
    hand += ",{\"key\":\"wt1_shape\",\"name\":\"WT1 Shape\",\"type\":\"string\",\"access\":\"read\"}"
            ",{\"key\":\"wt2_shape\",\"name\":\"WT2 Shape\",\"type\":\"string\",\"access\":\"read\"}"
            ",{\"key\":\"wt3_shape\",\"name\":\"WT3 Shape\",\"type\":\"string\",\"access\":\"read\"}"
            ",{\"key\":\"smp_shape\",\"name\":\"Sample 1 Shape\",\"type\":\"string\",\"access\":\"read\"}"
            ",{\"key\":\"smp2_shape\",\"name\":\"Sample 2 Shape\",\"type\":\"string\",\"access\":\"read\"}"
            ",{\"key\":\"smp3_shape\",\"name\":\"Sample 3 Shape\",\"type\":\"string\",\"access\":\"read\"}"
            ",{\"key\":\"wt_on\",\"name\":\"WT On\",\"type\":\"string\",\"access\":\"read\"}";
    /* The web panel's preset browser: the store's banks and preset names (read-only, built when the store is scanned), the
     * current bank and index, and the index to load. `bank` is declared above; `preset` is the index within the current bank. */
    /* ORDER MATTERS. The manager's initial value fetch sends in batches of 8 and flushes the last partial batch only when the LAST declared key
     * reads back fine: if it does not, the tail of the list is dropped. So the list ends on a key that always reads (preset_now); `preset` moved up with the other keys the Move turns (see `hand`). */
    out += ",{\"key\":\"rescan\",\"name\":\"Rescan\",\"type\":\"int\",\"min\":0,\"max\":1,\"default\":0}"
           ",{\"key\":\"macro_edit\",\"name\":\"Macro Edit\",\"type\":\"string\"}"
           /* THE BIG LISTS THE WEB PANEL READS (preset_list, demo_list, wt_paths) ARE NOT DECLARED. The page only sees what the manager pushes into its cache, and the manager pushes a
            * module's JSON state (which leaves these out on purpose: they are tens of kilobytes) plus its viz `extra_keys`, and it leaves an extra key out when the key is also declared (it
            * expects the main loop to fetch it). So since the state became JSON (5daf5ed) a declared list never reached the page: the preset bar, the Demo packs and the wavetable list stayed empty,
            * and so did a sample's shape. An undeclared key named as an extra key IS read by the manager (on subscribe, on a preset change and every 500 ms while the panel is open, sending only
            * what moved). `data_feed` is the declared key that carries that list; it sits on no page, so the Move's own screen never reads the keys (an undeclared key read by the host
            * re-parses chain_params). */
           ",{\"key\":\"data_feed\",\"name\":\"Data Feed\",\"type\":\"string\",\"access\":\"read\",\"viz\":{\"kind\":\"custom:tinyhouse_data\",\"group\":\"data\",\"role\":\"a\",\"extra_keys\":[\"preset_list\",\"demo_list\",\"wt_paths\",\"live_vals\"]}}"
           ",{\"key\":\"preset_now\",\"name\":\"Preset Now\",\"type\":\"string\",\"access\":\"read\"}";
    hand += ",{\"key\":\"preset\",\"name\":\"Preset\",\"type\":\"int\",\"min\":0,\"max\":4095,\"default\":0}";
    out.insert(prioLen, hand);      /* right after the page keys: inside the host's 256-key cache */
    out += "]";
    return out;
}

/* The list to publish: the first of kLabelFits that fits in `max` (empty if the static head is not a JSON array). `used`, if given, says which. */
inline std::string chainParams(const char *staticHead, size_t prioLen, int wtSlots, const ChainParamLists &l, size_t max = kChainParamsMax, LabelFit *used = nullptr)
{
    std::string out;
    for (const LabelFit &fit : kLabelFits) {
        out = chainParamsAt(staticHead, prioLen, wtSlots, l, fit);
        if (used) *used = fit;
        if (out.size() <= max) break;
    }
    return out;
}

} // namespace tb

#endif
