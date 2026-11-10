"""A reader for the CBOR a .SerumPreset holds, standing in for the `cbor2` package inside the browser importer.

The real cbor2 (6.x) is a compiled extension with no pure-Python fallback, and Pyodide does not ship it. serum2_preset_loader only
reads with it (`loads`), so this decodes that: integers, byte and text strings, arrays, maps, floats of three widths, booleans and null,
definite and indefinite lengths. A tag is read through to the value it wraps; nothing Serum writes uses one.
Writing (`dumps`) is not needed to import and is not provided."""
import struct


class CBORDecodeError(ValueError):
    pass


def loads(data):
    data = bytes(data)
    pos = [0]
    end = len(data)

    def need(n):
        if pos[0] + n > end:
            raise CBORDecodeError("truncated")

    def uint(info):
        if info < 24:
            return info
        n = {24: 1, 25: 2, 26: 4, 27: 8}.get(info)
        if n is None:
            raise CBORDecodeError("bad length")
        need(n)
        v = int.from_bytes(data[pos[0]:pos[0] + n], "big")
        pos[0] += n
        return v

    def item(depth):
        if depth > 400:
            raise CBORDecodeError("too deep")
        need(1)
        ib = data[pos[0]]
        pos[0] += 1
        major, info = ib >> 5, ib & 31
        if major == 0:
            return uint(info)
        if major == 1:
            return -1 - uint(info)
        if major in (2, 3):
            if info == 31:                                  # indefinite: chunks until a break
                out = []
                while True:
                    need(1)
                    if data[pos[0]] == 0xFF:
                        pos[0] += 1
                        break
                    out.append(item(depth + 1))
                return b"".join(out) if major == 2 else "".join(out)
            n = uint(info)
            need(n)
            raw = data[pos[0]:pos[0] + n]
            pos[0] += n
            return raw if major == 2 else raw.decode("utf-8")
        if major == 4:
            if info == 31:
                out = []
                while True:
                    need(1)
                    if data[pos[0]] == 0xFF:
                        pos[0] += 1
                        return out
                    out.append(item(depth + 1))
            return [item(depth + 1) for _ in range(uint(info))]
        if major == 5:
            out = {}
            if info == 31:
                while True:
                    need(1)
                    if data[pos[0]] == 0xFF:
                        pos[0] += 1
                        return out
                    k = item(depth + 1)
                    out[k] = item(depth + 1)
            for _ in range(uint(info)):
                k = item(depth + 1)
                out[k] = item(depth + 1)
            return out
        if major == 6:
            uint(info)                                      # the tag number: read through to the wrapped value
            return item(depth + 1)
        if info == 20:
            return False
        if info == 21:
            return True
        if info in (22, 23):
            return None
        if info == 25:
            need(2)
            v = struct.unpack(">e", data[pos[0]:pos[0] + 2])[0]
            pos[0] += 2
            return v
        if info == 26:
            need(4)
            v = struct.unpack(">f", data[pos[0]:pos[0] + 4])[0]
            pos[0] += 4
            return v
        if info == 27:
            need(8)
            v = struct.unpack(">d", data[pos[0]:pos[0] + 8])[0]
            pos[0] += 8
            return v
        raise CBORDecodeError("unsupported simple value")

    v = item(0)
    if pos[0] != end:
        raise CBORDecodeError("extra data")
    return v


def dumps(*_a, **_k):
    raise NotImplementedError("the browser importer reads presets only")
