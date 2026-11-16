/* Tablor — 2-osc wavetable synth for Ableton Move (Schwung module).
 *
 * plugin_api_v2. Parameter surface + version-tagged state round-trip +
 * the phase 3 engine: 8 voices, poly/mono, glide, mod matrix.
 *
 * Realtime rules (docs/REALTIME_SAFETY.md): render_block never allocates,
 * never touches the filesystem, never logs.
 */

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cmath>
#include <cstdint>
#include <new>
#include <algorithm>
#include <dirent.h>
#include <string>
#include <sys/stat.h>
#include <vector>
#include <atomic>
#include <chrono>
#include <memory>
#include <mutex>

#include "../host/plugin_api_v1.h"
#include "params.h"
#include "engine.h"
#include "wt/scanner.h"
#include "wt/loader.h"
#include "partjoin.h"
#include "preset_digest.h"

static const host_api_v1_t *g_host = nullptr;

/* ------------------------------------------------------------------ */
/* Instance                                                            */
/* ------------------------------------------------------------------ */

struct tablor_instance {
    tb::Engine engine;
    tb::WtScanner scanner;                  /* used for first-run seeding only */
    tb::WtLoader loader { engine };
    char  module_dir[512] = {};

    /* wavetable selection = absolute file paths ("" = built-in Init) */
    char  wt_path[TB_WT_SLOTS + 4][512] = {};      /* slot TB_WT_SLOTS is the sample set, the next two the second and third, the last the convolution reverb's impulse response */

    /* presets: factory (from the module) + UNLIMITED user .tblr files in
     * the user library (filename = preset name, content = TBLR1 blob).
     *
     * The worker rebuilds this list whenever the folder changes while the
     * audio thread reads it in get_param, so it is published as an IMMUTABLE
     * snapshot and swapped atomically — never mutated in place. */
    struct preset { std::string name, blob, path, bank; };   /* path "" = factory */
    /* `banks`: the store's presets are grouped in folders (one per pack: Deep House, ...) and the loose files are the
     * "User" bank. items are ordered by bank then name, so each bank is the contiguous run [bank_first, bank_first + bank_n). */
    struct PresetList {
        std::vector<preset> items;
        int factory_count = 0;
        std::vector<std::string> banks;
        std::vector<int> bank_first, bank_n;
        std::string digest;               /* the store's banks and preset names as JSON, for the web panel's browser (preset_digest.h) */
    };
    std::shared_ptr<const PresetList> presets { std::make_shared<PresetList>() };
    std::atomic<int> preset_index { 0 };
    std::atomic<int> pending_preset { -1 };   /* a preset asked for before the list was scanned: applied when it is */
    std::atomic<bool> state_seen { false };   /* some sound (state or preset) has been applied since the module loaded */

    std::shared_ptr<const PresetList> presetList() const
    { return std::atomic_load(&presets); }

    /* The preset page: the .json files in Schwung's store for this module, which is where the converted
     * Deep House sounds live. Scanned once on the worker at startup and published as an immutable snapshot
     * like the list above. `store_index` is the one the page shows and last loaded. */
    std::shared_ptr<const PresetList> store { std::make_shared<PresetList>() };
    std::atomic<int> store_index { 0 };   /* GLOBAL index into store->items */
    std::atomic<int> bank_index { 0 };    /* which bank the preset page lists */
    /* A sound the browser chose and the engine has not taken yet. The NAME follows the knob at once (store_index); the sound is applied once the
     * knob has rested for settle_ms (a spin through 384 presets applies the one it lands on, not 384), or when something reads what it set. */
    std::atomic<int> pend_idx { -1 };
    std::atomic<long long> pend_due { 0 };
    int settle_ms = 150;                  /* TB_PRESET_SETTLE_MS overrides it; 0 applies at once (the test tools rely on that) */
    /* The "pk" a restored state named (see preset_key), held until the worker has scanned the store and can find it. */
    std::shared_ptr<const std::string> want_pk;

    std::shared_ptr<const PresetList> storeList() const
    { return std::atomic_load(&store); }

    /* The demo parts: MIDI parts of songs the remote UI uploaded, /data/UserData/schwung/midi/<module>/<Pack>/<Part>.tdm. Scanned on the worker and published as a
     * snapshot like the presets. `demo_pack` and `demo_part` (the index inside the pack) pick the part the engine plays when `demo_on` is set. */
    struct DemoList {
        std::vector<std::string> packs;
        std::vector<int> pack_first, pack_n;
        std::vector<std::string> names, paths;         /* by global index */
        std::string digest;                            /* {"b":[{"n":pack,"p":[parts]}]} for the web panel */
    };
    std::shared_ptr<const DemoList> demos { std::make_shared<DemoList>() };
    std::atomic<int> demo_pack { 0 }, demo_part { 0 }, demo_on { 0 };
    /* What a restored state said the slot was playing: the pack and the part by NAME (an index shifts when a song is added or removed), and whether it
     * was on. Held until the worker has scanned the demo store, then applied (apply_want_demo). */
    struct WantDemo { std::string pack, part; bool on = false; };
    std::shared_ptr<const WantDemo> want_demo;
    std::shared_ptr<const DemoList> demoList() const
    { return std::atomic_load(&demos); }

    /* state blob scratch */
    char  state_buf[16 * 1024];
    char  arp_str[4096] = {};                 /* the preset's arpeggiator, likewise */
    char  clip_str[4096] = {};                /* the preset's note pattern as the state carried it (kept to write it back out) */
    char  mac_str[3072] = {};                 /* the preset's macro routes ("macro:key:travel,..."), kept to write back out */
    char  macn_str[512] = {};                 /* the macros' names ("a|b|c|..."), likewise */
    /* The macro names as chain_params last described them. A preset with other names republishes chain_params (the names ride the macro
     * knobs' `unit`, which the host shows beside the value when a knob is touched), on the worker; is_loading says "1" until that is done. */
    std::shared_ptr<const std::string> macn_pub { std::make_shared<std::string>() };
    std::atomic<int> pub_pending { 0 };

    /* ---- Turnable wavetable selection -------------------------------
     * The filepath control (wt1_table/wt2_table) is OPAQUE to the host:
     * it can be opened, never turned, because turning a path would write
     * nonsense into it. So a pot cannot change the wavetable, which is
     * what was reported.
     *
     * These add the turnable half WITHOUT touching the filepath control:
     * a pack chosen from a list, and one enum per oscillator whose
     * options are the entries in that pack. An enum is turnable AND
     * divable, so hold+click opens a scrolling picker for a long pack.
     *
     * Rebuilt only when the scan or the pack changes, on the worker and
     * NEVER on the SPI callback -- neither in get_param (see the note above
     * tb_chain_params_json) nor in set_param, which is the same thread.
     * Published as an immutable snapshot and swapped atomically, exactly
     * like the preset list: the audio thread only ever takes a pointer. */
    struct SelSnapshot {
        std::vector<std::string> packs;    /* "Pack" prefixes, [0] = All    */
        std::vector<std::string> paths;    /* the whole scan, by index      */
        std::vector<int>         filtered; /* indices into paths            */
        std::string chain_params;          /* static head + dynamic options */
        std::string pack_list;             /* wt_pack_list JSON             */
        std::string path_list;             /* wt_paths JSON, index-aligned
                                            * with the wtN_select options  */
    };
    std::shared_ptr<const SelSnapshot> sel { std::make_shared<SelSnapshot>() };
    std::atomic<int> wt_pack { 0 };

    std::shared_ptr<const SelSnapshot> selection() const
    { return std::atomic_load(&sel); }

    /* false until the worker has scanned, loaded presets and published the
     * Init table; the audio path renders silence in the meantime */
    std::atomic<bool> ready { false };

    /* A drawable digest of the table each oscillator actually has loaded, so
     * a UI can show the real waveform instead of a stand-in. Built on the
     * worker when a table lands and published as an immutable string; the
     * audio thread only ever hands out the pointer. */
    std::shared_ptr<const std::string> wt_shape[TB_WT_SLOTS];
    std::shared_ptr<const std::string> shape(int osc) const
    { return std::atomic_load(&wt_shape[osc]); }

    float *params() { return engine.pots; }
};

/* User presets are plain files the user can copy, share and manage:
 *   /data/UserData/UserLibrary/Tablor Presets/<Name>.tblr
 * filename (minus extension) IS the preset name; content is one
 * TBLR1 state blob line. */
/* Not "Tablor Presets": the migration below MOVES what it finds and deletes the
 * originals, so sharing stock Tablor's folder would take its user presets. */
static constexpr const char *kPresetDir =
    "/data/UserData/UserLibrary/Tablor DH Presets";

static std::string sanitize_preset_name(const char *raw)
{
    std::string n;
    for (const char *s = raw; *s && n.size() < 40; s++) {
        char c = *s;
        if (c == '/' || c == '\\' || c == ':' || c == '*' || c == '?' ||
            c == '"' || c == '<' || c == '>' || c == '|' || c == ';' ||
            c == '=' || c == '\n' || c == '\r')
            c = ' ';
        n += c;
    }
    while (!n.empty() && (n.front() == ' ' || n.front() == '.')) n.erase(0, 1);
    while (!n.empty() && n.back() == ' ') n.pop_back();
    if (n.empty()) n = "Untitled";
    return n;
}

static std::string preset_file_for(const std::string &name)
{
    return std::string(kPresetDir) + "/" + name + ".tblr";
}

/* A name nobody has yet: "Name", "Name 2", "Name 3", … */
static std::string unique_preset_name(const std::string &want)
{
    std::string name = want;
    for (int i = 2; i < 1000; i++) {
        struct stat st;
        if (::stat(preset_file_for(name).c_str(), &st) != 0)
            return name;
        name = want + " " + std::to_string(i);
    }
    return want;
}

static bool read_blob_file(const char *path, std::string &blob)
{
    FILE *f = fopen(path, "r");
    if (!f) return false;
    char line[8192];
    bool ok = false;
    if (fgets(line, sizeof line, f)) {
        line[strcspn(line, "\r\n")] = 0;
        const char *b = line;
        const char *bar = strchr(line, '|');    /* legacy "Name|blob" lines */
        if (bar) b = bar + 1;
        if (!strncmp(b, "TBLR1;", 6) || !strncmp(b, "TBLR2;", 6)) { blob = b; ok = true; }
    }
    fclose(f);
    return ok;
}

static void write_preset_file(const std::string &path, const char *blob)
{
    FILE *f = fopen(path.c_str(), "w");
    if (f) {
        fputs(blob, f);
        fputc('\n', f);
        fclose(f);
    }
}

/* ---- Schwung's own preset store -------------------------------------------
 *
 * Schwung keeps module presets at
 *   /data/UserData/schwung/presets/<module-id>/<name>.json
 * as {"name","module","version","state"} where `state` is the very blob this
 * module already answers for `state` (shadow_ui_presets.mjs). Its browser is
 * reached from the slot, works for every module, and auditions a preset as
 * you scroll -- so Tablor's own store is the odd one out, not the feature.
 *
 * Both halves run once on the worker at startup: the factory sounds are
 * seeded there so a fresh install has them, and any .tblr the user already
 * saved is copied across. The .tblr files are LEFT ALONE -- a migration that
 * deletes the only copy of someone's sound is not a migration -- and a stamp
 * file stops the copy repeating, so a preset deleted in Schwung stays
 * deleted instead of reappearing on the next boot.
 */
static constexpr const char *kSchwungPresetDir =
    "/data/UserData/schwung/presets/" TB_MODULE_ID;
static constexpr const char *kInstalledStamp =
    "/data/UserData/schwung/presets/" TB_MODULE_ID "/.installed";

/* A NAME becomes both the display string and the file name, so it loses path
 * separators and control characters -- matching Schwung's own safeFileStem --
 * and loses quotes and backslashes too, since it is read back out with a
 * regex (/"name"\s*:\s*"([^"]+)"/) that neither escapes nor tolerates them. */
