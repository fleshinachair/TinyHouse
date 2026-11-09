/* The preset list the web panel's browser reads: every bank of the store with the names of its
 * presets, as one JSON string  {"b":[{"n":"Factory","p":["BA - Foo","CH - Bar"]},...]}.
 *
 * Built once on the worker when the store is scanned and served from the snapshot, because
 * get_param runs on the SPI callback and must not format or touch the filesystem. The browser
 * addresses a preset by its bank and its index inside the bank, which is what the module's
 * "bank" and "preset" keys take. */
#ifndef TABLOR_PRESET_DIGEST_H
#define TABLOR_PRESET_DIGEST_H

#include <string>
#include <vector>

namespace tb {

/* JSON string escape: quote and backslash escaped, control characters dropped (names are file names). */
inline void jsonEscape(std::string &out, const std::string &in)
{
    for (char c : in) {
        if (c == '"' || c == '\\') { out += '\\'; out += c; }
        else if ((unsigned char) c < 0x20) continue;
        else out += c;
    }
}

inline std::string presetDigest(const std::vector<std::string> &banks,
                                const std::vector<std::vector<std::string>> &names)
{
    std::string d = "{\"b\":[";
    for (size_t b = 0; b < banks.size(); b++) {
        if (b) d += ',';
        d += "{\"n\":\"";
        jsonEscape(d, banks[b]);
        d += "\",\"p\":[";
        if (b < names.size())
            for (size_t i = 0; i < names[b].size(); i++) {
                if (i) d += ',';
                d += '"';
                jsonEscape(d, names[b][i]);
                d += '"';
            }
        d += "]}";
    }
    d += "]}";
    return d;
}

} // namespace tb

#endif
