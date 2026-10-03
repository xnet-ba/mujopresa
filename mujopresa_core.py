#!/usr/bin/env python3
"""
MujoPresa Core – LZ77 + Range Coding + Order-1 Context Model
=============================================================
Stable compression engine. Used by muj.py archiver.
"""

from typing import Tuple

CYTHON_ACTIVE = False

__version__ = "1.4.0"

# -----------------------------------------------------------------
# Constants
# -----------------------------------------------------------------
MIN_MATCH = 4
MAX_LEN = 511
DIST_BUCKETS = 24

LITERAL = 0
MATCH = 1

# -----------------------------------------------------------------
# Range Coder (CACM87, infinite precision)
# Python big ints – no 32-bit overflow issues.
# -----------------------------------------------------------------
RC_TOP = 1 << 24
RC_BOTTOM = 1 << 16
RC_HALF = 1 << 31
RC_QUARTER = 1 << 30
RC_MASK = 0xFFFFFFFF


class RangeEncoder:
    """CACM87 arithmetic encoder with byte-packed output."""
    def __init__(self):
        self.low = 0
        self.high = RC_MASK
        self.pending = 0
        self.buf = bytearray()
        self.acc = 0
        self.n = 0

    def encode(self, cum_freq: int, freq: int, tot_freq: int):
        r = self.high - self.low + 1
        self.high = self.low + (r * (cum_freq + freq)) // tot_freq - 1
        self.low = self.low + (r * cum_freq) // tot_freq
        self._renorm()

    def _renorm(self):
        low = self.low
        high = self.high
        pending = self.pending
        buf = self.buf
        acc = self.acc
        n = self.n
        while True:
            if high < RC_HALF:
                bit = 0
                inv = 1
            elif low >= RC_HALF:
                bit = 1
                inv = 0
                low -= RC_HALF
                high -= RC_HALF
            elif low >= RC_QUARTER and high < 3 * RC_QUARTER:
                pending += 1
                low -= RC_QUARTER
                high -= RC_QUARTER
                low <<= 1
                high = (high << 1) | 1
                continue
            else:
                break
            acc = (acc << 1) | bit
            n += 1
            if n == 8:
                buf.append(acc)
                acc = 0
                n = 0
            for _ in range(pending):
                acc = (acc << 1) | inv
                n += 1
                if n == 8:
                    buf.append(acc)
                    acc = 0
                    n = 0
            pending = 0
            low <<= 1
            high = (high << 1) | 1
        self.low = low
        self.high = high
        self.pending = pending
        self.acc = acc
        self.n = n

    def _pack_bit(self, bit: int):
        self.acc = (self.acc << 1) | bit
        self.n += 1
        if self.n == 8:
            self.buf.append(self.acc)
            self.acc = 0
            self.n = 0

    def finish(self) -> bytes:
        self.pending += 1
        if self.low < RC_QUARTER:
            self._pack_bit(0)
            while self.pending:
                self._pack_bit(1)
                self.pending -= 1
        else:
            self._pack_bit(1)
            while self.pending:
                self._pack_bit(0)
                self.pending -= 1
        if self.n:
            self.buf.append(self.acc << (8 - self.n))
        return bytes(self.buf)


class RangeDecoder:
    """CACM87 arithmetic decoder."""
    def __init__(self, data: bytes):
        self.low = 0
        self.high = RC_MASK
        self.code = 0
        self.data = data
        self.byte_pos = 0
        self.bit_pos = 0
        for _ in range(4):
            self.code = ((self.code << 8) | self._read_byte()) & RC_MASK

    def _read_byte(self) -> int:
        if self.byte_pos < len(self.data):
            b = self.data[self.byte_pos]
            self.byte_pos += 1
            return b
        return 0

    def get_freq(self, tot_freq: int) -> int:
        r = self.high - self.low + 1
        return ((self.code - self.low + 1) * tot_freq - 1) // r

    def decode(self, cum_freq: int, freq: int, tot_freq: int):
        r = self.high - self.low + 1
        self.high = self.low + (r * (cum_freq + freq)) // tot_freq - 1
        self.low = self.low + (r * cum_freq) // tot_freq
        self._renorm()

    def _renorm(self):
        low = self.low
        high = self.high
        code = self.code
        data = self.data
        byte_pos = self.byte_pos
        bit_pos = self.bit_pos
        n = len(data)
        while True:
            if high < RC_HALF:
                pass
            elif low >= RC_HALF:
                code -= RC_HALF
                low -= RC_HALF
                high -= RC_HALF
            elif low >= RC_QUARTER and high < 3 * RC_QUARTER:
                code -= RC_QUARTER
                low -= RC_QUARTER
                high -= RC_QUARTER
            else:
                break
            low <<= 1
            high = (high << 1) | 1
            if byte_pos >= n:
                bit = 0
            else:
                bit = (data[byte_pos] >> (7 - bit_pos)) & 1
                bit_pos += 1
                if bit_pos >= 8:
                    bit_pos = 0
                    byte_pos += 1
            code = ((code << 1) & RC_MASK) | bit
        self.low = low
        self.high = high
        self.code = code
        self.byte_pos = byte_pos
        self.bit_pos = bit_pos