static std::string json_safe_name(const std::string &in)
{
    std::string o;
    for (char c : in) {
        if (c == '"' || c == '\\' || (unsigned char) c < 0x20) continue;
        if (c == '/') { o += '-'; continue; }
        o += c;
    }
    while (!o.empty() && o.back() == ' ') o.pop_back();
    return o.empty() ? std::string("Preset") : o;
}

/* A STATE BLOB is data and must survive intact: it carries absolute wavetable
 * paths, so a slash is content, not a separator. Escaping it as the name is
 * escaped turned "/data/UserData/..." into "-data-UserData-...", which loads
 * as a missing file -- every migrated preset would have fallen back to the
 * Init table, silently. Only the two characters JSON itself reserves are
 * touched, and they are escaped rather than dropped. */
static std::string json_escape(const std::string &in)
{
    std::string o;
    for (char c : in) {
        if (c == '"' || c == '\\') { o += '\\'; o += c; continue; }
        if ((unsigned char) c < 0x20) continue;      /* no newlines in a blob */
        o += c;
    }
    return o;
}

/* Write one preset into Schwung's store. Returns false if it did not land --
 * the caller is about to delete the source file, so "probably worked" is not
 * good enough. */
static bool write_schwung_json(const std::string &path, const std::string &name,
                               const std::string &blob)
{
    FILE *f = fopen(path.c_str(), "w");
    if (!f) return false;
    fprintf(f, "{\"name\":\"%s\",\"module\":\"" TB_MODULE_ID "\",\"version\":1,\"state\":\"%s\"}\n",
            name.c_str(), json_escape(blob).c_str());
    const bool ok = (fflush(f) == 0);
    fclose(f);
    if (!ok) { ::remove(path.c_str()); return false; }
    struct stat st;                                   /* it exists, and it is not empty */
    if (::stat(path.c_str(), &st) != 0 || st.st_size < 16) return false;
    return true;
}

/* Seed one factory sound. Never overwrites: a factory preset the user edited
 * and re-saved under the same name is theirs now. */
static void write_schwung_preset(const std::string &name, const std::string &blob)
{
    const std::string safe = json_safe_name(name);
    const std::string path = std::string(kSchwungPresetDir) + "/" + safe + ".json";
    struct stat st;
    if (::stat(path.c_str(), &st) == 0) return;
    write_schwung_json(path, safe, blob);
}

/* Does this .json already hold exactly this sound? Then the .tblr beside it
 * is a leftover from an earlier migration and can simply go. */
static bool schwung_json_holds(const std::string &path, const std::string &blob)
{
    FILE *f = fopen(path.c_str(), "r");
    if (!f) return false;
    std::string raw;
    char chunk[1024];
    size_t got;
    while ((got = fread(chunk, 1, sizeof chunk, f)) > 0) raw.append(chunk, got);
    fclose(f);
    const std::string want = "\"state\":\"" + json_escape(blob) + "\"";
    return raw.find(want) != std::string::npos;
}

/* MOVE every .tblr into Schwung's store: convert, verify, then delete the
 * original. Deleting is what makes this self-completing -- an empty folder is
 * a finished migration, so nothing has to remember that it ran, and a preset
 * deleted in Schwung's browser has no .tblr left to come back from.
 *
 * The original is removed ONLY after the .json is on disk and non-empty. */
static void migrate_tblr_into_schwung()
{
    DIR *d = ::opendir(kPresetDir);
    if (!d) return;
    std::vector<std::string> files;
    while (dirent *e = ::readdir(d)) {
        const char *dot = strrchr(e->d_name, '.');
        if (dot && strcasecmp(dot, ".tblr") == 0) files.push_back(e->d_name);
    }
    ::closedir(d);

    for (const std::string &fn : files) {
        const std::string src = std::string(kPresetDir) + "/" + fn;
        std::string blob;
        if (!read_blob_file(src.c_str(), blob) || blob.empty()) continue;

        const std::string stem = fn.substr(0, fn.rfind('.'));
        const std::string safe = json_safe_name(stem);

        /* Already migrated under this name? Drop the leftover and move on. */
        std::string path = std::string(kSchwungPresetDir) + "/" + safe + ".json";
        struct stat st;
        if (::stat(path.c_str(), &st) == 0) {
            if (schwung_json_holds(path, blob)) { ::remove(src.c_str()); continue; }
            /* A DIFFERENT sound owns that name -- a factory seed, most
             * likely. Take the next free one, the way Schwung's own save
             * does, rather than overwriting either. */
            for (int n = 2; n < 100; n++) {
                char cand[512];
                snprintf(cand, sizeof cand, "%s/%s %d.json",
                         kSchwungPresetDir, safe.c_str(), n);
                if (::stat(cand, &st) != 0) { path = cand; break; }
            }
            char disp[256];
            snprintf(disp, sizeof disp, "%s", path.c_str());
            const char *slash = strrchr(disp, '/');
            std::string dispName = slash ? slash + 1 : disp;
            dispName = dispName.substr(0, dispName.rfind(".json"));
            if (write_schwung_json(path, dispName, blob)) ::remove(src.c_str());
            continue;
        }
        if (write_schwung_json(path, safe, blob)) ::remove(src.c_str());
    }
}

/* Seed the factory sounds and move any .tblr across -- ON UPDATE, once.
 *
 * The stamp holds the module version that last ran this. A fresh install has
 * no stamp; an update changes it; every ordinary boot in between matches and
 * returns immediately, which is the point -- there is nothing to do on a
 * device that has already been converted, so it should not go looking.
 *
 * Version-keyed rather than a plain "done" flag so a release that adds a
 * factory sound still delivers it, and so a user who somehow still has a
 * .tblr gets it moved at the next update rather than never.
 */
static void export_presets_to_schwung(tablor_instance *inst)
{
    ::mkdir("/data/UserData/schwung/presets", 0755);
    ::mkdir(kSchwungPresetDir, 0755);

    char stamp[64] = {};
    FILE *f = fopen(kInstalledStamp, "r");
    if (f) { if (!fgets(stamp, sizeof stamp, f)) stamp[0] = 0; fclose(f); }
    for (char *c = stamp; *c; c++) if (*c == '\n') { *c = 0; break; }
    if (strcmp(stamp, TB_VERSION) == 0) return;      /* already done for this build */

    /* Factory sounds. Never overwrites: one you edited and re-saved under the
     * same name is yours now, and one you deleted stays deleted until a
     * release that changes this version brings it back. */
    auto ps = inst->presetList();
    const int nf = ps->factory_count;
    for (int i = 0; i < nf && i < (int) ps->items.size(); i++) {
        if (ps->items[(size_t) i].name == "Init") continue;   /* every module has one */
        write_schwung_preset(ps->items[(size_t) i].name, ps->items[(size_t) i].blob);
    }

    migrate_tblr_into_schwung();

    f = fopen(kInstalledStamp, "w");
    if (f) { fprintf(f, "%s\n", TB_VERSION); fclose(f); }
}

/* One-time migration of the old fixed-slot files (presets/user/uN.tbl). */
static void migrate_old_user_slots(tablor_instance *inst)
{
    for (int i = 0; i < 8; i++) {
        char old[600];
        snprintf(old, sizeof old, "%s/presets/user/u%d.tbl",
                 inst->module_dir, i + 1);
        FILE *f = fopen(old, "r");
        if (!f) continue;
        char line[8192];
        std::string name = "User " + std::to_string(i + 1), blob;
        if (fgets(line, sizeof line, f)) {
            line[strcspn(line, "\r\n")] = 0;
            char *bar = strchr(line, '|');
            const char *b = line;
            if (bar) { *bar = 0; if (line[0]) name = line; b = bar + 1; }
            if (!strncmp(b, "TBLR1;", 6) || !strncmp(b, "TBLR2;", 6)) blob = b;
        }
        fclose(f);
        if (!blob.empty() && blob != "TBLR1;" && blob != "TBLR2;")
            write_preset_file(preset_file_for(unique_preset_name(
                sanitize_preset_name(name.c_str()))), blob.c_str());
        ::remove(old);
    }
}

/* Re-list the user preset folder (sorted by name, case-insensitive).
 * WORKER THREAD ONLY: does directory I/O, then publishes a new snapshot. */
static void scan_user_presets(tablor_instance *inst)
{
    auto cur = inst->presetList();
    auto next = std::make_shared<tablor_instance::PresetList>();
    next->factory_count = cur->factory_count;
    next->items.assign(cur->items.begin(),
                       cur->items.begin() + cur->factory_count);

    std::vector<tablor_instance::preset> user;
    DIR *d = ::opendir(kPresetDir);
    if (d) {
        while (dirent *e = ::readdir(d)) {
            const char *dot = strrchr(e->d_name, '.');
            if (!dot || strcasecmp(dot, ".tblr") != 0) continue;
            std::string path = std::string(kPresetDir) + "/" + e->d_name;
            std::string blob;
            if (!read_blob_file(path.c_str(), blob)) continue;
            user.push_back({ std::string(e->d_name, (size_t) (dot - e->d_name)),
                             blob, path });
        }
        ::closedir(d);
    }
    std::sort(user.begin(), user.end(),
              [](const tablor_instance::preset &a,
                 const tablor_instance::preset &b) {
                  return strcasecmp(a.name.c_str(), b.name.c_str()) < 0;
              });
    for (auto &u : user)
        next->items.push_back(std::move(u));

    if (inst->preset_index.load() >= (int) next->items.size())
        inst->preset_index = 0;
    std::atomic_store(&inst->presets, std::shared_ptr<const tablor_instance::PresetList>(next));
}

/* The state blob out of one of Schwung's preset files: {"name":..,"module":..,"version":1,"state":".."}.
 * Our blobs hold no quotes, so the value ends at the first unescaped quote; \ and \" are undone. */
static bool read_store_state(const char *path, std::string &blob)
{
    FILE *f = fopen(path, "r");
    if (!f) return false;
    std::string raw;
    char chunk[4096];
    size_t got;
    while ((got = fread(chunk, 1, sizeof chunk, f)) > 0 && raw.size() < 64 * 1024) raw.append(chunk, got);
    fclose(f);
    /* "state" then optional spaces, a colon, optional spaces, and the opening quote: Schwung writes it
     * without spaces, Python's json.dumps (the converter) with them. */
    size_t k = raw.find("\"state\"");
    if (k == std::string::npos) return false;
    k += 7;
    while (k < raw.size() && raw[k] == ' ') k++;
    if (k >= raw.size() || raw[k] != ':') return false;
    k++;
    while (k < raw.size() && raw[k] == ' ') k++;
    if (k >= raw.size() || raw[k] != '"') return false;
    blob.clear();
    for (size_t i = k + 1; i < raw.size(); i++) {
        if (raw[i] == '\\' && i + 1 < raw.size()) { blob += raw[++i]; continue; }
        if (raw[i] == '"') break;
        blob += raw[i];
    }
    return !strncmp(blob.c_str(), "TBLR1;", 6) || !strncmp(blob.c_str(), "TBLR2;", 6);
}

/* One folder's .json presets into `out`, tagged with `bank`, in name order. */
static void scan_store_dir(const std::string &dir, const std::string &bank, std::vector<tablor_instance::preset> &out)
{
    const size_t from = out.size();
    if (DIR *d = ::opendir(dir.c_str())) {
        while (dirent *e = ::readdir(d)) {
            const char *dot = strrchr(e->d_name, '.');
            if (!dot || e->d_name[0] == '.' || strcasecmp(dot, ".json") != 0) continue;
            const std::string path = dir + "/" + e->d_name;
            std::string blob;
            if (!read_store_state(path.c_str(), blob)) continue;
            out.push_back({ std::string(e->d_name, (size_t) (dot - e->d_name)), blob, path, bank });
        }
        ::closedir(d);
    }
    std::sort(out.begin() + (long) from, out.end(),
              [](const tablor_instance::preset &a, const tablor_instance::preset &b) {
                  return strcasecmp(a.name.c_str(), b.name.c_str()) < 0;
              });
}

