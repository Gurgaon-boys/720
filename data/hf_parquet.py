"""Read single rows out of large remote parquet files with HTTP range requests.

The Hugging Face AudioSet mirror (agkphysics/AudioSet) stores FLAC bytes in
parquet shards of ~2.7 GB, 20 row groups x 100 rows each. Downloading whole
shards for a few thousand clips would cost terabytes, so this module:

  1. reads each shard's footer (FileMetaData) and decodes it with a minimal
     Thrift compact-protocol reader (pyarrow does not expose the page index);
  2. reads the small `video_id` column chunk of a row group to find a row;
  3. uses the audio column's offset index to fetch only the page holding that
     row (~1.4 MB) and decodes it (PLAIN or RLE_DICTIONARY, data page v1/v2).

Only the parquet features this mirror uses are implemented; anything else
raises ParquetFormatError so the caller can log the clip as failed.
"""
from __future__ import annotations

import struct
import threading
import time
import urllib.error
import urllib.request

import pyarrow as pa

HF_BASE = "https://huggingface.co/datasets/{repo}/resolve/main/{path}"
USER_AGENT = "720-data-fetch/1.0"
CODECS = {0: None, 1: "snappy", 2: "gzip", 4: "brotli", 6: "zstd", 7: "lz4_raw"}


class ParquetFormatError(Exception):
    pass


# --------------------------------------------------------------------------
# Thrift compact protocol (just enough for parquet metadata)
# --------------------------------------------------------------------------
class _Thrift:
    def __init__(self, buf: bytes, pos: int = 0):
        self.b = buf
        self.p = pos

    def varint(self) -> int:
        shift = out = 0
        while True:
            c = self.b[self.p]
            self.p += 1
            out |= (c & 0x7F) << shift
            if not c & 0x80:
                return out
            shift += 7

    def zigzag(self) -> int:
        n = self.varint()
        return (n >> 1) ^ -(n & 1)

    def value(self, t: int):
        if t in (1, 2):  # bool encoded in the type nibble (struct fields)
            return t == 1
        if t == 3:
            v = struct.unpack_from("b", self.b, self.p)[0]
            self.p += 1
            return v
        if t in (4, 5, 6):
            return self.zigzag()
        if t == 7:
            v = struct.unpack_from("<d", self.b, self.p)[0]
            self.p += 8
            return v
        if t == 8:
            n = self.varint()
            v = bytes(self.b[self.p:self.p + n])
            self.p += n
            return v
        if t in (9, 10):
            h = self.b[self.p]
            self.p += 1
            n, et = h >> 4, h & 0x0F
            if n == 15:
                n = self.varint()
            if et in (1, 2):  # bools inside lists take one byte each
                out = [self.b[self.p + i] == 1 for i in range(n)]
                self.p += n
                return out
            return [self.value(et) for _ in range(n)]
        if t == 11:
            n = self.varint()
            if n == 0:
                return {}
            kv = self.b[self.p]
            self.p += 1
            return {self.value(kv >> 4): self.value(kv & 0x0F) for _ in range(n)}
        if t == 12:
            return self.struct()
        raise ParquetFormatError(f"unknown thrift type {t}")

    def struct(self) -> dict:
        out, fid = {}, 0
        while True:
            h = self.b[self.p]
            self.p += 1
            if h == 0:
                return out
            delta, t = h >> 4, h & 0x0F
            fid = fid + delta if delta else self.zigzag()
            out[fid] = self.value(t)


# --------------------------------------------------------------------------
# HTTP range reads with cached signed CDN URLs
# --------------------------------------------------------------------------
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_no_redirect = urllib.request.build_opener(_NoRedirect)