# -----------------------------------------------------------------
# Fenwick Tree Model
# -----------------------------------------------------------------
class FenwickModel:
    __slots__ = ('size', 'tree', 'total', 'max_total', 'increment', '_bitmask_start')
    def __init__(self, size: int, max_total: int = 8192, increment: int = 16):
        self.size = size
        self.tree = [0] * (size + 1)
        self.total = size
        self.max_total = min(max_total, 65535)
        self.increment = increment
        for i in range(size):
            self._add(i, 1)
        bm = 1
        while bm * 2 <= size:
            bm *= 2
        self._bitmask_start = bm

    def _add(self, symbol: int, delta: int):
        i = symbol + 1
        n = self.size
        tree = self.tree
        while i <= n:
            tree[i] += delta
            i += i & (-i)

    def _prefix_sum(self, i: int) -> int:
        if i > self.size:
            i = self.size
        s = 0
        tree = self.tree
        while i > 0:
            s += tree[i]
            i -= i & (-i)
        return s

    def cum_and_freq(self, symbol: int) -> Tuple[int, int]:
        cum = self._prefix_sum(symbol)
        freq = self._prefix_sum(symbol + 1) - cum
        return cum, freq

    def find(self, target: int) -> Tuple[int, int, int]:
        if target < 0:
            target = 0
        if target >= self.total:
            target = self.total - 1
        idx = 0
        bitmask = self._bitmask_start
        tree = self.tree
        size = self.size
        remaining = target
        while bitmask:
            nxt = idx + bitmask
            if nxt <= size and tree[nxt] <= remaining:
                idx = nxt
                remaining -= tree[nxt]
            bitmask >>= 1
        symbol = idx
        if symbol >= size:
            symbol = size - 1
            cum = self._prefix_sum(symbol)
        else:
            cum = target - remaining
        freq = self._prefix_sum(symbol + 1) - cum
        return symbol, cum, freq

    def update(self, symbol: int):
        i = symbol + 1
        n = self.size
        tree = self.tree
        delta = self.increment
        while i <= n:
            tree[i] += delta
            i += i & (-i)
        self.total += delta
        if self.total > (self.max_total * 4):
            self._half_life()

    def _half_life(self):
        freqs = [0] * self.size
        for i in range(self.size):
            freqs[i] = self._prefix_sum(i + 1) - self._prefix_sum(i)
        self.tree = [0] * (self.size + 1)
        self.total = 0
        for i, f in enumerate(freqs):
            nf = (f >> 1) or 1
            self._add(i, nf)
            self.total += nf


# -----------------------------------------------------------------
# LZ77 Hash-Chain Matcher
# -----------------------------------------------------------------
def _hash4(data: bytes, i: int) -> int:
    return ((data[i] << 24) | (data[i+1] << 16) | (data[i+2] << 8) | data[i+3]) * 2654435761 & 0xFFFFFFFF