/* The bank folders of the store, by name. A bank is a folder; the loose files are the "User" bank, listed last. */
static std::vector<std::string> store_bank_dirs()
{
    std::vector<std::string> dirs;
    if (DIR *d = ::opendir(kSchwungPresetDir)) {
        while (dirent *e = ::readdir(d)) {
            if (e->d_name[0] == '.') continue;
            struct stat st;
            const std::string path = std::string(kSchwungPresetDir) + "/" + e->d_name;
            if (::stat(path.c_str(), &st) == 0 && S_ISDIR(st.st_mode)) dirs.push_back(e->d_name);
        }
        ::closedir(d);
    }
    std::sort(dirs.begin(), dirs.end(), [](const std::string &a, const std::string &b) { return strcasecmp(a.c_str(), b.c_str()) < 0; });
    return dirs;
}

static void scan_store_presets(tablor_instance *inst)
{
    auto next = std::make_shared<tablor_instance::PresetList>();
    auto add_bank = [&](const std::string &name, const std::string &dir) {
        const size_t first = next->items.size();
        scan_store_dir(dir, name, next->items);
        if (next->items.size() == first) return;
        next->banks.push_back(name);
        next->bank_first.push_back((int) first);
        next->bank_n.push_back((int) (next->items.size() - first));
    };
    for (const auto &b : store_bank_dirs())
        add_bank(b, std::string(kSchwungPresetDir) + "/" + b);
    add_bank("User", kSchwungPresetDir);
    if (next->banks.empty()) {                 /* nothing stored yet: one empty bank, so the page has something to show */
        next->banks.push_back("User");
        next->bank_first.push_back(0);
        next->bank_n.push_back(0);
    }
    {
        std::vector<std::vector<std::string>> names(next->banks.size());
        for (size_t k = 0; k < next->banks.size(); k++)
            for (int i = next->bank_first[k]; i < next->bank_first[k] + next->bank_n[k]; i++)
                names[k].push_back(next->items[(size_t) i].name);
        next->digest = tb::presetDigest(next->banks, names);
    }
    int b = inst->bank_index.load();
    if (b < 0 || b >= (int) next->banks.size()) b = 0;
    inst->bank_index = b;
    const int first = next->bank_first[(size_t) b], n = next->bank_n[(size_t) b];
    const int si = inst->store_index.load();
    if (si < first || si >= first + n) inst->store_index = first;
    std::atomic_store(&inst->store, std::shared_ptr<const tablor_instance::PresetList>(next));
}

/* ---- the demo parts ------------------------------------------------------------------- */
static constexpr const char *kDemoDir = "/data/UserData/schwung/midi/" TB_MODULE_ID;

/* The part the pack and part indexes name, read and handed to the engine (null when there is none). WORKER THREAD ONLY. */
static void load_demo(tablor_instance *inst)
{
    auto dl = inst->demoList();
    const int p = inst->demo_pack.load();
    if (p < 0 || p >= (int) dl->packs.size() || dl->pack_n[(size_t) p] <= 0) { inst->engine.setDemo(nullptr); return; }
    const int i = dl->pack_first[(size_t) p] + std::min(std::max(inst->demo_part.load(), 0), dl->pack_n[(size_t) p] - 1);
    std::string raw;
    if (FILE *f = fopen(dl->paths[(size_t) i].c_str(), "r")) {
        char chunk[8192];
        size_t got;
        while ((got = fread(chunk, 1, sizeof chunk, f)) > 0 && raw.size() < 2 * 1024 * 1024) raw.append(chunk, got);
        fclose(f);
    }
    inst->engine.setDemo(tb::parseDemo(raw.c_str()));
}

/* A state key's value: ';' is the separator of the state, so it cannot appear in a name. */
static std::string state_safe(std::string s)
{
    std::replace(s.begin(), s.end(), ';', ',');
    return s;
}

/* Put the slot back on the pack and part its restored state named, and switch the demo on if it was on. True when the demo list changed
 * (the part list the Demo Part knob offers follows the pack). WORKER THREAD ONLY. Waits (keeps the request) until the store has been scanned. */
static bool apply_want_demo(tablor_instance *inst)
{
    auto w = std::atomic_load(&inst->want_demo);
    if (!w) return false;
    auto dl = inst->demoList();
    if (dl->packs.empty()) return false;                       /* not scanned yet: scan_demos asks again */
    std::atomic_store(&inst->want_demo, std::shared_ptr<const tablor_instance::WantDemo>());
    for (size_t p = 0; p < dl->packs.size(); p++) {
        if (state_safe(dl->packs[p]) != w->pack) continue;
        for (int i = 0; i < dl->pack_n[p]; i++) {
            if (state_safe(dl->names[(size_t) (dl->pack_first[p] + i)]) != w->part) continue;
            inst->demo_pack = (int) p;
            inst->demo_part = i;
            load_demo(inst);
            if (w->on) {                                              /* on again, and ready: it plays when the transport does (it never starts it) */
                inst->demo_on = 1;
                inst->engine.setDemoOn(true);
            }
            return true;
        }
    }
    return false;                                              /* the song is gone: the slot stays as it is, demo off */
}

/* Every pack folder of the demo store with its .tdm parts in name order; loose files form the pack "Demos". WORKER THREAD ONLY. */
static void scan_demos(tablor_instance *inst)
{
    auto next = std::make_shared<tablor_instance::DemoList>();
    auto add_pack = [&](const std::string &name, const std::string &dir) {
        std::vector<std::pair<std::string, std::string>> found;
        if (DIR *d = ::opendir(dir.c_str())) {
            while (dirent *e = ::readdir(d)) {
                const char *dot = strrchr(e->d_name, '.');
                if (!dot || e->d_name[0] == '.' || strcasecmp(dot, ".tdm") != 0) continue;
                found.push_back({ std::string(e->d_name, (size_t) (dot - e->d_name)), dir + "/" + e->d_name });
            }
            ::closedir(d);
        }
        if (found.empty()) return;
        std::sort(found.begin(), found.end(), [](const auto &a, const auto &b) { return strcasecmp(a.first.c_str(), b.first.c_str()) < 0; });
        next->packs.push_back(name);
        next->pack_first.push_back((int) next->names.size());
        next->pack_n.push_back((int) found.size());
        for (auto &f : found) { next->names.push_back(f.first); next->paths.push_back(f.second); }
    };
    std::vector<std::string> dirs;
    if (DIR *d = ::opendir(kDemoDir)) {
        while (dirent *e = ::readdir(d)) {
            if (e->d_name[0] == '.') continue;
            struct stat st;
            if (::stat((std::string(kDemoDir) + "/" + e->d_name).c_str(), &st) == 0 && S_ISDIR(st.st_mode)) dirs.push_back(e->d_name);
        }
        ::closedir(d);
    }
    std::sort(dirs.begin(), dirs.end(), [](const std::string &a, const std::string &b) { return strcasecmp(a.c_str(), b.c_str()) < 0; });
    for (const auto &b : dirs) add_pack(b, std::string(kDemoDir) + "/" + b);
    add_pack("Demos", kDemoDir);
    {
        std::vector<std::vector<std::string>> names(next->packs.size());
        for (size_t k = 0; k < next->packs.size(); k++)
            for (int i = next->pack_first[k]; i < next->pack_first[k] + next->pack_n[k]; i++) names[k].push_back(next->names[(size_t) i]);
        next->digest = tb::presetDigest(next->packs, names);
    }
    int p = inst->demo_pack.load();
    if (p < 0 || p >= (int) next->packs.size()) p = 0;
    inst->demo_pack = p;
    if (!next->packs.empty() && inst->demo_part.load() >= next->pack_n[(size_t) p]) inst->demo_part = 0;
    std::atomic_store(&inst->demos, std::shared_ptr<const tablor_instance::DemoList>(next));
    apply_want_demo(inst);                                      /* a restored state may be waiting for this list (the caller republishes) */
    load_demo(inst);
}

/* Which preset the page shows is not part of the sound, so a state restored by the host (a reboot, a reloaded slot) carried the sound
 * back and left the page on the first preset. The state now names its preset as "pk=<bank>/<name>"; ';' is the state's separator. */
static std::string preset_key(const tablor_instance::preset &p)
{
    std::string k = p.bank + "/" + p.name;
    std::replace(k.begin(), k.end(), ';', ',');
    return k;
}

/* Point the preset page at the preset with this key. False when the store has no such preset (not scanned yet, or since deleted). */
static bool select_preset_by_key(tablor_instance *inst, const std::string &key)
{
    auto ps = inst->storeList();
    for (size_t b = 0; b < ps->banks.size(); b++)
        for (int i = ps->bank_first[b]; i < ps->bank_first[b] + ps->bank_n[b]; i++)
            if (preset_key(ps->items[(size_t) i]) == key) {
                inst->bank_index = (int) b;
                inst->store_index = i;
                return true;
            }
    return false;
}

static void select_preset_by_path(tablor_instance *inst, const std::string &path)
{
    auto ps = inst->presetList();
    for (size_t i = 0; i < ps->items.size(); i++)
        if (ps->items[i].path == path) {
            inst->preset_index = (int) i;
            return;
        }
}

/* WORKER THREAD ONLY. */
static void load_presets(tablor_instance *inst)
{
    auto boot = std::make_shared<tablor_instance::PresetList>();
    char path[600];
    snprintf(path, sizeof path, "%s/presets/factory.tbl", inst->module_dir);
    FILE *f = fopen(path, "r");
    if (f) {
        char line[4096];
        while (fgets(line, sizeof line, f)) {
            if (line[0] == '#' || line[0] == '\n') continue;
            char *bar = strchr(line, '|');
            if (!bar) continue;
            *bar = 0;
            char *blob = bar + 1;
            blob[strcspn(blob, "\r\n")] = 0;
            boot->items.push_back({ line, blob, "" });
        }
        fclose(f);
    }
    boot->factory_count = (int) boot->items.size();
    std::atomic_store(&inst->presets, std::shared_ptr<const tablor_instance::PresetList>(boot));

    ::mkdir(kPresetDir, 0755);
    migrate_old_user_slots(inst);
    scan_user_presets(inst);
}

static int param_index(const char *key)
{
    /* A binary search, not a walk of 521 names: a preset change looks up every key its state names, on the audio callback. The order is built once
     * into a plain array (no object with a destructor: a static map's destructor ran from exit() after the module had been unloaded and crashed the
     * process). Equal keys sort in index order, so the first of two wins, as the walk did. */
    static int order[TB_PARAM_COUNT];
    static std::once_flag once;
    std::call_once(once, [] {
        for (int i = 0; i < TB_PARAM_COUNT; i++) order[i] = i;
        std::stable_sort(order, order + TB_PARAM_COUNT, [](int a, int b) { return strcmp(tb_params[a].key, tb_params[b].key) < 0; });
    });
    int lo = 0, hi = TB_PARAM_COUNT;
    while (lo < hi) {
        const int mid = (lo + hi) / 2;
        if (strcmp(tb_params[order[mid]].key, key) < 0) lo = mid + 1; else hi = mid;
    }
    return lo < TB_PARAM_COUNT && !strcmp(tb_params[order[lo]].key, key) ? order[lo] : -1;
}

/* Which wavetable slot a param drives: wt1_table -> 0 ... wt3_table -> 2. */
static int tb_path_slot(int param_idx)
{
    return param_idx == TB_P_WT1_TABLE ? 0 : param_idx == TB_P_WT2_TABLE ? 1 : param_idx == TB_P_SMP_SET ? TB_WT_SLOTS : param_idx == TB_P_SMP2_SET ? TB_WT_SLOTS + 1 : param_idx == TB_P_SMP3_SET ? TB_WT_SLOTS + 2 : param_idx == TB_P_CONV_SET ? TB_WT_SLOTS + 3 : 2;
}

