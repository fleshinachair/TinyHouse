/* Joining a big file the remote UI sent in parts.
 *
 * The Move's file manager keeps an upload in memory only up to a limit and writes a bigger one to its temp folder, which on the Move is on the (nearly
 * full) system partition: a large sample set then fails in transit. So the remote UI's Import sends a big file in small pieces named
 *     <file>.part<i>of<n>        (i from 0, four digits each: Piano.tbs.part0002of0010)
 * and this puts them back together, when the module rescans: a file is joined once all n pieces are there (a missing piece means the upload is not
 * finished or failed: nothing is touched), written under a temporary name and renamed into place, and the pieces are then removed.
 * Only folders under the root given are looked at. Works on directory listings and plain stdio, as the scanner does. */
#pragma once

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dirent.h>
#include <map>
#include <string>
#include <sys/stat.h>
#include <vector>

namespace tb {

struct PartJoin {
    /* "<stem>.part0002of0010" -> stem, 2, 10; false when the name is not a piece */
    static bool parsePart(const std::string &name, std::string &stem, int &i, int &n)
    {
        const size_t p = name.rfind(".part");
        if (p == std::string::npos || p == 0 || name.size() != p + 5 + 4 + 2 + 4) return false;
        const char *s = name.c_str() + p + 5;
        for (int k = 0; k < 4; k++) if (s[k] < '0' || s[k] > '9') return false;
        if (s[4] != 'o' || s[5] != 'f') return false;
        for (int k = 6; k < 10; k++) if (s[k] < '0' || s[k] > '9') return false;
        i = atoi(std::string(s, 4).c_str());
        n = atoi(std::string(s + 6, 4).c_str());
        if (n < 1 || i >= n) return false;
        stem = name.substr(0, p);
        return true;
    }

    /* Join what is complete in one folder, then the folders below it. Returns how many files were put together. */
    static int joinAll(const std::string &dir, int depth = 0)
    {
        DIR *d = opendir(dir.c_str());
        if (!d) return 0;
        std::map<std::string, std::vector<int>> found;       /* stem -> pieces present (index) */
        std::map<std::string, int> total;                    /* stem -> n */
        std::vector<std::string> sub;
        while (dirent *e = readdir(d)) {
            const std::string nm = e->d_name;
            if (nm == "." || nm == "..") continue;
            std::string stem; int i = 0, n = 0;
            if (parsePart(nm, stem, i, n)) {
                auto t = total.find(stem);
                if (t != total.end() && t->second != n) { found[stem].clear(); total[stem] = -1; continue; }     /* pieces of two different splits: leave them */
                if (t == total.end() || t->second > 0) { total[stem] = n; found[stem].push_back(i); }
                continue;
            }
            struct stat st;
            if (depth < 6 && stat((dir + "/" + nm).c_str(), &st) == 0 && S_ISDIR(st.st_mode)) sub.push_back(nm);
        }
        closedir(d);
        int joined = 0;
        for (auto &kv : found) {
            const std::string &stem = kv.first;
            const int n = total[stem];
            if (n <= 0) continue;
            std::vector<int> have = kv.second;
            std::sort(have.begin(), have.end());
            have.erase(std::unique(have.begin(), have.end()), have.end());
            if ((int) have.size() != n) continue;            /* not all here yet */
            if (joinOne(dir, stem, n)) joined++;
        }
        for (auto &s : sub) joined += joinAll(dir + "/" + s, depth + 1);
        return joined;
    }

private:
    static std::string pieceName(const std::string &dir, const std::string &stem, int i, int n)
    {
        char suf[32];
        snprintf(suf, sizeof suf, ".part%04dof%04d", i, n);
        return dir + "/" + stem + suf;
    }

    static bool joinOne(const std::string &dir, const std::string &stem, int n)
    {
        const std::string out = dir + "/" + stem, tmp = out + ".joining";
        FILE *o = fopen(tmp.c_str(), "wb");
        if (!o) return false;
        bool ok = true;
        std::vector<char> buf(1 << 20);
        for (int i = 0; i < n && ok; i++) {
            FILE *f = fopen(pieceName(dir, stem, i, n).c_str(), "rb");
            if (!f) { ok = false; break; }
            size_t r;
            while ((r = fread(buf.data(), 1, buf.size(), f)) > 0) if (fwrite(buf.data(), 1, r, o) != r) { ok = false; break; }
            if (ferror(f)) ok = false;
            fclose(f);
        }
        if (fclose(o) != 0) ok = false;
        if (ok && rename(tmp.c_str(), out.c_str()) != 0) {      /* where rename will not replace (Windows, in the tests): clear the old file and go again */
            remove(out.c_str());
            ok = rename(tmp.c_str(), out.c_str()) == 0;
        }
        if (!ok) { remove(tmp.c_str()); return false; }
        for (int i = 0; i < n; i++) remove(pieceName(dir, stem, i, n).c_str());
        return true;
    }
};

} // namespace tb