class LZMatcher:
    __slots__ = ('data', 'mv', 'n', 'window', 'max_chain', 'head', 'prev')
    def __init__(self, data: bytes, window: int, max_chain: int):
        self.data = data
        self.mv = memoryview(data)
        self.n = len(data)
        self.window = window
        self.max_chain = max_chain
        self.head = {}
        self.prev = [-1] * self.n if self.n else []

    def insert(self, i: int):
        data = self.data
        if i + 4 <= self.n:
            h = ((data[i] << 24) | (data[i+1] << 16) | (data[i+2] << 8) | data[i+3]) * 2654435761 & 0xFFFFFFFF
            self.prev[i] = self.head.get(h, -1)
            self.head[h] = i

    def find_match(self, pos: int) -> Tuple[int, int]:
        n = self.n
        data = self.data
        mv = self.mv
        if pos + 4 > n:
            return 0, 0
        h = ((data[pos] << 24) | (data[pos+1] << 16) | (data[pos+2] << 8) | data[pos+3]) * 2654435761 & 0xFFFFFFFF
        candidate = self.head.get(h, -1)
        best_len = 0
        best_dist = 0
        chain = 0
        limit = MAX_LEN
        if n - pos < limit:
            limit = n - pos
        min_candidate = pos - self.window
        prev = self.prev
        max_chain = self.max_chain
        while candidate >= 0 and candidate >= min_candidate and chain < max_chain:
            if best_len == 0 or (candidate + best_len < n and mv[candidate + best_len] == mv[pos + best_len]):
                l = 0
                while l < limit:
                    end = l + 8
                    if end > limit:
                        end = limit
                    if mv[candidate+l:candidate+end] == mv[pos+l:pos+end]:
                        l = end
                    else:
                        while l < end and mv[candidate+l] == mv[pos+l]:
                            l += 1
                        break
                if l > best_len:
                    best_len = l
                    best_dist = pos - candidate
                    if best_len >= limit:
                        break
            candidate = prev[candidate]
            chain += 1
        return best_len, best_dist

    def find_match_lazy(self, pos: int) -> Tuple[int, int, bool]:
        best_len, best_dist = self.find_match(pos)
        if best_len >= MIN_MATCH and pos + 1 < self.n:
            next_len, _ = self.find_match(pos + 1)
            if next_len > best_len:
                return best_len, best_dist, True
        return best_len, best_dist, False


# -----------------------------------------------------------------
# Context Model (order-1)
# -----------------------------------------------------------------
class ContextModel:
    __slots__ = ('order', 'tables', 'history', 'last')
    def __init__(self, order: int = 1):
        self.order = order
        self.tables = {}
        self.history = bytearray(order)
        self.last = 0

    def key(self):
        if not self.order:
            return 0
        if self.order == 1:
            return self.last
        return bytes(self.history)

    def table(self):
        if self.order == 1:
            k = self.last
        elif self.order:
            k = bytes(self.history)
        else:
            k = 0
        t = self.tables.get(k)
        if t is None:
            t = FenwickModel(256, max_total=65536, increment=1)
            self.tables[k] = t
        return t

    def update(self, symbol: int):
        if self.order == 1:
            self.last = symbol
            self.history[0] = symbol
        elif self.order:
            self.history.pop(0)
            self.history.append(symbol)


# -----------------------------------------------------------------
# Aggregate models for token coding
# -----------------------------------------------------------------
class Models:
    __slots__ = ('literal', 'flag', 'length', 'dist_bucket',
                 'rep_flag', 'rep_index', 'rep_cache')
    def __init__(self):
        self.literal = ContextModel(1)
        self.flag = FenwickModel(2, max_total=32768, increment=1)
        self.length = FenwickModel(508, max_total=65536, increment=1)
        self.dist_bucket = FenwickModel(DIST_BUCKETS, max_total=32768, increment=1)
        self.rep_flag = FenwickModel(2, max_total=32768, increment=1)
        self.rep_index = FenwickModel(4, max_total=32768, increment=1)
        self.rep_cache = [0, 0, 0, 0]

    def update_rep_cache(self, dist: int):
        if dist in self.rep_cache:
            self.rep_cache.remove(dist)
        self.rep_cache.insert(0, dist)
        if len(self.rep_cache) > 4:
            self.rep_cache.pop()

    def get_rep_index(self, dist: int) -> int:
        try:
            return self.rep_cache.index(dist)
        except ValueError:
            return -1


# -----------------------------------------------------------------
# Encoding / Decoding wrappers
# -----------------------------------------------------------------
def encode_symbol(encoder: RangeEncoder, model: FenwickModel, symbol: int):
    cum, freq = model.cum_and_freq(symbol)
    encoder.encode(cum, freq, model.total)
    model.update(symbol)

def decode_symbol(decoder: RangeDecoder, model: FenwickModel) -> int:
    tot = model.total
    target = decoder.get_freq(tot)
    symbol, cum, freq = model.find(target)
    decoder.decode(cum, freq, tot)
    model.update(symbol)
    return symbol

def literal_model_encode(encoder: RangeEncoder, model: ContextModel, byte_val: int):
    tbl = model.table()
    cum, freq = tbl.cum_and_freq(byte_val)
    encoder.encode(cum, freq, tbl.total)
    tbl.update(byte_val)
    model.update(byte_val)

def literal_model_decode(decoder: RangeDecoder, model: ContextModel) -> int:
    tbl = model.table()
    tot = tbl.total
    target = decoder.get_freq(tot)
    symbol, cum, freq = tbl.find(target)
    decoder.decode(cum, freq, tot)
    tbl.update(symbol)
    model.update(symbol)
    return symbol