/* The same for the wtN_select / wtN_shape keys, which are not params. */
static int tb_key_slot(const char *key)
{
    return key[2] == '3' ? 2 : key[2] == '2' ? 1 : 0;
}

static bool tb_is_wt_key(const char *key, const char *suffix)
{
    return key[0] == 'w' && key[1] == 't' && key[2] >= '1' && key[2] <= '3' &&
           !strcmp(key + 3, suffix);
}

/* Store a path and post the background load. "" or "Init" = built-in. */
/* The scanner list IS the enum: index 0 = "Init" (built-in), then every
 * file found. Keep the param's index and the loaded path in step. */
static void sync_table_index(tablor_instance *inst, int osc)
{
    auto snap = inst->selection();
    const auto &l = snap->paths;
    int idx = 0;
    for (size_t i = 0; i < l.size(); i++)
        if (l[i] == inst->wt_path[osc]) { idx = (int) i; break; }
    static const int kTableParam[TB_WT_SLOTS] = {
        TB_P_WT1_TABLE, TB_P_WT2_TABLE, TB_P_WT3_TABLE };
    inst->params()[kTableParam[osc]] = (float) idx;
}

/* Free on the worker what a preset change took out of the engine: the last reference to a big table or sample set must not drop on the audio callback. */
template <class T> static void retire(tablor_instance *inst, std::shared_ptr<T> old)
{
    if (old && old.use_count() == 1) inst->loader.post([o = std::move(old)]() mutable { o.reset(); });
}

static void set_table_path(tablor_instance *inst, int osc, const char *path, bool mute = false)
{
    if (!strcmp(path, "Init")) path = "";
    snprintf(inst->wt_path[osc], sizeof inst->wt_path[osc], "%s", path);

    /* A PRESET load (mute) silences the slot until its new table has loaded. Left alone it kept the previous preset's table under the new
     * preset's levels, so a note struck just after a preset change could come out loud and wrong. Switching a table by hand mid-note keeps
     * the old one sounding until the new one is ready (the sound stays continuous), so that path does not mute. */
    if (mute) {
        if (osc >= TB_WT_SLOTS && osc <= TB_WT_SLOTS + 2) retire(inst, inst->engine.takeSample(osc - TB_WT_SLOTS));
        else if (osc == TB_WT_SLOTS + 3) retire(inst, inst->engine.takeConv());
        else if (osc >= 0 && osc < TB_WT_SLOTS) retire(inst, inst->engine.takeTable(osc));
    }

    if (osc >= TB_WT_SLOTS) {               /* a sample set (the first, or the second): read on the worker, then swapped in like a wavetable */
        const std::string p = path;
        const int which = osc - TB_WT_SLOTS;
        inst->loader.postKeyed(100 + which, [inst, p, which] {      /* latest wins per slot: the sets of presets passed in a spin are not read */
            if (which == 3) {                                            /* the convolution reverb's IR: read, then transformed into a plan here, off the audio thread */
                if (p.empty()) { inst->engine.setConv(nullptr); return; }
                tb::SampleSet ir;
                inst->engine.setConv(tb::sampleLoad(p.c_str(), ir) ? tb::convBuild(ir, 44100.0f) : nullptr);
                return;
            }
            auto put = [&](std::shared_ptr<tb::SampleSet> s) { if (which == 2) inst->engine.setSample3(std::move(s)); else if (which == 1) inst->engine.setSample2(std::move(s)); else inst->engine.setSample(std::move(s)); };
            if (p.empty()) { put(nullptr); return; }
            auto set = std::make_shared<tb::SampleSet>();
            if (tb::sampleLoad(p.c_str(), *set)) put(set);
            else put(nullptr);
        });
        return;
    }

    tb::WtEntry e;
    e.path = path;
    if (const char *dot = strrchr(path, '.'); dot && !strncasecmp(dot, ".wt", 3)) {
        int fs = atoi(dot + 3);
        /* 32, not 256: the same floor wtBuild accepts. A .wt64 was rejected
         * here, fell through to the WAV reader, failed to parse as WAV and
         * loaded nothing at all. An unusable number leaves the size at 0 and
         * the loader infers it -- the file is still FLAC either way. */
        e.flac = true;
        if (fs >= 32 && fs <= 4096 && (fs & (fs - 1)) == 0)
            e.flacFrameSize = fs;
    }
    inst->loader.requestLoad(osc, e);
    sync_table_index(inst, osc);
}

/* ------------------------------------------------------------------ */
/* Pack list + per-pack enum options                                    */
/*                                                                      */
/* WtEntry.name is already "Pack/Name" or "Name", so the pack list costs */
/* no extra filesystem work -- it is a pass over a list we already hold. */
/* ------------------------------------------------------------------ */

static std::string tb_pack_of(const std::string &name)
{
    size_t slash = name.find('/');
    return slash == std::string::npos ? std::string() : name.substr(0, slash);
}

static std::string tb_leaf_of(const std::string &name)
{
    size_t slash = name.rfind('/');
    return slash == std::string::npos ? name : name.substr(slash + 1);
}

/* JSON-escape into a std::string. The option names are filenames, so a
 * quote or a backslash is unlikely but entirely legal on disk -- and one
 * unescaped byte turns the whole contract into a failed read. */
static void tb_json_append(std::string &out, const std::string &in)
{
    for (char c : in) {
        if (c == '"' || c == '\\') { out += '\\'; out += c; }
        else if ((unsigned char) c < 0x20) continue;
        else out += c;
    }
}

/* Everything below runs ON THE WORKER: it walks the scan, copies it and
 * formats ~24 KB of JSON. The audio thread only ever reads the finished
 * snapshot through inst->selection(). */

/* The host's chain-host parser caps an enum at MAX_ENUM_OPTIONS (128).
 * Past that the knob grid still lists them (the JS parses the JSON
 * itself) but CC knob mapping and modulation targets truncate, which
 * fails silently -- so stop at the cap rather than publish a list only
 * half of the host can see. */
static const size_t TB_MAX_ENUM_OPTIONS = 128;

/* Rebuild the whole selection contract off the current scan and pack.
 * Worker thread only. Publishes atomically when done. */
static void tb_publish_selection(tablor_instance *inst)
{
    auto snap = std::make_shared<tablor_instance::SelSnapshot>();
    const auto &l = inst->scanner.list();

    /* the scan, copied — so the audio thread never reads the scanner while
     * a rescan is mutating it */
    snap->paths.reserve(l.size());
    for (const auto &e : l)
        snap->paths.push_back(e.path);

    /* packs: the distinct "Pack/" prefixes, All first */
    snap->packs.push_back("All");
    for (const auto &e : l) {
        std::string pk = tb_pack_of(e.name);
        if (pk.empty()) continue;
        bool seen = false;
        for (size_t i = 1; i < snap->packs.size(); i++)
            if (snap->packs[i] == pk) { seen = true; break; }
        if (!seen) snap->packs.push_back(pk);
    }

    int pack = inst->wt_pack.load(std::memory_order_relaxed);
    if (pack < 0 || pack >= (int) snap->packs.size()) {
        pack = 0;
        inst->wt_pack.store(0, std::memory_order_relaxed);
    }

    /* the entries this pack offers */
    const bool all = (pack <= 0);
    const std::string want = all ? std::string() : snap->packs[(size_t) pack];
    for (size_t i = 0; i < l.size(); i++) {
        if (i == 0) { snap->filtered.push_back(0); continue; }   /* Init */
        if (all || tb_pack_of(l[i].name) == want)
            snap->filtered.push_back((int) i);
        if (snap->filtered.size() >= TB_MAX_ENUM_OPTIONS) break;
    }

    /* wt_pack_list: the items-level rows */
    std::string &pl = snap->pack_list;
    pl = "[";
    for (size_t i = 0; i < snap->packs.size(); i++) {
        char head[32];
        snprintf(head, sizeof head, "%s{\"index\":%d,\"label\":\"",
                 i ? "," : "", (int) i);
        pl += head;
        tb_json_append(pl, snap->packs[i]);
        pl += "\"}";
    }
    pl += "]";

    /* wt_paths: the FILE for each wtN_select index. A browser cannot read a
     * value the device did not push, and nothing pushes when a pot changes a
     * table -- but the select INDEX does push, so with the paths in hand the
     * page can fetch and draw the file itself instead of waiting on us. */
    std::string &pl2 = snap->path_list;
    pl2 = "[";
    for (size_t i = 0; i < snap->filtered.size(); i++) {
        int g = snap->filtered[i];
        if (i) pl2 += ",";
        pl2 += "\"";
        if (g > 0 && g < (int) l.size()) tb_json_append(pl2, l[(size_t) g].path);
        pl2 += "\"";
    }
    pl2 += "]";

    /* chain_params: the static head plus the three dynamic entries */
    std::string &out = snap->chain_params;
    out.assign(tb_chain_params_json);
    if (out.size() < 2 || out.back() != ']')
        return;                              /* not the shape we expect */
    out.pop_back();                          /* drop the closing ] */

    /* The keys the Move turns (the wavetable selects, bank, the demo switch, pack and part, preset) go in `hand` and are inserted right after the
     * page keys at the end, not appended: the host caches only the first 256 declared keys, and a set of one past that makes it re-fetch and re-parse
     * this whole list on the SPI callback (23 ms, four times a second). See tools/gen_params.py. */
    std::string hand;

    out += ",{\"key\":\"wt_pack\",\"name\":\"WT Pack\",\"type\":\"enum\",\"options\":[";
    for (size_t i = 0; i < snap->packs.size(); i++) {
        if (i) out += ",";
        out += "\""; tb_json_append(out, snap->packs[i]); out += "\"";
    }
    out += "]}";

    for (int osc = 0; osc < TB_WT_SLOTS; osc++) {
        char head[96];
        snprintf(head, sizeof head,
                 ",{\"key\":\"wt%d_select\",\"name\":\"WT%d Table\",\"type\":\"enum\",\"options\":[",
                 osc + 1, osc + 1);
        hand += head;
        for (size_t i = 0; i < snap->filtered.size(); i++) {
            int g = snap->filtered[i];
            if (i) hand += ",";
            hand += "\"";
            tb_json_append(hand, g == 0 ? std::string("Init")
                                        : tb_leaf_of(l[(size_t) g].name));
            hand += "\"";
        }
        hand += "]}";
    }

    /* The Bank knob: one option per preset folder in the store (scanned before the second publish). */
    {
        auto ps = inst->storeList();
        hand += ",{\"key\":\"bank\",\"name\":\"Bank\",\"type\":\"enum\",\"options\":[";
        if (ps->banks.empty()) hand += "\"User\"";
        for (size_t i = 0; i < ps->banks.size(); i++) {
            if (i) hand += ",";
            hand += "\""; tb_json_append(hand, ps->banks[i]); hand += "\"";
        }
        hand += "]}";
    }

    /* The demo mode: the switch, the pack and the part inside it (enums whose options are what the scan found), and the list and the current pick for the web panel. */
    {
        auto dl = inst->demoList();
        hand += ",{\"key\":\"demo_on\",\"name\":\"Demo\",\"type\":\"enum\",\"options\":[\"Off\",\"On\"],\"default\":\"Off\"}";
        hand += ",{\"key\":\"demo_pack\",\"name\":\"Demo Pack\",\"type\":\"enum\",\"options\":[";
        if (dl->packs.empty()) hand += "\"None\"";
        for (size_t i = 0; i < dl->packs.size(); i++) {
            if (i) hand += ",";
            hand += "\""; tb_json_append(hand, dl->packs[i]); hand += "\"";
        }
        hand += "]},{\"key\":\"demo_part\",\"name\":\"Demo Part\",\"type\":\"enum\",\"options\":[";
        const int dp = inst->demo_pack.load();
        const int dn = dp >= 0 && dp < (int) dl->packs.size() ? dl->pack_n[(size_t) dp] : 0;
        if (dn <= 0) hand += "\"None\"";
        for (int i = 0; i < dn; i++) {
            if (i) hand += ",";
            hand += "\""; tb_json_append(hand, dl->names[(size_t) (dl->pack_first[(size_t) dp] + i)]); hand += "\"";
        }
        hand += "]}";
        out += ",{\"key\":\"demo_list\",\"name\":\"Demo List\",\"type\":\"string\",\"access\":\"read\"}"
               ",{\"key\":\"demo_now\",\"name\":\"Demo Now\",\"type\":\"string\",\"access\":\"read\"}"
               ",{\"key\":\"macro_names\",\"name\":\"Macro Names\",\"type\":\"string\",\"access\":\"read\"}"
               ",{\"key\":\"macro_routes\",\"name\":\"Macro Routes\",\"type\":\"string\",\"access\":\"read\"}";
    }

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
    out += ",{\"key\":\"wt1_shape\",\"name\":\"WT1 Shape\",\"type\":\"string\",\"access\":\"read\"}"
           ",{\"key\":\"wt2_shape\",\"name\":\"WT2 Shape\",\"type\":\"string\",\"access\":\"read\"}"
           ",{\"key\":\"wt3_shape\",\"name\":\"WT3 Shape\",\"type\":\"string\",\"access\":\"read\"}"
           ",{\"key\":\"wt_paths\",\"name\":\"WT Paths\",\"type\":\"string\",\"access\":\"read\"}";
    /* The web panel's preset browser: the store's banks and preset names (read-only, built when the store is scanned), the
     * current bank and index, and the index to load. `bank` is declared above; `preset` is the index within the current bank. */
    /* ORDER MATTERS. The manager's initial value fetch sends in batches of 8 and flushes the last partial batch only when the LAST declared key
     * reads back fine: if it does not, the tail of the list is dropped. So the list ends on a key that always reads (preset_now); `preset` moved up with the other keys the Move turns (see `hand`). */
    out += ",{\"key\":\"rescan\",\"name\":\"Rescan\",\"type\":\"int\",\"min\":0,\"max\":1,\"default\":0}"
           ",{\"key\":\"macro_edit\",\"name\":\"Macro Edit\",\"type\":\"string\"}"
           ",{\"key\":\"preset_list\",\"name\":\"Preset List\",\"type\":\"string\",\"access\":\"read\"}"
           ",{\"key\":\"preset_now\",\"name\":\"Preset Now\",\"type\":\"string\",\"access\":\"read\"}";
    hand += ",{\"key\":\"preset\",\"name\":\"Preset\",\"type\":\"int\",\"min\":0,\"max\":4095,\"default\":0}";
    out.insert(tb_chain_params_prio_len, hand);      /* right after the page keys: inside the host's 256-key cache */
    out += "]";

    /* The macro knobs carry the preset's macro names as their `unit`: the cell label stays "MCR 1", and touching a knob shows the
     * value with the name beside it ("64 More Delay") in the top bar. A macro the preset does not use gets no unit. The key's entry
     * is found by its key; the unit goes in right after the key (so it does not depend on the knob having a custom widget). */
    {
        const std::string names = *std::atomic_load(&inst->macn_pub);
        size_t pos = 0;
        for (int m = 0; m < 8; m++) {
            size_t end = names.find('|', pos);
            std::string nm = names.substr(pos, end == std::string::npos ? std::string::npos : end - pos);
            pos = end == std::string::npos ? names.size() : end + 1;
            while (!nm.empty() && nm.back() == ' ') nm.pop_back();
            while (!nm.empty() && nm.front() == ' ') nm.erase(nm.begin());
            if (nm.empty()) continue;
            if (nm.size() > 20) nm.resize(20);
            const std::string key = "\"key\":\"mac" + std::to_string(m + 1) + "\"";
            const size_t k = out.find(key);
            if (k == std::string::npos) continue;
            std::string unit = ",\"unit\":\"";
            tb_json_append(unit, nm);
            unit += "\"";
            out.insert(k + key.size(), unit);
        }
    }

    std::atomic_store(&inst->sel,
                      std::shared_ptr<const tablor_instance::SelSnapshot>(snap));
}

