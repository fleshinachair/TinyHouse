"""Which parts of the converter a conversion went through, so that an update converts again only the presets it can change.

Convert on load (reconvert.py) used to convert every kept preset again whenever the converter changed at all. Instead, each conversion made by a bundle
records its path through the converter's code: every step from one executed line to the next inside a function (an "edge"). After an update the Move
checks those steps against the new code. A preset is converted again only if one of its steps no longer exists unchanged; otherwise the old result is
what the new converter would make, and only the stamp moves on.

Identity of a line: its file, the function it is in, its logical line's code (tokens: comments, blank lines and line breaks inside a statement do not
count, indentation does), which occurrence of that code in the function it is, the module-level constants it names (by their definitions, followed
through the constants they use, in any converter file) and the content of any data table it names by a string literal ("calibration/fx_laws.json").

A step a -> b still exists when both lines exist with their identity and nothing new sits on the way between them:
  - the line nearest above b at b's indentation or less is the same (code inserted before b, at b's level, would run first: an elif, a statement),
  - when a is a plain statement (it always falls through), the code line after a is the same (code inserted after a would run first).
Code added inside a branch the preset skipped changes neither, so the presets that never enter that branch keep their result.

Module-level statements other than constants and functions (imports, classes, top-level logic) and EPOCH form one "global" hash: any change there
converts everything again.

Caches: a conversion that reuses another one's cached work (a fitted curve, a loaded table) would not go through the code that made it. reset_caches()
empties every cache before a recorded conversion; CACHES lists them (a new cache must be added there; test: tools/deps_check.py).
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import re
import struct
import sys
import tokenize
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
EPOCH = 1                       # raise it to convert every kept preset again with the next update (a change the line tables cannot see)
DATA_EXT = (".json", ".npz", ".npy")
SKIP_DIRS = {"lib", "__pycache__", "licenses"}       # numpy (the Move's copy) and licence texts are not the converter's code
MAGIC = b"TDEP2"
# Tracing every line is slow on the Move's Python 3.10 (a light preset 0.2 -> 1.7 s). A small function (SMALL code lines or fewer) called HOT times in one
# conversion, or running LOOP lines in one call, is recorded whole from then on (any change in it counts) and no longer traced. Big functions are always
# traced line by line: they hold most of the converter's branches, and recording one whole would convert every preset again for any fix in it.
SMALL = 80
HOT = 3
LOOP = 200

# (module, attribute, how to empty it): every cache that lets one conversion skip work another one did. Lists and dicts are emptied in place.
CACHES = [
    ("import_web", "_LEVEL_LAW", None),
    ("fxmap", "_reso_table", "clear"), ("fxmap", "_fx_models", None), ("fxmap", "_conv_cache", "clear"), ("fxmap", "_REDUCE_CACHE", "clear"),
    ("fxmap", "_laws", None), ("fxmap", "_fingerprints", None), ("fxmap", "_cache", None), ("fxmap", "_FIT_MEMO", "empty"),
    ("sample_export", "_found", "clear"), ("sample_export", "_found_sfz", "clear"), ("sample_export", "_standin", "clear"),
    ("vital_import", "_PD_TABLE", None), ("vital_import", "_FALL_CACHE", "clear"), ("vital_import", "_HOLD_CACHE", "clear"),
    ("vital_morph", "_RAND", None),
]


def reset_caches() -> None:
    for mod, attr, how in CACHES:
        m = sys.modules.get(mod)
        if m is None or not hasattr(m, attr):
            continue
        if how == "clear":
            getattr(m, attr).clear()
        elif how == "empty":
            setattr(m, attr, {})               # loaded and empty: the memo file is not read again (a recorded conversion computes every fit itself)
        else:
            setattr(m, attr, None)


def _h32(*parts) -> int:
    return struct.unpack("<I", hashlib.sha1("\0".join(map(str, parts)).encode()).digest()[:4])[0]


def _data_hash(root: Path, lit: str) -> str | None:
    """The content hash of a data table a string literal names, if one is in the bundle (JSON compared by content: the bundle minifies it)."""
    lit = lit.replace("\\", "/").lstrip("/")
    if not lit.lower().endswith(DATA_EXT):
        return None
    for f in (root / lit, root.parent / lit, root / Path(lit).name):
        if f.is_file():
            b = f.read_bytes()
            if f.suffix == ".json":
                try:
                    b = json.dumps(json.loads(b), sort_keys=True, separators=(",", ":")).encode()
                except ValueError:
                    pass
            return hashlib.sha1(b).hexdigest()[:12]
    return None


class _File:
    def __init__(self, rel: str, src: str):
        self.rel = rel
        self.tree = ast.parse(src)
        self.phys = src.splitlines()
        # logical lines: each physical line with code -> (index of its logical line, its tokens' text), the logical line's tokens
        self.logical: list[list[str]] = []
        self.lines: dict[int, int] = {}          # physical line -> logical index
        self.strings: dict[int, list[str]] = {}  # logical index -> string literals
        self.names: dict[int, set[str]] = {}     # logical index -> NAME tokens
        cur, start = [], None
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type in (tokenize.COMMENT, tokenize.NL, tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER):
                continue
            if tok.type == tokenize.NEWLINE:
                if cur:
                    self.logical.append(cur)
                cur, start = [], None
                continue
            k = len(self.logical)
            if start is None:
                start = tok.start[0]
                line = self.phys[start - 1]
                cur.append(str(len(line) - len(line.lstrip())))
            for ln in range(tok.start[0], tok.end[0] + 1):
                self.lines.setdefault(ln, k)
            cur.append(tok.string)
            if tok.type == tokenize.STRING:
                self.strings.setdefault(k, []).append(tok.string)
            elif tok.type == tokenize.NAME:
                self.names.setdefault(k, set()).add(tok.string)
        if cur:
            self.logical.append(cur)
        self.order = sorted(self.lines)
        self.pos = {ln: i for i, ln in enumerate(self.order)}
        self.first = {}
        for ln in self.order:
            self.first.setdefault(self.lines[ln], ln)
        self.indent = {ln: int(self.logical[self.lines[ln]][0]) for ln in self.order}
        # the function each line is in (innermost), for the line's identity
        self.owner: dict[int, str] = {}
        self.defs: dict[int, tuple[int, int]] = {}      # a function's first line (its decorator or def) and its def line -> its span
        self.inside: dict[int, tuple[int, int]] = {}    # line -> span of the innermost function holding it
        spans = []
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                spans.append((node.lineno, node.end_lineno or node.lineno, node.name, min([d.lineno for d in node.decorator_list] + [node.lineno]), not isinstance(node, ast.ClassDef)))
        for a, b, name, first, func in sorted(spans, key=lambda s: s[1] - s[0], reverse=True):     # outer first: inner spans overwrite
            for ln in range(first, b + 1):
                self.owner[ln] = name
                if func:
                    self.inside[ln] = (first, b)
            if func:
                self.defs[first] = self.defs[a] = (first, b)

    def span_for(self, line: int) -> tuple[int, int]:
        """The code a code object starting at `line` belongs to: its function, else the function holding it (a comprehension, a lambda), else its statement."""
        if line in self.defs:
            return self.defs[line]
        if line in self.inside:
            return self.inside[line]
        k = self.lines.get(line)
        if k is None:
            return (line, line)
        last = max(ln for ln, kk in self.lines.items() if kk == k)
        return (self.first[k], last)


class Code:
    """The converter's code as line identities, for recording a conversion's path and checking it against a newer converter."""

    def __init__(self, root: Path = HERE):
        self.root = Path(root).resolve()
        self.files: dict[str, _File] = {}
        for f in sorted(self.root.rglob("*.py")):
            rel = f.relative_to(self.root)
            if SKIP_DIRS & set(rel.parts[:-1]) or rel.as_posix() == "conv_deps.py":
                continue                                # numpy, licences, and this file (it runs around a conversion, not in it)
            self.files[str(f)] = _File(rel.as_posix(), f.read_text(encoding="utf-8"))
        self._constants()
        self.ids: dict[str, dict[int, int]] = {}         # filename -> physical line -> identity
        self.byid: dict[int, tuple[str, int]] = {}
        dup = set()
        for fn, F in self.files.items():
            ids, seen, cache = {}, {}, {}
            for ln in F.order:
                k = F.lines[ln]
                if k not in cache:
                    owner = F.owner.get(ln, "<module>")
                    text = " ".join(F.logical[k])
                    seen[(owner, text)] = seen.get((owner, text), 0) + 1
                    consts = sorted(self.const_hash[n] for n in F.names.get(k, ()) if n in self.const_hash)
                    data = sorted(filter(None, (_data_hash(self.root, s.strip("\"'rbuRBU")) for s in F.strings.get(k, ()))))
                    cache[k] = (F.rel, owner, text, seen[(owner, text)], ",".join(consts), ",".join(data))
                i = _h32(*cache[k], ln - F.first[k])      # each physical line of a statement: its place in it
                ids[ln] = i
                if i in self.byid:
                    dup.add(i)
                self.byid[i] = (fn, ln)
            self.ids[fn] = ids
        for i in dup:                                   # a 32-bit collision: that identity cannot be looked up (a step through it counts as changed)
            self.byid.pop(i, None)
        own = self.root / "conv_deps.py"                # this converter's EPOCH (the file is not in the tables: it runs around a conversion)
        m = re.search(r"^EPOCH\s*=\s*(\d+)", own.read_text(encoding="utf-8"), re.M) if own.is_file() else None
        self.glob = _h32(int(m.group(1)) if m else EPOCH, self._global_text())
        self._keys: dict[str, str | None] = {}
        self._whole: dict = {}
        self._small: dict = {}

    def small(self, fn: str, line: int) -> bool:
        """Whether the function a code object starting at `line` belongs to is small enough to be recorded whole (SMALL code lines or fewer)."""
        k = (fn, line)
        if k not in self._small:
            F = self.files[fn]
            a, b = F.span_for(line)
            self._small[k] = sum(1 for ln in range(a, b + 1) if ln in F.lines) <= SMALL
        return self._small[k]

    def whole_hash(self, fn: str, span: tuple[int, int]) -> int:
        """One function as a whole (every line's identity): a function recorded whole holds only while none of it changes."""
        k = (fn, span)
        if k not in self._whole:
            ids = self.ids[fn]
            self._whole[k] = _h32(*(ids[ln] for ln in range(span[0], span[1] + 1) if ln in ids))
        return self._whole[k]

    def _constants(self) -> None:
        """const_hash: NAME -> hash of every module-level assignment to that name (in any file), following the constants each definition uses."""
        stmts: dict[str, list[tuple[str, set[str], list[str]]]] = {}
        for F in self.files.values():
            for node in F.tree.body:
                targets = []
                if isinstance(node, ast.Assign):
                    targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
                    targets = [node.target.id]
                if not targets:
                    continue
                text = ast.dump(node.value if not isinstance(node, ast.AnnAssign) else node.value)
                used = {n.id for n in ast.walk(node.value) if isinstance(n, ast.Name)}
                lits = [n.value for n in ast.walk(node.value) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
                data = [d for d in (_data_hash(self.root, s) for s in lits) if d]
                for t in targets:
                    stmts.setdefault(t, []).append((F.rel + ":" + text, used, data))
        memo: dict[str, str] = {}

        def h(name: str, stack: tuple = ()) -> str:
            if name in memo:
                return memo[name]
            if name in stack:
                return "cycle"
            parts = []
            for text, used, data in stmts[name]:
                parts.append(text + "|" + ",".join(data) + "|" + ",".join(h(u, stack + (name,)) for u in sorted(used) if u in stmts and u != name))
            memo[name] = hashlib.sha1("\n".join(sorted(parts)).encode()).hexdigest()[:12]
            return memo[name]

        self.const_hash = {n: h(n) for n in stmts}

    def _global_text(self) -> str:
        """Module-level code that is neither a function nor a constant: imports, classes' own statements, top-level logic."""
        out = []
        for F in sorted(self.files.values(), key=lambda f: f.rel):
            for node in F.tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if isinstance(node, (ast.Assign, ast.AnnAssign)) and all(isinstance(t, ast.Name) for t in (node.targets if isinstance(node, ast.Assign) else [node.target])):
                    continue
                if isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Constant):
                    continue                            # a docstring
                if isinstance(node, ast.ClassDef):          # its own statements; its methods are lines like any function's
                    out.append(F.rel + ":class " + node.name + "".join(ast.dump(x) for x in node.bases + node.decorator_list)
                               + "".join(ast.dump(b) for b in node.body if not isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef))))
                    continue
                out.append(F.rel + ":" + ast.dump(node))
        return "\n".join(out)

    def key(self, co_filename: str) -> str | None:
        """The table's name for a code object's file (the same file can be spelt differently: relative, another drive-letter case), or None."""
        try:
            return self._keys[co_filename]
        except KeyError:
            pass
        try:
            k = str(Path(co_filename).resolve())
        except (OSError, ValueError):
            k = co_filename
        norm = {os.path.normcase(f): f for f in self.files}
        self._keys[co_filename] = k = norm.get(os.path.normcase(k))
        return k

    # ---- the step test ----
    def _sig(self, fn: str, a: int, b: int) -> int | None:
        F, ids = self.files[fn], self.ids[fn]
        if a not in ids or b not in ids:
            return None
        k = F.lines[a]
        last = F.logical[k][-1]
        first = F.logical[k][1] if len(F.logical[k]) > 1 else ""
        nxt = 0
        if last != ":" and first not in ("return", "raise", "break", "continue"):          # a plain statement: it falls through to the next line
            i = F.pos[a]
            nxt = ids[F.order[i + 1]] if i + 1 < len(F.order) else 0
        prev = 0
        i = F.pos[b]
        for j in range(i - 1, -1, -1):
            if F.indent[F.order[j]] <= F.indent[b]:
                prev = ids[F.order[j]]
                break
        return _h32(ids[a], ids[b], nxt, prev)

    # ---- recording ----
    def recorder(self) -> "Recorder":
        return Recorder(self)

    def encode(self, edges: dict, whole: set = frozenset()) -> bytes:
        """The steps of one conversion: the line identities it went through and, per step, the two lines (as indices into those) and its signature;
        then the functions recorded whole (hot ones): the identity of each one's first line and its hash."""
        steps = []
        for fn, packed in edges.items():
            for v in packed:
                a, b = v >> 20, v & 0xFFFFF
                s = self._sig(fn, a, b)
                if s is not None:
                    steps.append((self.ids[fn][a], self.ids[fn][b], s))
        idl = sorted({x for st in steps for x in st[:2]})
        ix = {v: i for i, v in enumerate(idl)}
        body = struct.pack("<II", self.glob, len(idl)) + struct.pack(f"<{len(idl)}I", *idl)
        steps = sorted(set((ix[a], ix[b], s) for a, b, s in steps))
        body += struct.pack("<I", len(steps)) + b"".join(struct.pack("<HHI", a, b, s) for a, b, s in steps)
        wl = set()
        for fn, line in whole:
            span = self.files[fn].span_for(line)
            if span[0] in self.ids[fn]:
                wl.add((self.ids[fn][span[0]], self.whole_hash(fn, span)))
        wl = sorted(wl)
        body += struct.pack("<I", len(wl)) + b"".join(struct.pack("<II", i, h) for i, h in wl)
        return MAGIC + zlib.compress(body, 9)

    def unchanged(self, blob: bytes) -> bool:
        """True when every step the recorded conversion took still exists in this code, unchanged: converting again would give the same result."""
        try:
            if not blob.startswith(MAGIC):
                return False
            body = zlib.decompress(blob[len(MAGIC):])
            glob, n = struct.unpack_from("<II", body, 0)
            if glob != self.glob:
                return False
            idl = struct.unpack_from(f"<{n}I", body, 8)
            off = 8 + 4 * n
            m, = struct.unpack_from("<I", body, off)
            off += 4
            for k in range(m):
                a, b, s = struct.unpack_from("<HHI", body, off + 8 * k)
                pa, pb = self.byid.get(idl[a]), self.byid.get(idl[b])
                if pa is None or pb is None or pa[0] != pb[0] or self._sig(pa[0], pa[1], pb[1]) != s:
                    return False
            off += 8 * m
            w, = struct.unpack_from("<I", body, off)
            for k in range(w):
                i, h = struct.unpack_from("<II", body, off + 4 + 8 * k)
                p = self.byid.get(i)
                if p is None or self.whole_hash(p[0], self.files[p[0]].span_for(p[1])) != h:
                    return False
            return True
        except (zlib.error, struct.error, IndexError, ValueError):
            return False