# -----------------------------------------------------------------
# Compression kernel
# -----------------------------------------------------------------
def compress_bytes(data: bytes, window_bits: int, max_chain: int = 32,
                   use_lazy: bool = True, use_repcache: bool = True,
                   progress_callback=None) -> bytes:
    n = len(data)
    models = Models()
    encoder = RangeEncoder()
    window = 1 << window_bits
    matcher = LZMatcher(data, window, max_chain)

    pos = 0
    while pos < n:
        if progress_callback and pos % 1024 == 0:
            progress_callback(pos, n)

        match_len, match_dist, lazy_flag = matcher.find_match_lazy(pos) if use_lazy else (0, 0, False)

        if lazy_flag:
            encode_symbol(encoder, models.rep_flag, 0)
            encode_symbol(encoder, models.flag, LITERAL)
            literal_model_encode(encoder, models.literal, data[pos])
            matcher.insert(pos)
            pos += 1
            match_len, match_dist = matcher.find_match(pos)

        if match_len >= MIN_MATCH:
            rep_idx = -1
            if use_repcache and match_dist > 0:
                rep_idx = models.get_rep_index(match_dist)
            if rep_idx >= 0:
                encode_symbol(encoder, models.rep_flag, 1)
                encode_symbol(encoder, models.rep_index, rep_idx)
                encode_symbol(encoder, models.length, match_len - MIN_MATCH)
                if use_repcache:
                    models.update_rep_cache(match_dist)
            else:
                encode_symbol(encoder, models.rep_flag, 0)
                encode_symbol(encoder, models.flag, MATCH)
                encode_symbol(encoder, models.length, match_len - MIN_MATCH)
                b = match_dist.bit_length() - 1
                if b >= DIST_BUCKETS:
                    b = DIST_BUCKETS - 1
                encode_symbol(encoder, models.dist_bucket, b)
                if b > 0:
                    extra = match_dist - (1 << b)
                    encoder.encode(extra, 1, 1 << b)
                if use_repcache:
                    models.update_rep_cache(match_dist)

            if match_len < 32:
                for i in range(pos, pos + match_len):
                    matcher.insert(i)
            else:
                for i in range(pos, pos + 8):
                    matcher.insert(i)
                for i in range(pos + 8, pos + match_len - 8, 2):
                    matcher.insert(i)
                for i in range(pos + match_len - 8, pos + match_len):
                    matcher.insert(i)
            pos += match_len
        else:
            encode_symbol(encoder, models.rep_flag, 0)
            encode_symbol(encoder, models.flag, LITERAL)
            literal_model_encode(encoder, models.literal, data[pos])
            matcher.insert(pos)
            pos += 1

    if progress_callback:
        progress_callback(n, n)
    return encoder.finish()


def decompress_bytes(payload: bytes, original_size: int, window_bits: int) -> bytearray:
    models = Models()
    decoder = RangeDecoder(payload)
    out = bytearray()
    out_len = 0

    while out_len < original_size:
        rep_flag = decode_symbol(decoder, models.rep_flag)
        if rep_flag == 1:
            rep_idx = decode_symbol(decoder, models.rep_index)
            distance = models.rep_cache[rep_idx]
            if distance <= 0:
                distance = 1
            length_sym = decode_symbol(decoder, models.length)
            length = length_sym + MIN_MATCH
            models.update_rep_cache(distance)
        else:
            flag = decode_symbol(decoder, models.flag)
            if flag == LITERAL:
                out.append(literal_model_decode(decoder, models.literal))
                out_len += 1
                continue
            else:
                length_sym = decode_symbol(decoder, models.length)
                length = length_sym + MIN_MATCH
                b = decode_symbol(decoder, models.dist_bucket)
                if b > 0:
                    tot_extra = 1 << b
                    target_extra = decoder.get_freq(tot_extra)
                    decoder.decode(target_extra, 1, tot_extra)
                    extra = target_extra
                else:
                    extra = 0
                distance = (1 << b) + extra
                if distance <= 0:
                    distance = 1
                models.update_rep_cache(distance)

        start = out_len - distance
        if start < 0:
            raise ValueError(f"Distance {distance} > output size {out_len}")
        if distance >= length:
            end = start + length
            if end > out_len:
                raise ValueError("Match out of bounds")
            out.extend(out[start:end])
        else:
            for i in range(length):
                if start + i >= out_len:
                    raise ValueError("Match out of bounds")
                out.append(out[start + i])
                out_len += 1
            continue
        out_len += length

    return out