/* Where the currently loaded table sits in the filtered list, or 0. */
static int tb_select_index(const tablor_instance::SelSnapshot &snap,
                           const char *path)
{
    for (size_t i = 0; i < snap.filtered.size(); i++) {
        size_t g = (size_t) snap.filtered[i];
        if (g < snap.paths.size() && snap.paths[g] == path)
            return (int) i;
    }
    return 0;
}

/* ---- drawable digest of a loaded wavetable ------------------------------
 * "<frames>,<samples>,<data>" with one character per sample: the amplitude
 * quantised to 64 levels over [-1,1] in a base64-ish alphabet. 24 frames of
 * 64 samples is ~1.5 KB -- enough to draw a waterfall, small enough to sit
 * in a param string, and cheap enough to build on the worker while the table
 * is already in cache. WORKER THREAD ONLY. */
static const char kQ[] =
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-_";

static std::string tb_wt_digest(const tb::Wavetable &wt)
{
    const int nf = wt.size();
    if (nf < 1) return std::string();
    const int OF = nf < 24 ? nf : 24;      /* frames we draw   */
    const int OS = 64;                     /* samples per frame */

    std::string out;
    char head[32];
    snprintf(head, sizeof head, "%d,%d,", OF, OS);
    out.reserve((size_t) OF * OS + 16);
    out.assign(head);

    for (int f = 0; f < OF; f++) {
        int src = (OF == 1) ? 0 : (int) ((long) f * (nf - 1) / (OF - 1));
        const tb::FrameTable *ft = wt.frame(src);
        if (!ft || ft->levels.empty()) { out.append(OS, kQ[32]); continue; }
        const tb::MipLevel &lv = ft->levels[0];   /* full band level */
        if (lv.size < 1 || lv.data.empty()) { out.append(OS, kQ[32]); continue; }
        for (int i = 0; i < OS; i++) {
            int j = (int) ((long) i * lv.size / OS);
            if (j >= (int) lv.data.size()) j = (int) lv.data.size() - 1;
            float v = lv.data[(size_t) j];
            if (v < -1.0f) v = -1.0f;
            if (v >  1.0f) v =  1.0f;
            int q = (int) ((v + 1.0f) * 0.5f * 63.0f + 0.5f);
            if (q < 0) q = 0;
            if (q > 63) q = 63;
            out += kQ[q];
        }
    }
    return out;
}

static void tb_publish_shape(tablor_instance *inst, int osc, const tb::Wavetable &wt)
{
    auto s = std::make_shared<const std::string>(tb_wt_digest(wt));
    std::atomic_store(&inst->wt_shape[osc], s);
}

/* Select by index into the published snapshot. */
static void set_table_index(tablor_instance *inst, int osc, int idx)
{
    auto snap = inst->selection();
    if (idx < 0 || idx >= (int) snap->paths.size()) return;
    set_table_path(inst, osc, snap->paths[(size_t) idx].c_str());
}

static float clamp_param(const tb_param_t *p, float v)
{
    if (v < p->min) v = p->min;
    if (v > p->max) v = p->max;
    return v;
}

/* The macro routes the state carries ("macro:key:travel,...") into the engine. */
static void apply_macro_routes(tablor_instance *inst, const char *text)
{
    tb::MacroRouteSpec r[tb::Engine::kMaxMacroRoutes];
    inst->engine.setMacroRoutes(r, tb::parseMacroRoutes(text, r, tb::Engine::kMaxMacroRoutes));
}

/* Serialize the current sound into inst->state_buf (TBLR1 blob). */
static void build_state_blob(tablor_instance *inst)
{
    /* TBLR2, not TBLR1: v1.0.5 reordered the LFO shape options (the host
     * draws the glyph from the index), so the same lfoN_shape number means a
     * different waveform on either side of that release. The tag is how a
     * loader can tell which order a blob was saved under. */
    int o = snprintf(inst->state_buf, sizeof inst->state_buf, "TBLR2;");
    {
        auto ps = inst->storeList();
        const int si = inst->store_index.load();
        if (si >= 0 && si < (int) ps->items.size())
            o += snprintf(inst->state_buf + o, sizeof inst->state_buf - (size_t) o, "pk=%s;", preset_key(ps->items[(size_t) si]).c_str());
    }
    if (inst->clip_str[0])
        o += snprintf(inst->state_buf + o, sizeof inst->state_buf - (size_t) o, "clip=%s;", inst->clip_str);
    if (inst->arp_str[0])
        o += snprintf(inst->state_buf + o, sizeof inst->state_buf - (size_t) o, "arp=%s;", inst->arp_str);
    if (inst->mac_str[0])
        o += snprintf(inst->state_buf + o, sizeof inst->state_buf - (size_t) o, "mac=%s;", inst->mac_str);
    if (inst->macn_str[0])
        o += snprintf(inst->state_buf + o, sizeof inst->state_buf - (size_t) o, "macn=%s;", inst->macn_str);
    {   /* the slot's demo: the song's pack and part by name, and whether it is on (a slot that never used the demo says nothing) */
        auto dl = inst->demoList();
        const int dp = inst->demo_pack.load(), dpt = inst->demo_part.load();
        if (dp >= 0 && dp < (int) dl->packs.size() && dpt >= 0 && dpt < dl->pack_n[(size_t) dp] && (inst->demo_on.load() || dp > 0 || dpt > 0))
            o += snprintf(inst->state_buf + o, sizeof inst->state_buf - (size_t) o, "dmpk=%s;dmpt=%s;dmon=%d;",
                          state_safe(dl->packs[(size_t) dp]).c_str(),
                          state_safe(dl->names[(size_t) (dl->pack_first[(size_t) dp] + dpt)]).c_str(), inst->demo_on.load() ? 1 : 0);
    }
    for (int i = 0; i < TB_PARAM_COUNT; i++) {
        if (tb_params[i].type == TB_PATH) {   /* wavetable: store the path */
            const char *path = inst->wt_path[tb_path_slot(i)];
            if (path[0])
                o += snprintf(inst->state_buf + o,
                              sizeof inst->state_buf - (size_t) o,
                              "%s=%s;", tb_params[i].key, path);
            continue;
        }
        float v = inst->params()[i];
        if (v == tb_params[i].def) continue;            /* defaults are implicit */
        o += snprintf(inst->state_buf + o, sizeof inst->state_buf - (size_t) o,
                      "%s=%g;", tb_params[i].key, (double) v);
        if (o >= (int) sizeof inst->state_buf - 64) break;
    }
    /* the FX buses' racks: the same keys behind "b1." and "b2." */
    for (int b = 0; b < 2; b++)
        for (int i = 0; i < TB_PARAM_COUNT; i++) {
            if (tb_params[i].type == TB_PATH) continue;
            const float v = inst->engine.busPots[b][i];
            if (v == tb_params[i].def || o >= (int) sizeof inst->state_buf - 64) continue;
            o += snprintf(inst->state_buf + o, sizeof inst->state_buf - (size_t) o, "b%d.%s=%g;", b + 1, tb_params[i].key, (double) v);
        }
}

/* The macro names changed (a preset loaded, or the sound reset): publish chain_params again so the knobs' units follow. On the worker; the host
 * re-reads the contract a moment after a preset change and is_loading holds it off until this is done. */
static void republish_macro_names(tablor_instance *inst)
{
    if (*std::atomic_load(&inst->macn_pub) == inst->macn_str) return;
    std::atomic_store(&inst->macn_pub, std::make_shared<const std::string>(inst->macn_str));
    inst->pub_pending.fetch_add(1);
    if (inst->loader.postKeyed(200, [inst] {                  /* one republish serves every preset passed in a spin: it reads the state when it runs */
        if (!inst->loader.stopping()) tb_publish_selection(inst);
        inst->pub_pending.fetch_sub(1);
    })) inst->pub_pending.fetch_sub(1);                        /* it dropped an older one that would have taken its count back */
}

