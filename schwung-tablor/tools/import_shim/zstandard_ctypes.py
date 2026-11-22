"""PoC stand-in for the zstandard package: the few calls serum2_preset_loader makes, through the Move's own libzstd (ctypes)."""
import ctypes
import ctypes.util

_lib = ctypes.CDLL(ctypes.util.find_library("zstd") or "libzstd.so.1")
_lib.ZSTD_getFrameContentSize.restype = ctypes.c_ulonglong
_lib.ZSTD_getFrameContentSize.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
_lib.ZSTD_decompress.restype = ctypes.c_size_t
_lib.ZSTD_decompress.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_size_t]
_lib.ZSTD_compress.restype = ctypes.c_size_t
_lib.ZSTD_compress.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_int]
_lib.ZSTD_compressBound.restype = ctypes.c_size_t
_lib.ZSTD_compressBound.argtypes = [ctypes.c_size_t]
_lib.ZSTD_isError.restype = ctypes.c_uint
_lib.ZSTD_isError.argtypes = [ctypes.c_size_t]
_lib.ZSTD_getErrorName.restype = ctypes.c_char_p
_lib.ZSTD_getErrorName.argtypes = [ctypes.c_size_t]

_UNKNOWN, _ERROR = 2**64 - 1, 2**64 - 2


class ZstdError(Exception):
    pass


def frame_content_size(data: bytes) -> int:
    data = bytes(data)
    n = _lib.ZSTD_getFrameContentSize(data, len(data))
    if n == _ERROR:
        raise ZstdError("not a zstd frame")
    return -1 if n == _UNKNOWN else int(n)


class ZstdDecompressor:
    def decompress(self, data: bytes, max_output_size: int = 0) -> bytes:
        data = bytes(data)
        n = frame_content_size(data)
        cap = n if n >= 0 else max_output_size
        if cap <= 0:
            raise ZstdError("unknown output size")
        buf = ctypes.create_string_buffer(cap)
        r = _lib.ZSTD_decompress(buf, cap, data, len(data))
        if _lib.ZSTD_isError(r):
            raise ZstdError(_lib.ZSTD_getErrorName(r).decode())
        return buf.raw[:r]


class ZstdCompressor:
    def __init__(self, level: int = 3):
        self.level = level

    def compress(self, data: bytes) -> bytes:
        data = bytes(data)
        cap = _lib.ZSTD_compressBound(len(data))
        buf = ctypes.create_string_buffer(cap)
        r = _lib.ZSTD_compress(buf, cap, data, len(data), self.level)
        if _lib.ZSTD_isError(r):
            raise ZstdError(_lib.ZSTD_getErrorName(r).decode())
        return buf.raw[:r]
