/* PartJoin: a big file sent in pieces is put back together once all its pieces are there, and not before. Runs natively. */

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
#include <sys/stat.h>
#ifdef _WIN32
#include <direct.h>
#define MKDIR(p) _mkdir(p)
#else
#define MKDIR(p) mkdir(p, 0755)
#endif

#include "../src/dsp/partjoin.h"

using namespace tb;

static int g_fail = 0;
#define CHECK(cond, ...) do { \
    if (!(cond)) { g_fail++; printf("FAIL: " __VA_ARGS__); printf("\n"); } \
    else         {           printf("  ok: " __VA_ARGS__); printf("\n"); } \
} while (0)

static std::string readAll(const std::string &p, bool &exists)
{
    FILE *f = fopen(p.c_str(), "rb");
    exists = f != nullptr;
    std::string s;
    if (!f) return s;
    char b[4096]; size_t r;
    while ((r = fread(b, 1, sizeof b, f)) > 0) s.append(b, r);
    fclose(f);
    return s;
}
static void writeAll(const std::string &p, const std::string &s) { FILE *f = fopen(p.c_str(), "wb"); fwrite(s.data(), 1, s.size(), f); fclose(f); }
static bool has(const std::string &p) { bool e; readAll(p, e); return e; }

int main()
{
    std::string root = std::string(getenv("TMP") ? getenv("TMP") : "/tmp") + "/pj_test";
    MKDIR(root.c_str()); MKDIR((root + "/Imports").c_str());
    const std::string d = root + "/Imports";
    for (const char *old : { "/Big set.tbs", "/Big set.tbs.joining", "/Mixed.tbs", "/Mixed.tbs.part0000of0002", "/Mixed.tbs.part0001of0003",
                             "/Big set.tbs.part0000of0001", "/Big set.tbs.part0000of0003", "/Big set.tbs.part0001of0003", "/Big set.tbs.part0002of0003" })
        remove((d + old).c_str());                  /* what an earlier run left behind */

    /* names */
    {
        std::string stem; int i = -1, n = -1;
        CHECK(PartJoin::parsePart("Piano.tbs.part0002of0010", stem, i, n) && stem == "Piano.tbs" && i == 2 && n == 10, "a piece's name parses");
        CHECK(!PartJoin::parsePart("Piano.tbs", stem, i, n), "a plain name is not a piece");
        CHECK(!PartJoin::parsePart("Piano.tbs.part2of10", stem, i, n), "a short number is not a piece");
        CHECK(!PartJoin::parsePart("Piano.tbs.part0010of0010", stem, i, n), "a piece past the end is refused");
        CHECK(!PartJoin::parsePart(".part0000of0001", stem, i, n), "a piece with no name is refused");
    }

    /* three pieces, sent out of order, with one still missing: nothing happens; then complete: joined in order, pieces removed */
    std::string a(5000, 'a'), b(5000, 'b'), c(123, 'c');
    for (size_t k = 0; k < a.size(); k++) a[k] = (char) (k * 7); for (size_t k = 0; k < b.size(); k++) b[k] = (char) (k * 13 + 1);
    writeAll(d + "/Big set.tbs.part0002of0003", c);
    writeAll(d + "/Big set.tbs.part0000of0003", a);
    CHECK(PartJoin::joinAll(root) == 0 && !has(d + "/Big set.tbs"), "with a piece missing nothing is joined");
    CHECK(has(d + "/Big set.tbs.part0000of0003") && has(d + "/Big set.tbs.part0002of0003"), "and the pieces that are there are left");
    writeAll(d + "/Big set.tbs.part0001of0003", b);
    CHECK(PartJoin::joinAll(root) == 1, "complete: one file is joined");
    bool ex; std::string got = readAll(d + "/Big set.tbs", ex);
    CHECK(ex && got == a + b + c, "the joined file is the pieces in order (%zu bytes)", got.size());
    CHECK(!has(d + "/Big set.tbs.part0000of0003") && !has(d + "/Big set.tbs.part0001of0003") && !has(d + "/Big set.tbs.part0002of0003"), "the pieces are removed");
    CHECK(!has(d + "/Big set.tbs.joining"), "no temporary file is left");

    /* a re-send replaces the old file; a second pass finds nothing to do */
    writeAll(d + "/Big set.tbs.part0000of0001", "new");
    CHECK(PartJoin::joinAll(root) == 1 && readAll(d + "/Big set.tbs", ex) == "new", "a new split replaces the old file");
    CHECK(PartJoin::joinAll(root) == 0, "nothing left to join");

    /* pieces of two different splits of one name are left alone */
    writeAll(d + "/Mixed.tbs.part0000of0002", "x"); writeAll(d + "/Mixed.tbs.part0001of0003", "y");
    CHECK(PartJoin::joinAll(root) == 0 && !has(d + "/Mixed.tbs"), "pieces of two different splits are not joined");

    printf(g_fail ? "FAILED %d\n" : "all passed\n", g_fail);
    return g_fail ? 1 : 0;
}