static void reset_to_defaults(tablor_instance *inst)
{
    inst->clip_str[0] = 0;
    inst->engine.setClip(nullptr);
    inst->arp_str[0] = 0;
    inst->engine.setArp(nullptr);
    inst->mac_str[0] = inst->macn_str[0] = 0;
    inst->engine.setMacroRoutes(nullptr, 0);
    for (int i = 0; i < TB_PARAM_COUNT; i++)
        if (tb_params[i].type != TB_PATH)
            inst->params()[i] = tb_params[i].def;
    inst->engine.macroSync();
    set_table_path(inst, 0, "");
    set_table_path(inst, 1, "");
    set_table_path(inst, 2, "");
    set_table_path(inst, TB_WT_SLOTS, "");
    set_table_path(inst, TB_WT_SLOTS + 1, "");
    set_table_path(inst, TB_WT_SLOTS + 2, "");
    set_table_path(inst, TB_WT_SLOTS + 3, "");
    republish_macro_names(inst);
}

/* Version-tagged blob: "TBLR1;…" or "TBLR2;…". Anything without a known
 * tag is from another life — ignore it entirely rather than half-apply
 * it (the ER-99 total-silence lesson). */
static void apply_state_blob(tablor_instance *inst, const char *val, bool host_restore = false)
{
    bool v1 = strncmp(val, "TBLR1;", 6) == 0;
    if (!v1 && strncmp(val, "TBLR2;", 6) != 0) return;
    inst->state_seen = true;
    /* A sound taken from the store is a whole sound: a setting it does not name is at its default, not where the last preset left it (the converter
     * writes only the settings that differ from the default, so browsing from a granular preset carried its grain settings into every sample preset
     * after it). The host's own restore of a slot keeps its meaning (a state that names little sets little). Unison Cap is the player's live limit. */
    const int capIdx = param_index("uni_cap");
    bool pathSeen[TB_WT_SLOTS + 4] = {};
    if (!host_restore)
        for (int i = 0; i < TB_PARAM_COUNT; i++)
            if (tb_params[i].type != TB_PATH && i != capIdx) inst->params()[i] = tb_params[i].def;
    for (int b = 0; b < 2; b++)                       /* a preset says what its buses hold: a bus it does not mention is empty */
        for (int i = 0; i < TB_PARAM_COUNT; i++)
            inst->engine.busPots[b][i] = tb_params[i].def;
    const char *p = val + 6;
    inst->clip_str[0] = 0;                            /* a preset without a pattern has none */
    inst->engine.setClip(nullptr);
    inst->arp_str[0] = 0;
    inst->engine.setArp(nullptr);
    inst->mac_str[0] = inst->macn_str[0] = 0;         /* nor macros: a preset says where its macros sit and what they move */
    inst->engine.setMacroRoutes(nullptr, 0);
    for (int i = 0; i < 8; i++) inst->params()[TB_P_MAC1 + i] = 0.0f;
    char kbuf[64], vbuf[4096];
    std::string dm_pack, dm_part;                     /* the demo the state names (only a host restore applies it: a preset taken from the store keeps the slot's demo) */
    bool dm_on = false, dm_seen = false;
    while (*p) {
        const char *eq = strchr(p, '=');
        if (!eq) break;
        const char *semi = strchr(eq, ';');
        if (!semi) semi = eq + strlen(eq);
        size_t kl = (size_t) (eq - p), vl = (size_t) (semi - eq - 1);
        if (kl < sizeof kbuf && vl < sizeof vbuf) {
            memcpy(kbuf, p, kl); kbuf[kl] = 0;
            memcpy(vbuf, eq + 1, vl); vbuf[vl] = 0;
            /* Only the host's own restore says which preset the page is on. A preset saved from a slot carries the key of the sound it
             * was made from, and loading it from the store must not send the page back there. Held first and then tried, so that
             * whichever of this and the worker's scan comes second finds the other's half. */
            if (host_restore && !strcmp(kbuf, "pk")) {
                std::atomic_store(&inst->want_pk, std::make_shared<const std::string>(vbuf));
                select_preset_by_key(inst, vbuf);
            }
            if (!strcmp(kbuf, "arp")) {
                snprintf(inst->arp_str, sizeof inst->arp_str, "%s", vbuf);
                inst->engine.setArp(tb::parseArp(vbuf));
                p = (*semi) ? semi + 1 : semi;
                continue;
            }
            if (!strcmp(kbuf, "clip")) {
                snprintf(inst->clip_str, sizeof inst->clip_str, "%s", vbuf);
                inst->engine.setClip(tb::parseClip(vbuf));
                p = (*semi) ? semi + 1 : semi;
                continue;
            }
            if (!strcmp(kbuf, "mac")) {
                snprintf(inst->mac_str, sizeof inst->mac_str, "%s", vbuf);
                apply_macro_routes(inst, vbuf);
                p = (*semi) ? semi + 1 : semi;
                continue;
            }
            if (!strcmp(kbuf, "macn")) {
                snprintf(inst->macn_str, sizeof inst->macn_str, "%s", vbuf);
                p = (*semi) ? semi + 1 : semi;
                continue;
            }
            if (!strcmp(kbuf, "dmpk") || !strcmp(kbuf, "dmpt") || !strcmp(kbuf, "dmon")) {
                if (!strcmp(kbuf, "dmpk")) { dm_pack = vbuf; dm_seen = true; }
                else if (!strcmp(kbuf, "dmpt")) dm_part = vbuf;
                else dm_on = atoi(vbuf) != 0;
                p = (*semi) ? semi + 1 : semi;
                continue;
            }
            /* Unison Cap is the player's live limit on this slot, not part of a sound: a preset taken from the store leaves it where it is
             * (only the host's own restore of the slot's saved state brings it back). */
            if (!host_restore && !strcmp(kbuf, "uni_cap")) { p = (*semi) ? semi + 1 : semi; continue; }
            const bool onBus = kbuf[0] == 'b' && (kbuf[1] == '1' || kbuf[1] == '2') && kbuf[2] == '.';
            int idx = param_index(onBus ? kbuf + 3 : kbuf);
            if (idx >= 0 && onBus) {
                if (tb_params[idx].type != TB_PATH)
                    inst->engine.busPots[kbuf[1] - '1'][idx] = clamp_param(&tb_params[idx], strtof(vbuf, nullptr));
            } else if (idx >= 0) {
                if (tb_params[idx].type == TB_PATH) {
                    pathSeen[tb_path_slot(idx)] = true;
                    set_table_path(inst, tb_path_slot(idx), vbuf, true);
                }
                else
                    inst->params()[idx] = clamp_param(&tb_params[idx],
                                                      strtof(vbuf, nullptr));
            }
        }
        p = (*semi) ? semi + 1 : semi;
    }
    if (!host_restore)                                /* a table or set the preset does not name is none */
        for (int s = 0; s < TB_WT_SLOTS + 4; s++)
            if (!pathSeen[s] && inst->wt_path[s][0]) set_table_path(inst, s, "", true);
    inst->engine.macroSync();                         /* the macros sit where the state says; their routes move nothing until one is turned */
    republish_macro_names(inst);
    if (host_restore && dm_seen) {                    /* back onto the song the slot was playing, once the demo store has been scanned */
        auto w = std::make_shared<tablor_instance::WantDemo>();
        w->pack = dm_pack; w->part = dm_part; w->on = dm_on;
        std::atomic_store(&inst->want_demo, std::shared_ptr<const tablor_instance::WantDemo>(w));
        inst->loader.post([inst] { if (inst->loader.stopping()) return; if (apply_want_demo(inst)) tb_publish_selection(inst); });
    }
}

/* The browser chose store item `i`: the name shows at once, the sound follows when the knob has rested (see pend_idx). */
static long long steady_ms()
{
    return (long long) std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
}

static void apply_pending(tablor_instance *inst)
{
    const int pi = inst->pend_idx.exchange(-1);
    if (pi < 0) return;
    auto ps = inst->storeList();
    if (pi < (int) ps->items.size()) apply_state_blob(inst, ps->items[(size_t) pi].blob.c_str());
}

static void choose_store_preset(tablor_instance *inst, int i)
{
    inst->store_index = i;
    if (inst->settle_ms <= 0) {
        inst->pend_idx = -1;
        auto ps = inst->storeList();
        if (i >= 0 && i < (int) ps->items.size()) apply_state_blob(inst, ps->items[(size_t) i].blob.c_str());
        return;
    }
    inst->pend_due = steady_ms() + inst->settle_ms;
    inst->pend_idx = i;
}

/* ------------------------------------------------------------------ */
/* User macros: u{i} pots write through to their selected target;      */
/* picking a new target back-syncs the pot so nothing jumps.           */
/* ------------------------------------------------------------------ */

/* ------------------------------------------------------------------ */
/* set / get                                                           */
/* ------------------------------------------------------------------ */


/* The remote UI's macro editing (right-click a knob): "add|macro|key|travel|name", "del|macro|key", "travel|macro|key|travel", "name|macro|text", "clear|macro", macro 1..8, travel in the
 * key's own steps over the macro's whole range. It edits the same two strings a preset's state carries (mac: "macro:key:travel,...", macn: eight names between bars), so the routes
 * are live at once, are saved with the slot, and the Move shows the names. Only plain-number parameters can be routed. */
static std::string macro_clean(const std::string &s)
{
    std::string o;
    for (char c : s) o += (c == '|' || c == ',' || c == ':' || c == ';' || c == '=' || (unsigned char) c < 32) ? ' ' : c;
    while (!o.empty() && o.back() == ' ') o.pop_back();
    while (!o.empty() && o.front() == ' ') o.erase(o.begin());
    return o.substr(0, 24);
}

static void macro_edit(tablor_instance *inst, const char *val)
{
    std::vector<std::string> f;
    for (const char *p = val;;) {
        const char *bar = strchr(p, '|');
        f.emplace_back(p, bar ? (size_t) (bar - p) : strlen(p));
        if (!bar) break;
        p = bar + 1;
    }
    if (f.size() < 2) return;
    const std::string &op = f[0];
    const int m = atoi(f[1].c_str());
    if (m < 1 || m > 8) return;
    struct R { int m; std::string key, travel; };
    std::vector<R> routes;
    for (const char *p = inst->mac_str; *p;) {
        const char *comma = strchr(p, ',');
        const std::string t(p, comma ? (size_t) (comma - p) : strlen(p));
        const size_t a = t.find(':'), b = a == std::string::npos ? a : t.find(':', a + 1);
        if (a != std::string::npos && b != std::string::npos) routes.push_back({ atoi(t.substr(0, a).c_str()), t.substr(a + 1, b - a - 1), t.substr(b + 1) });
        if (!comma) break;
        p = comma + 1;
    }
    std::vector<std::string> names;
    for (const char *p = inst->macn_str;;) {
        const char *bar = strchr(p, '|');
        names.emplace_back(p, bar ? (size_t) (bar - p) : strlen(p));
        if (!bar) break;
        p = bar + 1;
    }
    names.resize(8);
    auto find = [&](const std::string &key) { for (size_t i = 0; i < routes.size(); i++) if (routes[i].m == m && routes[i].key == key) return (int) i; return -1; };
    auto travelStr = [](float t) { char b[32]; snprintf(b, sizeof b, "%g", (double) std::lround(t * 100.0f) / 100.0); return std::string(b); };
    if (op == "add" || op == "travel") {
        if (f.size() < 4) return;
        const int idx = param_index(f[2].c_str());
        if (idx < 0 || tb_params[idx].type != TB_INT) return;
        const float span = tb_params[idx].max - tb_params[idx].min;
        const float t = std::clamp(strtof(f[3].c_str(), nullptr), -span, span);
        const int at = find(f[2]);
        if (at >= 0) routes[(size_t) at].travel = travelStr(t);
        else if (op == "add") routes.push_back({ m, f[2], travelStr(t) });
        if (op == "add" && names[(size_t) m - 1].empty()) names[(size_t) m - 1] = f.size() > 4 && !macro_clean(f[4]).empty() ? macro_clean(f[4]) : "Macro " + std::to_string(m);
    } else if (op == "del") {
        if (f.size() < 3) return;
        const int at = find(f[2]);
        if (at >= 0) routes.erase(routes.begin() + at);
    } else if (op == "clear") {
        routes.erase(std::remove_if(routes.begin(), routes.end(), [&](const R &r) { return r.m == m; }), routes.end());
    } else if (op == "name") {
        if (f.size() < 3) return;
        names[(size_t) m - 1] = macro_clean(f[2]);
    } else return;
    for (int i = 1; i <= 8; i++) {                    /* a macro that moves nothing has no name (the Move hides it) */
        bool any = false;
        for (const R &r : routes) any = any || r.m == i;
        if (!any) names[(size_t) i - 1].clear();
    }
    std::string mac, macn;
    for (const R &r : routes) mac += (mac.empty() ? "" : ",") + std::to_string(r.m) + ":" + r.key + ":" + r.travel;
    bool anyName = false;
    for (int i = 0; i < 8; i++) { macn += (i ? "|" : "") + names[(size_t) i]; anyName = anyName || !names[(size_t) i].empty(); }
    if (!anyName) macn.clear();
    if (mac.size() >= sizeof inst->mac_str || macn.size() >= sizeof inst->macn_str) return;       /* no room: leave it as it was */
    snprintf(inst->mac_str, sizeof inst->mac_str, "%s", mac.c_str());
    snprintf(inst->macn_str, sizeof inst->macn_str, "%s", macn.c_str());
    apply_macro_routes(inst, inst->mac_str);
    republish_macro_names(inst);
}