class RemoteFile:
    """A file on the HF Hub, read by byte range. Thread-safe."""

    SIGNED_TTL_S = 15 * 60

    def __init__(self, repo: str, path: str, token: str | None = None):
        self.url = HF_BASE.format(repo=repo, path=path)
        self.token = token
        self._signed: tuple[str, float] | None = None
        self._lock = threading.Lock()
        self.size: int | None = None

    def _resolve(self) -> str:
        with self._lock:
            if self._signed and time.time() - self._signed[1] < self.SIGNED_TTL_S:
                return self._signed[0]
            req = urllib.request.Request(self.url, method="HEAD", headers={"User-Agent": USER_AGENT})
            if self.token:
                req.add_header("Authorization", f"Bearer {self.token}")
            try:
                r = _no_redirect.open(req, timeout=60)
                loc = self.url  # served directly, no redirect
                size = r.headers.get("Content-Length")
            except urllib.error.HTTPError as e:
                if e.code not in (301, 302, 303, 307, 308):
                    raise
                loc = e.headers["Location"]
                size = e.headers.get("X-Linked-Size") or None
            if size and self.size is None:
                self.size = int(size)
            self._signed = (loc, time.time())
            return loc

    CHUNK = 16 * 2**20

    def read(self, start: int, end: int, retries: int = 5) -> bytes:
        """Bytes [start, end). Negative start reads the last -start bytes."""
        if start >= 0 and end - start > self.CHUNK:  # large reads in chunks, each retried
            return b"".join(self.read(s, min(s + self.CHUNK, end), retries) for s in range(start, end, self.CHUNK))
        rng = f"bytes={start}" if start < 0 else f"bytes={start}-{end - 1}"
        for attempt in range(retries):
            try:
                req = urllib.request.Request(self._resolve(), headers={"Range": rng, "User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=120) as r:
                    data = r.read()
                    if r.status != 206:
                        raise ParquetFormatError(f"server ignored Range (HTTP {r.status})")
                    return data
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                code = getattr(e, "code", None)
                if code in (401, 403):
                    with self._lock:
                        self._signed = None  # signed URL expired
                if code == 404 or attempt == retries - 1:
                    raise
                time.sleep(min(30, 2 ** attempt + (5 if code == 429 else 0)))
        raise RuntimeError("unreachable")


# --------------------------------------------------------------------------
# Footer -> compact row-group index
# --------------------------------------------------------------------------
def read_footer_index(f: RemoteFile) -> list[dict]:
    """Row groups with first_row, num_rows, video_id min/max and per-column offsets."""
    tail = f.read(-65536, 0)
    if tail[-4:] != b"PAR1":
        raise ParquetFormatError("not a parquet file")
    flen = struct.unpack("<I", tail[-8:-4])[0]
    if flen + 8 > len(tail):
        tail = f.read(-(flen + 8), 0)
    meta = _Thrift(tail[len(tail) - 8 - flen:len(tail) - 8]).struct()
    rgs, first = [], 0
    for rg in meta[4]:
        cols = {}
        for cc in rg[1]:
            md = cc[3]
            name = ".".join(p.decode() for p in md[3])
            stats = md.get(12, {})
            cols[name] = {
                "codec": md[4],
                "data_page_offset": md[9],
                "dictionary_page_offset": md.get(11),
                "total_compressed_size": md[7],
                "offset_index_offset": cc.get(4),
                "offset_index_length": cc.get(5),
                "min": (stats.get(6) or stats.get(2) or b"").decode("utf-8", "replace"),
                "max": (stats.get(5) or stats.get(1) or b"").decode("utf-8", "replace"),
            }
        vid = cols["video_id"]
        rgs.append({"first_row": first, "num_rows": rg[3], "vid_min": vid["min"], "vid_max": vid["max"], "cols": cols})
        first += rg[3]
    return rgs


# --------------------------------------------------------------------------
# Page decoding
# --------------------------------------------------------------------------
def _rle_hybrid(buf: bytes, pos: int, end: int, bit_width: int, count: int) -> list[int]:
    out: list[int] = []
    t = _Thrift(buf, pos)
    nbytes = (bit_width + 7) // 8
    while len(out) < count and t.p < end:
        h = t.varint()
        if h & 1:  # bit-packed groups of 8
            ngroups = h >> 1
            raw = int.from_bytes(buf[t.p:t.p + ngroups * bit_width], "little")
            t.p += ngroups * bit_width
            mask = (1 << bit_width) - 1
            out.extend((raw >> (i * bit_width)) & mask for i in range(ngroups * 8))
        else:  # RLE run
            v = int.from_bytes(buf[t.p:t.p + nbytes], "little")
            t.p += nbytes
            out.extend([v] * (h >> 1))
    return out[:count]


def _plain_byte_arrays(buf: bytes, pos: int, n: int) -> list[bytes]:
    out = []
    for _ in range(n):
        ln = struct.unpack_from("<I", buf, pos)[0]
        pos += 4
        out.append(bytes(buf[pos:pos + ln]))
        pos += ln
    return out


def _decompress(data: bytes, codec: int, size: int) -> bytes:
    name = CODECS.get(codec, "?")
    if name is None:
        return data
    if name == "?":
        raise ParquetFormatError(f"unsupported codec {codec}")
    return pa.decompress(data, decompressed_size=size, codec=name).to_pybytes()


def decode_pages(buf: bytes, codec: int, max_def: int, dictionary: list[bytes] | None = None):
    """Decode consecutive pages in buf. Returns (rows, dictionary).

    rows holds one entry per row (bytes, or None for a null)."""
    rows: list[bytes | None] = []
    pos = 0
    bw = max_def.bit_length()
    while pos < len(buf):
        t = _Thrift(buf, pos)
        hdr = t.struct()
        body_start = t.p
        ptype, usize, csize = hdr[1], hdr[2], hdr[3]
        body = buf[body_start:body_start + csize]
        if len(body) < csize:
            raise ParquetFormatError("truncated page")
        pos = body_start + csize
        if ptype == 2:  # dictionary page (PLAIN)
            n = hdr[7][1]
            dictionary = _plain_byte_arrays(_decompress(body, codec, usize), 0, n)
            continue
        if ptype == 0:  # data page v1: levels + values all compressed
            dph = hdr[5]
            n, enc = dph[1], dph[2]
            data = _decompress(body, codec, usize)
            p = 0
            if max_def:
                ln = struct.unpack_from("<I", data, 0)[0]
                defs = _rle_hybrid(data, 4, 4 + ln, bw, n)
                p = 4 + ln
            else:
                defs = [0] * n
        elif ptype == 3:  # data page v2: levels uncompressed
            dph = hdr[8]
            n, enc = dph[1], dph[4]
            dl, rl = dph[5], dph[6]
            defs = _rle_hybrid(body, rl, rl + dl, bw, n) if max_def else [0] * n
            vals = body[rl + dl:]
            data = _decompress(vals, codec, usize - rl - dl) if dph.get(7, True) else vals
            p = 0
        else:
            raise ParquetFormatError(f"unsupported page type {ptype}")
        nvals = sum(1 for d in defs if d == max_def)
        if enc == 0:  # PLAIN
            values = _plain_byte_arrays(data, p, nvals)
        elif enc in (2, 8):  # PLAIN_DICTIONARY / RLE_DICTIONARY
            if dictionary is None:
                raise ParquetFormatError("dictionary page missing")
            idx = _rle_hybrid(data, p + 1, len(data), data[p], nvals)
            values = [dictionary[i] for i in idx]
        else:
            raise ParquetFormatError(f"unsupported encoding {enc}")
        it = iter(values)
        rows.extend(next(it) if d == max_def else None for d in defs)
    return rows, dictionary


def read_column_chunk(f: RemoteFile, col: dict, max_def: int) -> list[bytes | None]:
    """Whole column chunk (use only for small columns such as video_id)."""
    start = col["dictionary_page_offset"] or col["data_page_offset"]
    buf = f.read(start, start + col["total_compressed_size"])
    return decode_pages(buf, col["codec"], max_def)[0]


def read_offset_index(f: RemoteFile, col: dict) -> list[tuple[int, int, int]]:
    """[(offset, compressed_page_size, first_row_index)] for the column's data pages."""
    if not col["offset_index_offset"]:
        raise ParquetFormatError("no offset index")
    o = col["offset_index_offset"]
    s = _Thrift(f.read(o, o + col["offset_index_length"])).struct()
    return [(pl[1], pl[2], pl[3]) for pl in s[1]]


def read_cell(f: RemoteFile, col: dict, row_in_group: int, max_def: int, offset_index=None) -> bytes | None:
    """One value of a (large) column, fetching only the page that holds it."""
    locs = offset_index or read_offset_index(f, col)
    i = max(k for k, loc in enumerate(locs) if loc[2] <= row_in_group)
    off, size, first = locs[i]
    buf = f.read(off, off + size)
    dictionary = None
    if col["dictionary_page_offset"] and _page_encoding(buf) in (2, 8):
        d0 = col["dictionary_page_offset"]
        dictionary = decode_pages(f.read(d0, col["data_page_offset"]), col["codec"], max_def)[1]
    rows, _ = decode_pages(buf, col["codec"], max_def, dictionary)
    return rows[row_in_group - first]


def _page_encoding(buf: bytes) -> int:
    hdr = _Thrift(buf).struct()
    return hdr[5][2] if hdr[1] == 0 else hdr[8][4]