class Recorder:
    """with code.recorder() as rec: ...convert...   then rec.blob(). rec.paused() leaves out work that does not shape the result (the import report)."""

    def __init__(self, code: Code):
        self.code = code
        self.edges: dict = {}                           # file -> {(from line << 20) | to line}
        self.whole: set = set()
        self.calls: dict = {}
        self._old = None

    def _call(self, frame, event, arg):
        co = frame.f_code
        fn = self.code.key(co.co_filename)
        if fn is None:
            return None
        small = self.code.small(fn, co.co_firstlineno)
        if small:
            n = self.calls.get(co, 0) + 1
            self.calls[co] = n
            if n > HOT:                                 # a helper called often: recorded whole from here on, not traced line by line
                self.whole.add((fn, co.co_firstlineno))
                return None
        edges = self.edges.get(fn)
        if edges is None:
            edges = self.edges[fn] = set()
        last = [co.co_firstlineno, 0]                   # a step is kept as (from << 20) | to: the cheapest thing to add per traced line
        if not small:
            def line(frame, event, arg):
                if event == "line":
                    ln = frame.f_lineno
                    edges.add(last[0] << 20 | ln)
                    last[0] = ln
                return line
            return line
        whole = self.whole

        def line_small(frame, event, arg):
            if event == "line":
                ln = frame.f_lineno
                edges.add(last[0] << 20 | ln)
                last[0] = ln
                last[1] += 1
                if last[1] > LOOP:                      # a long loop: the whole function stands for the rest of this call
                    whole.add((fn, co.co_firstlineno))
                    frame.f_trace_lines = False
            return line_small
        return line_small

    def __enter__(self):
        reset_caches()
        self._old = sys.gettrace()
        self._frame = sys._getframe(1)                  # the caller's own lines from here on count too
        self._frame.f_trace = self._call(self._frame, "call", None)
        sys.settrace(self._call)
        return self

    def __exit__(self, *exc):
        sys.settrace(self._old)
        self._frame.f_trace = None
        self._frame = None
        return False

    def paused(self):
        rec = self

        class _P:
            def __enter__(self):
                self.t = sys.gettrace()
                sys.settrace(rec._old)

            def __exit__(self, *exc):
                sys.settrace(self.t)
                return False
        return _P()

    def blob(self) -> bytes:
        return self.code.encode(self.edges, self.whole)


_CODE: Code | None = None


def code() -> Code:
    """This converter's tables (made once per process)."""
    global _CODE
    if _CODE is None:
        _CODE = Code(HERE)
    return _CODE