static void tb_set_param(void *instance, const char *key, const char *val)
{
    auto *inst = (tablor_instance *) instance;
    if (!inst || !key || !val) return;

    /* state restore: host sends "<prefix>:state" resolved to "state" upstream
     * or the raw prefixed key — accept both spellings. */
    const char *k = key;
    const char *colon = strrchr(key, ':');
    if (colon) k = colon + 1;

    if (!strcmp(k, "state")) {
        inst->pend_idx = -1;                            /* a restored state is the sound now: a chosen one still waiting must not land on top of it */
        apply_state_blob(inst, val, true);
        return;
    }
    if (!strcmp(k, "macro_edit")) { macro_edit(inst, val); return; }

    /* The preset page (list_param "preset"): load the sound at that index of the store's list. */
    if (!strcmp(k, "preset")) {
        auto ps = inst->storeList();
        const int b = std::min(std::max(inst->bank_index.load(), 0), (int) ps->banks.size() - 1);
        const int n = b >= 0 ? ps->bank_n[(size_t) b] : 0;        /* the page lists the current bank */
        if (n > 0) {
            const int i = ps->bank_first[(size_t) b] + std::min(std::max(atoi(val), 0), n - 1);
            choose_store_preset(inst, i);
        } else {
            inst->pending_preset = std::max(atoi(val), 0);      /* the list is still being scanned: remember it */
        }
        return;
    }

    /* The remote UI's Import has written presets and wavetables into the store: look again. The scan and the republish run on the worker
     * (the same ones the load does), so nothing here touches a folder; the preset list the page reads is replaced when they finish. */
    if (!strcmp(k, "rescan")) {
        inst->loader.post([inst] {
            if (inst->loader.stopping()) return;
            tb::PartJoin::joinAll("/data/UserData/UserLibrary/Samples");     /* big sample sets the Import sent in pieces */
            inst->scanner.scan();
            tb_publish_selection(inst);
            scan_store_presets(inst);
            scan_demos(inst);
            tb_publish_selection(inst);
        });
        return;
    }

    /* Demo mode: the switch is the engine's; a pack or part change loads the part on the worker (a pack change also republishes the part list). */
    if (!strcmp(k, "demo_on")) {
        const bool on = !strcasecmp(val, "on") || atoi(val) == 1;
        inst->demo_on = on ? 1 : 0;
        inst->engine.setDemoOn(on);                                 /* ready: the part joins at the next bar once the transport runs (it does not start it) */
        return;
    }
    if (!strcmp(k, "demo_pack")) {
        auto dl = inst->demoList();
        if (dl->packs.empty()) return;
        const int n = std::min(std::max(atoi(val), 0), (int) dl->packs.size() - 1);
        inst->demo_pack = n;
        inst->demo_part = 0;
        inst->loader.post([inst] { if (inst->loader.stopping()) return; load_demo(inst); tb_publish_selection(inst); });
        return;
    }
    if (!strcmp(k, "demo_part")) {
        inst->demo_part = std::max(atoi(val), 0);
        inst->loader.post([inst] { if (inst->loader.stopping()) return; load_demo(inst); });
        return;
    }

    /* The bank knob: another bank's list, starting at its first sound (which plays, as a scroll would). */
    if (!strcmp(k, "bank")) {
        auto ps = inst->storeList();
        if (ps->banks.empty()) return;
        const int b = std::min(std::max(atoi(val), 0), (int) ps->banks.size() - 1);
        if (b == inst->bank_index.load()) return;
        inst->bank_index = b;
        if (ps->bank_n[(size_t) b] > 0) {
            choose_store_preset(inst, ps->bank_first[(size_t) b]);
        }
        return;
    }

    /* Turnable wavetable selection. Indices only: get_param reports an
     * index for these, so the host learns WIRE_INDEX and never sends a
     * name. Nothing here touches the filesystem. */
    if (!strcmp(k, "wt_pack")) {
        auto snap = inst->selection();
        int n = atoi(val);
        if (n < 0) n = 0;
        if (n >= (int) snap->packs.size()) n = (int) snap->packs.size() - 1;
        if (n == inst->wt_pack.load(std::memory_order_relaxed)) return;
        inst->wt_pack.store(n, std::memory_order_relaxed);
        /* Republish: wt1_select / wt2_select now have different options.
         * The host re-reads the contract after an items selection settles
         * (page_controller commitItem -> armContractSettle), which is why
         * the pack is offered as an items level and not as a plain enum
         * knob -- a plain enum commit does NOT arm the re-read.
         *
         * The rebuild itself is ~24 KB of formatting: it goes to the worker,
         * because THIS is the SPI callback. The host waits 500 ms and then
         * wants two agreeing reads (CONTRACT_SETTLE_MS), and is_loading below
         * holds it off meanwhile, so the snapshot is always long since
         * published by the time anyone reads it. */
        inst->loader.post([inst] { tb_publish_selection(inst); });
        return;
    }
    if (tb_is_wt_key(k, "_select")) {
        int osc = tb_key_slot(k);
        auto snap = inst->selection();
        int n = atoi(val);
        if (n < 0 || n >= (int) snap->filtered.size()) return;
        set_table_index(inst, osc, snap->filtered[(size_t) n]);
        return;
    }

    int idx = param_index(k);
    if (idx < 0) return;
    const tb_param_t *p = &tb_params[idx];

    if (p->type == TB_PATH) {
        set_table_path(inst, tb_path_slot(idx), val);
        return;
    }

    float v;
    if (p->type == TB_ENUM) {
        /* Accept an option NAME or an integer index — never trust one
         * spelling (the Forge atoi-collapse gotcha). */
        v = -1;
        for (int i = 0; i < p->n_options; i++)
            if (!strcmp(p->options[i], val)) { v = (float) i; break; }
        if (v < 0) {                            /* not a name — try a number */
            char *end = nullptr;
            float n = strtof(val, &end);
            if (end == val) return;             /* unknown name: ignore */
            v = (float) (int) n;
        }
    } else {
        v = strtof(val, nullptr);
    }

    inst->params()[idx] = clamp_param(p, v);
    if (idx >= TB_P_MAC1 && idx <= TB_P_MAC8)
        inst->engine.macroMoved(idx - TB_P_MAC1, inst->params()[idx]);       /* the macro's parameters move by its change */
}

static int write_str(char *buf, int buf_len, const char *s)
{
    int n = (int) strlen(s);
    if (n >= buf_len) n = buf_len - 1;
    memcpy(buf, s, (size_t) n);
    buf[n] = 0;
    return n;
}

static int tb_get_param(void *instance, const char *key, char *buf, int buf_len)
{
    auto *inst = (tablor_instance *) instance;
    if (!inst || !key || !buf || buf_len <= 1) return -1;

    /* Static string: a copy, nothing more. get_param is serviced from the
     * SPI callback (chain_internal.h) — anything that formats or reads the
     * filesystem here jams the param bus. */
    if (!strcmp(key, "chain_params")) {
        auto snap = inst->selection();
        return write_str(buf, buf_len,
                         snap->chain_params.empty() ? tb_chain_params_json
                                                    : snap->chain_params.c_str());
    }

    /* Turnable wavetable selection. All three are answered from memory --
     * no scan, no allocation beyond the small formatting buffer. */
    if (!strcmp(key, "wt_pack")) {
        char tmp[16];
        snprintf(tmp, sizeof tmp, "%d", inst->wt_pack.load(std::memory_order_relaxed));
        return write_str(buf, buf_len, tmp);
    }
    if (!strcmp(key, "wt_paths")) {
        auto snap = inst->selection();
        return write_str(buf, buf_len,
                         snap->path_list.empty() ? "[]" : snap->path_list.c_str());
    }
    if (!strcmp(key, "wt_pack_list")) {
        auto snap = inst->selection();
        return write_str(buf, buf_len,
                         snap->pack_list.empty() ? "[]" : snap->pack_list.c_str());
    }
    if (tb_is_wt_key(key, "_select")) {
        auto snap = inst->selection();
        char tmp[16];
        snprintf(tmp, sizeof tmp, "%d",
                 tb_select_index(*snap, inst->wt_path[tb_key_slot(key)]));
        return write_str(buf, buf_len, tmp);
    }

    /* The table each oscillator has loaded, drawable: "frames,samples,data".
     * Served from a string the worker built when the table landed -- no walk
     * of the wavetable here, this is the SPI callback. */
    if (tb_is_wt_key(key, "_shape")) {
        auto sp = inst->shape(tb_key_slot(key));
        return write_str(buf, buf_len, sp ? sp->c_str() : "");
    }

    /* Triggers read as their idle option, so the host learns the NAME wire
     * and a page shows a quiet "-" rather than junk. */
    /* The host asks this while a contract re-read is pending and holds off
     * for as long as it says "1" (page_controller isLoadingSays). Ours is
     * true while the worker still owes a scan, a table or a republish. */
    if (!strcmp(key, "is_loading"))
        return write_str(buf, buf_len,
                         (!inst->ready.load(std::memory_order_relaxed) ||
                          inst->loader.busy() || inst->pub_pending.load() > 0) ? "1" : "0");

    /* Schwung 0.12+: the stock hierarchy editor (viz graphics, preset
     * browser, file browser, keyboard) IS Tablor's on-device UI. */
    if (!strcmp(key, "ui_hierarchy"))
        return write_str(buf, buf_len, tb_ui_hierarchy_json);

    /* Shadow UI preset browser convention: preset_count / preset /
     * preset_name (the "No presets" screen reads exactly these). They are the .json presets in
     * Schwung's store for this module, in name order. */
    if (!strcmp(key, "bank"))
        return write_str(buf, buf_len, std::to_string(inst->bank_index.load()).c_str());
    if (!strcmp(key, "bank_name")) {
        auto ps = inst->storeList();
        const int b = inst->bank_index.load();
        return write_str(buf, buf_len, b >= 0 && b < (int) ps->banks.size() ? ps->banks[(size_t) b].c_str() : "");
    }
    if (!strcmp(key, "bank_count"))
        return write_str(buf, buf_len, std::to_string(inst->storeList()->banks.size()).c_str());
    if (!strcmp(key, "preset_count")) {
        auto ps = inst->storeList();
        const int b = inst->bank_index.load();
        return write_str(buf, buf_len, std::to_string(b >= 0 && b < (int) ps->banks.size() ? ps->bank_n[(size_t) b] : 0).c_str());
    }
    if (!strcmp(key, "preset")) {
        auto ps = inst->storeList();
        const int b = inst->bank_index.load();
        const int first = b >= 0 && b < (int) ps->banks.size() ? ps->bank_first[(size_t) b] : 0;
        return write_str(buf, buf_len, std::to_string(std::max(inst->store_index.load() - first, 0)).c_str());
    }
    /* The web panel's browser: every bank with its preset names (a string built at scan time), and "bank,index" for the sound
     * the page shows. */
    if (!strcmp(key, "preset_list"))
        return write_str(buf, buf_len, inst->storeList()->digest.c_str());
    if (!strcmp(key, "demo_on"))
        return write_str(buf, buf_len, inst->demo_on.load() ? "On" : "Off");
    if (!strcmp(key, "demo_pack"))
        return write_str(buf, buf_len, std::to_string(inst->demo_pack.load()).c_str());
    if (!strcmp(key, "demo_part"))
        return write_str(buf, buf_len, std::to_string(inst->demo_part.load()).c_str());
    if (!strcmp(key, "demo_list"))
        return write_str(buf, buf_len, inst->demoList()->digest.c_str());
    if (!strcmp(key, "macro_names"))              /* eight names between bars, as the preset gave them; an empty one is a macro that moves nothing ("Cutoff||Wet|||||") */
        return write_str(buf, buf_len, inst->macn_str);
    if (!strcmp(key, "macro_routes"))             /* "macro:key:travel,..." as the preset gave them: the remote UI re-reads the keys a macro moves */
        return write_str(buf, buf_len, inst->mac_str);
    if (!strcmp(key, "demo_now")) {
        char tmp[48];
        snprintf(tmp, sizeof tmp, "%d,%d,%d", inst->demo_pack.load(), inst->demo_part.load(), inst->engine.demoPlaying() ? 1 : 0);
        return write_str(buf, buf_len, tmp);
    }
    if (!strcmp(key, "preset_now")) {
        auto ps = inst->storeList();
        const int b = inst->bank_index.load();
        const int first = b >= 0 && b < (int) ps->banks.size() ? ps->bank_first[(size_t) b] : 0;
        char tmp[32];
        snprintf(tmp, sizeof tmp, "%d,%d", std::max(b, 0), std::max(inst->store_index.load() - first, 0));
        return write_str(buf, buf_len, tmp);
    }
    /* Write-only in effect (any value rescans), but it is DECLARED, and the manager's initial fetch reads every declared key in batches of 8:
     * a key that fails to read skips the final flush and the last few values of the list never reach the page (the preset browser's
     * preset_list / preset_now among them). Every declared key must read back. */
    if (!strcmp(key, "rescan"))
        return write_str(buf, buf_len, "0");
    if (!strcmp(key, "macro_edit"))
        return write_str(buf, buf_len, "");
    if (!strcmp(key, "preset_name")) {
        auto ps = inst->storeList();
        const int i = inst->store_index.load();
        return write_str(buf, buf_len, i >= 0 && i < (int) ps->items.size() ? ps->items[(size_t) i].name.c_str() : "");
    }
    /* "1" while the worker still owes us file work (used by tests and by
     * anything that wants to read back a save it just asked for) */
    if (!strcmp(key, "busy"))
        return write_str(buf, buf_len, inst->loader.busy() ? "1" : "0");

    if (!strcmp(key, "ready"))
        return write_str(buf, buf_len,
                         inst->ready.load(std::memory_order_relaxed) ? "1" : "0");

    if (!strcmp(key, "state")) {
        apply_pending(inst);                            /* what is saved is the sound that was chosen */
        build_state_blob(inst);
        return write_str(buf, buf_len, inst->state_buf);
    }

    int idx = param_index(key);
    if (idx < 0) return -1;
    apply_pending(inst);                                /* a read of a setting is a read of the chosen sound */
    const tb_param_t *p = &tb_params[idx];

    if (p->type == TB_PATH)
        return write_str(buf, buf_len, inst->wt_path[tb_path_slot(idx)]);

    if (p->type == TB_ENUM) {
        const char *name = p->options[(int) inst->params()[idx]];
        return write_str(buf, buf_len, name ? name : "?");
    }
    char tmp[32];
    snprintf(tmp, sizeof tmp, "%g", (double) inst->params()[idx]);
    return write_str(buf, buf_len, tmp);
}

static int tb_get_error(void *, char *, int) { return 0; }

/* ------------------------------------------------------------------ */
/* MIDI + audio                                                        */
/* ------------------------------------------------------------------ */

static void tb_on_midi(void *instance, const uint8_t *msg, int len, int source)
{
    auto *inst = (tablor_instance *) instance;
    if (!inst || !msg) return;
    inst->engine.onMidi(msg, len);
}

/* Hardware flush-to-zero for the length of one audio callback. FPCR is per thread, so it is set here, on
 * whichever thread the host renders from, and put back on return so the host's own code runs as it did.
 * Bit 24 (FZ) makes the core treat denormal operands and results as zero instead of taking the slow path,
 * which is what turned a decaying reverb or filter tail into a crackle. fx.h's sanitize() flushes the same
 * states in software, for builds and cores where this bit does nothing. Other targets: no-op. */
struct FlushToZero {
#if defined(__aarch64__)
    uint64_t saved = 0;
    FlushToZero()
    {
        __asm__ volatile("mrs %0, fpcr" : "=r"(saved));
        __asm__ volatile("msr fpcr, %0" : : "r"(saved | (1ULL << 24)));
    }
    ~FlushToZero() { __asm__ volatile("msr fpcr, %0" : : "r"(saved)); }
#endif
};

static void tb_render_block(void *instance, int16_t *out_lr, int frames)
{
    auto *inst = (tablor_instance *) instance;
    if (!inst || !inst->ready.load(std::memory_order_relaxed)) {
        memset(out_lr, 0, (size_t) frames * 2 * sizeof(int16_t));
        return;
    }
    if (inst->pend_idx.load(std::memory_order_relaxed) >= 0 && steady_ms() >= inst->pend_due.load(std::memory_order_relaxed))
        apply_pending(inst);                            /* the knob has rested */
    FlushToZero ftz;
    if (g_host && g_host->get_bpm)
        inst->engine.setHostBpm(g_host->get_bpm());
    if (g_host && g_host->get_beat_position)                 /* the Move's transport, for the demo (guarded: an older host has no such entry) */
        inst->engine.setHostBeat(g_host->get_beat_position(), true);
    else
        inst->engine.setHostBeat(-1.0, false);
    inst->engine.renderBlock(out_lr, frames);
}

/* ------------------------------------------------------------------ */
/* Lifecycle                                                           */
/* ------------------------------------------------------------------ */

static void *tb_create_instance(const char *module_dir, const char *json_defaults)
{
    (void) json_defaults;
    auto *inst = new (std::nothrow) tablor_instance();
    if (!inst) return nullptr;
    if (const char *e = getenv("TB_PRESET_SETTLE_MS")) inst->settle_ms = std::max(0, atoi(e));

    if (module_dir)
        snprintf(inst->module_dir, sizeof inst->module_dir, "%s", module_dir);

    /* create_instance runs on the SPI callback too (MODULES.md, "Threading:
     * there is no control thread"). Everything below is file I/O, a directory
     * walk, a ~20 MB first-run copy and an FFT — none of which may happen
     * here. Start the worker and let it do all of it; the engine renders
     * silence until the Init table is published. */
    inst->loader.start();
    inst->loader.post([inst] {
        tb::WtScanner::seedUserFolder(inst->module_dir);   /* first-run copy */
        if (inst->loader.stopping()) return;   /* destroy_instance waiting */
        inst->scanner.scan();                              /* opendir walk   */
        if (inst->loader.stopping()) return;
        /* Derive the pack list and the published option lists ONCE, off the
         * single scan — so get_param never has to. */
        tb_publish_selection(inst);
        load_presets(inst);                                /* fopen + scan   */
        export_presets_to_schwung(inst);   /* into Schwung's own preset store */
        scan_store_presets(inst);          /* the list the preset page shows */
        scan_demos(inst);                  /* the demo parts */
        /* AGAIN, now that the presets exist. The first call publishes the
         * wavetable lists, but the PRESET cell's options ARE the preset list,
         * and load_presets runs after it -- so a single early call left that
         * enum with no options at all: the cell read 0 and stepping it found
         * nothing. */
        tb_publish_selection(inst);
        if (inst->loader.stopping()) return;
        /* The digest is built from the real samples, on this thread, as each
         * table lands -- including these built-in ones. */
        inst->loader.onTable = [inst](int osc, const tb::Wavetable &wt) {
            tb_publish_shape(inst, osc, wt);
        };
        auto init0 = tb::makeInitTable();                  /* FFT + ~13 MB */
        tb_publish_shape(inst, 0, *init0);
        tb_publish_shape(inst, 1, *init0);
        tb_publish_shape(inst, 2, *init0);
        inst->engine.setTable(0, init0);
        inst->engine.setTable(1, tb::makeInitTable());
        inst->engine.setTable(2, init0);    /* shared: another Init is ~13 MB */
        /* The host picks the first sound as the module loads, before this list exists, so that request found nothing to load and
         * the module played its Init sound until the preset was chosen again. Load what was asked for (or, if nothing was, the
         * first sound), now that the list is here. A saved state that landed first wins: the host asks for the first sound at load and
         * then restores the slot's own, so on a slow scan (a reboot) the request used to land on top of the restore, and the host saved
         * the first sound back over the slot. */
        {
            if (auto pk = std::atomic_load(&inst->want_pk)) select_preset_by_key(inst, *pk);
            const int want = inst->pending_preset.exchange(-1);
            auto ps = inst->storeList();
            if (!ps->items.empty() && !ps->banks.empty() && !inst->state_seen.load()) {
                const int b = std::min(std::max(inst->bank_index.load(), 0), (int) ps->banks.size() - 1);
                const int n = ps->bank_n[(size_t) b];
                if (n > 0) {
                    const int i = ps->bank_first[(size_t) b] + std::min(std::max(want, 0), n - 1);
                    inst->store_index = i;
                    apply_state_blob(inst, ps->items[(size_t) i].blob.c_str());
                }
            }
        }
        /* a state restore may have landed while we were scanning: re-apply
         * whatever paths it stored, now that the list and loader are live */
        for (int osc = 0; osc <= TB_WT_SLOTS + 3; osc++)
            if (inst->wt_path[osc][0]) {
                char keep[512];
                snprintf(keep, sizeof keep, "%s", inst->wt_path[osc]);
                set_table_path(inst, osc, keep);
            }
        inst->ready = true;
        if (g_host && g_host->log) {
            char msg[160];
            snprintf(msg, sizeof msg,
                     TB_MODULE_ID ": v" TB_VERSION " ready, %d wavetables, %d packs, %d presets",
                     (int) inst->scanner.list().size(),
                     (int) inst->selection()->packs.size(),
                     (int) inst->presetList()->items.size());
            g_host->log(msg);
        }
    });
    return inst;
}

static void tb_destroy_instance(void *instance)
{
    auto *inst = (tablor_instance *) instance;
    if (!inst) return;
    /* Join the worker FIRST: member destruction runs in reverse declaration
     * order, which would free presets/sel/wt_shape while a job still writes
     * them. */
    inst->loader.stop();
    delete inst;
}

static plugin_api_v2_t g_api = {
    /* .api_version      = */ 2,
    /* .create_instance  = */ tb_create_instance,
    /* .destroy_instance = */ tb_destroy_instance,
    /* .on_midi          = */ tb_on_midi,
    /* .set_param        = */ tb_set_param,
    /* .get_param        = */ tb_get_param,
    /* .get_error        = */ tb_get_error,
    /* .render_block     = */ tb_render_block,
};

extern "C" plugin_api_v2_t *move_plugin_init_v2(const host_api_v1_t *host)
{
    g_host = host;
    return &g_api;
}
