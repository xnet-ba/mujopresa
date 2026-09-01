#!/usr/bin/env python3
"""
MujoPresa: LZ77 + Adaptivni Order-N Statisticki Lossless Kompresor
=====================================================================
Jedan fajl. Ako je Cython + C kompajler dostupan, automatski se
kompajlira i koristi ubrzano C jezgro (desetine puta brze). Ako nije,
tiho pada nazad na cist Python (ista logika, isti format arhive -
arhive su medjusobno kompatibilne bez obzira koji backend ih napravio).
"""

import os
import sys
import struct

# =====================================================================
# CYTHON UBRZANO JEZGRO (opciono) - izvor ugradjen kao string, kompajlira
# se u kes direktorij pri prvom pokretanju preko pyximport-a.
# =====================================================================

_PYX_SOURCE = r'''# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True
from libc.stdlib cimport malloc, realloc, free
from libc.string cimport memset, memcpy
from cpython.bytes cimport PyBytes_FromStringAndSize
from cpython.bytearray cimport PyByteArray_FromStringAndSize

DEF MASK32 = 0xFFFFFFFF
DEF TOP = 1 << 24
DEF BOTTOM = 1 << 16
DEF MIN_MATCH = 4
DEF MAX_LEN = MIN_MATCH + 255
DEF DIST_BUCKETS = 16
DEF HASH_BITS = 19
DEF HASH_SIZE = 1 << HASH_BITS
DEF HASH_MASK = HASH_SIZE - 1

# ---------------------------------------------------------------
# Rastuci C bajt bafer
# ---------------------------------------------------------------
cdef class ByteBuf:
    cdef unsigned char* data
    cdef Py_ssize_t size
    cdef Py_ssize_t cap

    def __cinit__(self, Py_ssize_t initial=4096):
        self.cap = initial
        self.data = <unsigned char*> malloc(self.cap)
        self.size = 0

    def __dealloc__(self):
        if self.data != NULL:
            free(self.data)

    cdef inline void push(self, unsigned char b):
        if self.size >= self.cap:
            self.cap <<= 1
            self.data = <unsigned char*> realloc(self.data, self.cap)
        self.data[self.size] = b
        self.size += 1

    cdef bytes to_bytes(self):
        return PyBytes_FromStringAndSize(<char*> self.data, self.size)


# ---------------------------------------------------------------
# Carryless range coder (Subbotin stil)
# ---------------------------------------------------------------
cdef class RangeEncoder:
    cdef unsigned int low, rng
    cdef ByteBuf out

    def __cinit__(self):
        self.low = 0
        self.rng = MASK32
        self.out = ByteBuf()

    cdef inline void encode(self, unsigned int cum, unsigned int freq, unsigned int tot):
        self.rng = self.rng // tot
        self.low = (self.low + cum * self.rng) & MASK32
        self.rng = (self.rng * freq) & MASK32
        self._normalize()

    cdef inline void _normalize(self):
        while True:
            if ((self.low ^ (self.low + self.rng)) & MASK32) < TOP:
                pass
            elif self.rng < BOTTOM:
                self.rng = (<unsigned int>(-<int>self.low)) & (BOTTOM - 1)
            else:
                break
            self.out.push((self.low >> 24) & 0xFF)
            self.low = (self.low << 8) & MASK32
            self.rng = (self.rng << 8) & MASK32

    cdef bytes finish(self):
        cdef int i
        for i in range(4):
            self.out.push((self.low >> 24) & 0xFF)
            self.low = (self.low << 8) & MASK32
        return self.out.to_bytes()


cdef class RangeDecoder:
    cdef const unsigned char* data
    cdef Py_ssize_t pos, n
    cdef unsigned int low, rng, code

    def __cinit__(self, const unsigned char[:] buf):
        self.data = &buf[0] if buf.shape[0] > 0 else NULL
        self.n = buf.shape[0]
        self.pos = 0
        self.low = 0
        self.rng = MASK32
        self.code = 0
        cdef int i
        for i in range(4):
            self.code = ((self.code << 8) | self._next()) & MASK32

    cdef inline unsigned char _next(self):
        cdef unsigned char b
        if self.pos < self.n:
            b = self.data[self.pos]
            self.pos += 1
            return b
        return 0

    cdef inline unsigned int get_freq(self, unsigned int tot):
        self.rng = self.rng // tot
        if self.rng == 0:
            self.rng = 1
        return ((self.code - self.low) & MASK32) // self.rng

    cdef inline void decode(self, unsigned int cum, unsigned int freq, unsigned int tot):
        self.low = (self.low + cum * self.rng) & MASK32
        self.rng = (self.rng * freq) & MASK32
        self._normalize()

    cdef inline void _normalize(self):
        cdef int _guard = 0
        while True:
            if ((self.low ^ (self.low + self.rng)) & MASK32) < TOP:
                pass
            elif self.rng < BOTTOM:
                self.rng = (<unsigned int>(-<int>self.low)) & (BOTTOM - 1)
                if self.rng == 0:
                    self.rng = 1  # sprjecava zaglavljivanje na rng=0 (korumpiran stream)
            else:
                break
            self.code = ((self.code << 8) | self._next()) & MASK32
            self.low = (self.low << 8) & MASK32
            self.rng = (self.rng << 8) & MASK32
            _guard += 1
            if _guard > 64:
                break  # korumpiran stream - prekini umjesto beskonacne petlje


# ---------------------------------------------------------------
# Fenwick (BIT) adaptivni model - C niz, O(log alfabet)
# ---------------------------------------------------------------
cdef class Fenwick:
    cdef unsigned short* tree
    cdef unsigned short* freqs
    cdef int size
    cdef unsigned int total, max_total
    cdef int increment
    cdef int bitmask_start

    def __cinit__(self, int size, unsigned int max_total, int increment):
        self.size = size
        self.tree = <unsigned short*> malloc((size + 1) * sizeof(unsigned short))
        self.freqs = <unsigned short*> malloc(size * sizeof(unsigned short))
        cdef int i
        self.tree[0] = 0
        for i in range(1, size + 1):
            # zatvorena formula: Fenwick stablo uniformnog niza jedinica
            # ima tree[i] = i & (-i) (najnizi postavljeni bit) - O(n) umjesto O(n log n)
            self.tree[i] = <unsigned short> (i & (-i))
        for i in range(size):
            self.freqs[i] = 1
        self.total = size
        self.max_total = max_total
        self.increment = increment
        self.bitmask_start = 1
        while self.bitmask_start * 2 <= size:
            self.bitmask_start *= 2

    def __dealloc__(self):
        if self.tree != NULL:
            free(self.tree)
        if self.freqs != NULL:
            free(self.freqs)

    cdef inline void _add(self, int symbol, int delta):
        cdef int i = symbol + 1
        while i <= self.size:
            self.tree[i] += delta
            i += i & (-i)

    cdef inline int _prefix_sum(self, int i):
        cdef int s = 0
        while i > 0:
            s += self.tree[i]
            i -= i & (-i)
        return s

    cdef inline void cum_and_freq(self, int symbol, unsigned int* cum, unsigned int* freq):
        cum[0] = <unsigned int> self._prefix_sum(symbol)
        freq[0] = <unsigned int> self.freqs[symbol]

    cdef inline int find(self, unsigned int target, unsigned int* cum, unsigned int* freq):
        cdef int idx = 0
        cdef int remaining = <int> target
        cdef int bitmask = self.bitmask_start
        cdef int next_idx
        while bitmask > 0:
            next_idx = idx + bitmask
            if next_idx <= self.size and self.tree[next_idx] <= remaining:
                idx = next_idx
                remaining -= self.tree[next_idx]
            bitmask >>= 1
        cum[0] = target - <unsigned int> remaining
        freq[0] = <unsigned int> self.freqs[idx]
        return idx

    cdef void update(self, int symbol):
        self._add(symbol, self.increment)
        self.freqs[symbol] += self.increment
        self.total += <unsigned int> self.increment
        cdef int i, nf, new_total
        if self.total > self.max_total:
            for i in range(self.size + 1):
                self.tree[i] = 0
            new_total = 0
            for i in range(self.size):
                nf = self.freqs[i] >> 1
                if nf == 0:
                    nf = 1
                self.freqs[i] = nf
                self._add(i, nf)
                new_total += nf
            self.total = <unsigned int> new_total


# ---------------------------------------------------------------
# Kompletan compress/decompress pipeline (C brzina)
# ---------------------------------------------------------------

DEF LITERAL = 0
DEF MATCH = 1
DEF REP_MATCH = 2
DEF REP_BIAS = 1
DEF NUM_REP = 6
DEF INSERT_STEP = 1

cdef inline void rep_use(int* cache, int idx):
    cdef int dist = cache[idx]
    cdef int k
    for k in range(idx, 0, -1):
        cache[k] = cache[k - 1]
    cache[0] = dist

cdef inline void rep_add(int* cache, int dist):
    cdef int k, found = -1
    for k in range(NUM_REP):
        if cache[k] == dist:
            found = k
            break
    if found >= 0:
        rep_use(cache, found)
    else:
        for k in range(NUM_REP - 1, 0, -1):
            cache[k] = cache[k - 1]
        cache[0] = dist

cdef inline unsigned int hash4(const unsigned char* d, Py_ssize_t i):
    cdef unsigned int h = (<unsigned int> d[i] << 24) | (<unsigned int> d[i+1] << 16) | \
                           (<unsigned int> d[i+2] << 8) | (<unsigned int> d[i+3])
    h = (h * 2654435761U) & MASK32
    return h & HASH_MASK


cdef inline int match_length(const unsigned char* d, Py_ssize_t a, Py_ssize_t b, int limit):
    cdef int l = 0
    cdef const unsigned long long* pa
    cdef const unsigned long long* pb
    while l + 8 <= limit:
        pa = <const unsigned long long*> (d + a + l)
        pb = <const unsigned long long*> (d + b + l)
        if pa[0] != pb[0]:
            while l < limit and d[a + l] == d[b + l]:
                l += 1
            return l
        l += 8
    while l < limit and d[a + l] == d[b + l]:
        l += 1
    return l


cdef inline bint quick4(const unsigned char* d, Py_ssize_t a, Py_ssize_t b, Py_ssize_t n):
    if a + 4 <= n and b + 4 <= n:
        return (<unsigned int*> (d + a))[0] == (<unsigned int*> (d + b))[0]
    elif a < n and b < n:
        return d[a] == d[b]
    return False


cdef inline int find_match(const unsigned char* d, Py_ssize_t n, int* head, int* prev,
                            Py_ssize_t window, int max_chain, Py_ssize_t pos, int* out_dist, unsigned int* out_hash):
    cdef unsigned int h
    cdef Py_ssize_t candidate, min_candidate
    cdef int best_len = 0, best_dist = 0, chain = 0, limit, l
    if pos + 4 > n:
        out_dist[0] = 0
        out_hash[0] = 0
        return 0
    h = hash4(d, pos)
    out_hash[0] = h
    candidate = head[h]
    min_candidate = pos - window
    limit = MAX_LEN
    if n - pos < limit:
        limit = <int>(n - pos)
    while candidate >= 0 and candidate >= min_candidate and chain < max_chain:
        if best_len == 0 or quick4(d, candidate + best_len, pos + best_len, n):
            l = match_length(d, candidate, pos, limit)
            if l > best_len:
                best_len = l
                best_dist = <int>(pos - candidate)
                if best_len >= limit:
                    break
                if best_len >= 32 and chain >= 8:
                    break
        candidate = prev[candidate]
        chain += 1
    out_dist[0] = best_dist
    return best_len


cdef inline int match_at_distance(const unsigned char* d, Py_ssize_t n, Py_ssize_t pos, int dist):
    cdef Py_ssize_t candidate = pos - dist
    cdef int limit
    if candidate < 0 or dist <= 0:
        return 0
    limit = MAX_LEN
    if n - pos < limit:
        limit = <int>(n - pos)
    return match_length(d, candidate, pos, limit)


def compress_bytes(data_py, int order, int window_bits, int max_chain, bint skip_literal_in_match=True, history_py=b""):
    cdef Py_ssize_t hist_len = len(history_py)
    cdef bytes combined = bytes(history_py) + bytes(data_py)
    cdef const unsigned char[:] data = combined
    cdef Py_ssize_t n = data.shape[0]
    cdef const unsigned char* d = &data[0] if n > 0 else NULL
    cdef Py_ssize_t window = 1 << window_bits

    cdef int* head = <int*> malloc(HASH_SIZE * sizeof(int))
    cdef int i
    cdef unsigned int h
    memset(head, 0xFF, HASH_SIZE * sizeof(int))
    cdef int* prev = <int*> malloc((n if n > 0 else 1) * sizeof(int))

    # napuni hash tabelu iz history regiona (samo insert, ne emituje tokene)
    for i in range(hist_len):
        if i + 4 <= n:
            h = hash4(d, i)
            prev[i] = head[h]
            head[h] = <int> i

    encoder = RangeEncoder()
    flag_model = Fenwick(3, 32768, 512)
    length_model = Fenwick(256, 32768, 64)
    dist_model = Fenwick(DIST_BUCKETS, 32768, 64)
    rep_index_model = Fenwick(NUM_REP, 32768, 512)
    cdef dict lit_tables = {}
    order0_table = Fenwick(256, 32768, 48)
    lit_list = [Fenwick(256, 32768, 48) for _ in range(256)] if order == 1 else ([None] * 65536 if order == 2 else [None])

    cdef int rep_cache[6]
    for i in range(NUM_REP):
        rep_cache[i] = 0

    cdef Py_ssize_t pos = hist_len
    cdef unsigned int cum, freq
    cdef int b, out_dist_c
    cdef int best_len, best_dist, nb_len, nb_dist, next_best, cur_len, match_len
    cdef int rep_best_idx, rep_best_len, ri, rl, nrep_len
    cdef unsigned int extra
    cdef bytes ctx_key
    cdef int hist0 = 0, hist1 = 0, lit_idx = 0
    cdef unsigned char sym
    cdef bint use_rep, use_new
    cdef bint have_cached = False
    cdef int cached_len = 0, cached_dist = 0
    cdef bint hash_valid = False
    cdef unsigned int cur_hash = 0, out_hash_c = 0
    cdef bint rep_any = False

    while pos < n:
        rep_best_idx = -1
        rep_best_len = 0
        if rep_any:
            for ri in range(NUM_REP):
                if rep_cache[ri] > 0:
                    rl = match_at_distance(d, n, pos, rep_cache[ri])
                    if rl > rep_best_len:
                        rep_best_len = rl
                        rep_best_idx = ri
                        if rep_best_len >= 64:
                            break

        if rep_best_len >= 32:
            # rep-match je vec dovoljno dobar - preskoci skupu hash-chain pretragu
            best_len = 0
            best_dist = 0
            have_cached = False
            hash_valid = False
        elif have_cached:
            best_len = cached_len
            best_dist = cached_dist
            have_cached = False
            hash_valid = False
        else:
            best_len = find_match(d, n, head, prev, window, max_chain, pos, &out_dist_c, &out_hash_c)
            best_dist = out_dist_c
            hash_valid = (pos + 4 <= n)
            cur_hash = out_hash_c

        use_rep = rep_best_idx >= 0 and rep_best_len >= MIN_MATCH and (rep_best_len + REP_BIAS >= best_len)
        use_new = (not use_rep) and best_len >= MIN_MATCH

        if use_rep or use_new:
            cur_len = rep_best_len if use_rep else best_len

            # ---- lazy matching: pogledaj pos+1 (preskoci ako je match vec dug) ----
            if pos + 1 < n and cur_len < 64:
                nrep_len = 0
                if rep_any:
                    for ri in range(NUM_REP):
                        if rep_cache[ri] > 0:
                            rl = match_at_distance(d, n, pos + 1, rep_cache[ri])
                            if rl > nrep_len:
                                nrep_len = rl
                            if nrep_len >= 64:
                                break
                if nrep_len >= 32:
                    nb_len = 0
                    nb_dist = 0
                else:
                    nb_len = find_match(d, n, head, prev, window, max_chain, pos + 1, &out_dist_c, &out_hash_c)
                    nb_dist = out_dist_c
                next_best = nb_len if nb_len > nrep_len else nrep_len
                if next_best > cur_len:
                    if nb_len >= nrep_len:
                        cached_len = nb_len
                        cached_dist = nb_dist
                        have_cached = True
                    flag_model.cum_and_freq(LITERAL, &cum, &freq)
                    encoder.encode(cum, freq, flag_model.total)
                    flag_model.update(LITERAL)

                    sym = d[pos]
                    if order == 0:
                        fm = order0_table
                    else:
                        lit_idx = hist0 if order == 1 else (hist1 * 256 + hist0)
                        fm = lit_list[lit_idx]
                        if fm is None:
                            fm = Fenwick(256, 32768, 48)
                            lit_list[lit_idx] = fm
                    (<Fenwick> fm).cum_and_freq(sym, &cum, &freq)
                    encoder.encode(cum, freq, (<Fenwick> fm).total)
                    (<Fenwick> fm).update(sym)
                    if order == 2:
                        hist1 = hist0
                    if order >= 1:
                        hist0 = sym

                    if pos + 4 <= n:
                        h = cur_hash if hash_valid else hash4(d, pos)
                        prev[pos] = head[h]
                        head[h] = <int> pos
                    pos += 1
                    continue

            if use_rep:
                flag_model.cum_and_freq(REP_MATCH, &cum, &freq)
                encoder.encode(cum, freq, flag_model.total)
                flag_model.update(REP_MATCH)

                rep_index_model.cum_and_freq(rep_best_idx, &cum, &freq)
                encoder.encode(cum, freq, rep_index_model.total)
                rep_index_model.update(rep_best_idx)

                length_model.cum_and_freq(rep_best_len - MIN_MATCH, &cum, &freq)
                encoder.encode(cum, freq, length_model.total)
                length_model.update(rep_best_len - MIN_MATCH)

                rep_use(rep_cache, rep_best_idx)
                match_len = rep_best_len
            else:
                flag_model.cum_and_freq(MATCH, &cum, &freq)
                encoder.encode(cum, freq, flag_model.total)
                flag_model.update(MATCH)

                length_model.cum_and_freq(best_len - MIN_MATCH, &cum, &freq)
                encoder.encode(cum, freq, length_model.total)
                length_model.update(best_len - MIN_MATCH)

                b = best_dist.bit_length() - 1
                dist_model.cum_and_freq(b, &cum, &freq)
                encoder.encode(cum, freq, dist_model.total)
                dist_model.update(b)
                if b > 0:
                    extra = <unsigned int>(best_dist - (1 << b))
                    encoder.encode(extra, 1, <unsigned int>(1 << b))
                rep_add(rep_cache, best_dist)
                rep_any = True
                match_len = best_len

            for i in range(pos, pos + match_len):
                if i + 4 <= n and (match_len < 8 or (i - pos) % INSERT_STEP == 0):
                    if i == pos and hash_valid:
                        h = cur_hash
                    else:
                        h = hash4(d, i)
                    prev[i] = head[h]
                    head[h] = <int> i
                if not skip_literal_in_match:
                    sym = d[i]
                    if order == 0:
                        fm = order0_table
                    else:
                        lit_idx = hist0 if order == 1 else (hist1 * 256 + hist0)
                        fm = lit_list[lit_idx]
                        if fm is None:
                            fm = Fenwick(256, 32768, 48)
                            lit_list[lit_idx] = fm
                    (<Fenwick> fm).update(sym)
                    if order == 2:
                        hist1 = hist0
                    if order >= 1:
                        hist0 = sym
            pos += match_len
        else:
            flag_model.cum_and_freq(LITERAL, &cum, &freq)
            encoder.encode(cum, freq, flag_model.total)
            flag_model.update(LITERAL)

            sym = d[pos]
            if order == 0:
                fm = order0_table
            else:
                lit_idx = hist0 if order == 1 else (hist1 * 256 + hist0)
                fm = lit_list[lit_idx]
                if fm is None:
                    fm = Fenwick(256, 32768, 48)
                    lit_list[lit_idx] = fm
            (<Fenwick> fm).cum_and_freq(sym, &cum, &freq)
            encoder.encode(cum, freq, (<Fenwick> fm).total)
            (<Fenwick> fm).update(sym)
            if order == 2:
                hist1 = hist0
            if order >= 1:
                hist0 = sym

            if pos + 4 <= n:
                h = cur_hash if hash_valid else hash4(d, pos)
                prev[pos] = head[h]
                head[h] = <int> pos
            pos += 1

    free(head)
    free(prev)
    return encoder.finish()


def decompress_bytes(bytes payload_py, Py_ssize_t original_size, int order, bint skip_literal_in_match=True, history_py=b""):
    cdef const unsigned char[:] payload = payload_py
    decoder = RangeDecoder(payload)

    flag_model = Fenwick(3, 32768, 512)
    length_model = Fenwick(256, 32768, 64)
    dist_model = Fenwick(DIST_BUCKETS, 32768, 64)
    rep_index_model = Fenwick(NUM_REP, 32768, 512)
    cdef dict lit_tables = {}
    order0_table = Fenwick(256, 32768, 48)
    lit_list = [Fenwick(256, 32768, 48) for _ in range(256)] if order == 1 else ([None] * 65536 if order == 2 else [None])

    cdef int rep_cache[6]
    cdef int i0
    for i0 in range(NUM_REP):
        rep_cache[i0] = 0

    cdef bytes hist_bytes = bytes(history_py)
    cdef const unsigned char[:] hist_view = hist_bytes
    cdef Py_ssize_t hist_len = hist_view.shape[0]
    cdef Py_ssize_t total_size = hist_len + original_size
    cdef unsigned char* out_buf = <unsigned char*> malloc(total_size if total_size > 0 else 1)
    cdef Py_ssize_t hi
    for hi in range(hist_len):
        out_buf[hi] = hist_view[hi]
    cdef Py_ssize_t wpos = hist_len
    cdef unsigned int cum, freq, tot, target, extra
    cdef int flag, symbol, length_sym, length, b, start, i, dist, rep_idx
    cdef int hist0 = 0, hist1 = 0, lit_idx = 0
    cdef bytes ctx_key
    cdef unsigned char byte_val

    while wpos - hist_len < original_size:
        tot = flag_model.total
        target = decoder.get_freq(tot)
        flag = flag_model.find(target, &cum, &freq)
        decoder.decode(cum, freq, tot)
        flag_model.update(flag)

        if flag == LITERAL:
            if order == 0:
                fm = order0_table
            else:
                lit_idx = hist0 if order == 1 else (hist1 * 256 + hist0)
                fm = lit_list[lit_idx]
                if fm is None:
                    fm = Fenwick(256, 32768, 48)
                    lit_list[lit_idx] = fm
            tot = (<Fenwick> fm).total
            target = decoder.get_freq(tot)
            symbol = (<Fenwick> fm).find(target, &cum, &freq)
            decoder.decode(cum, freq, tot)
            (<Fenwick> fm).update(symbol)
            out_buf[wpos] = <unsigned char> symbol
            wpos += 1
            if order == 2:
                hist1 = hist0
            if order >= 1:
                hist0 = symbol
        else:
            if flag == REP_MATCH:
                tot = rep_index_model.total
                target = decoder.get_freq(tot)
                rep_idx = rep_index_model.find(target, &cum, &freq)
                decoder.decode(cum, freq, tot)
                rep_index_model.update(rep_idx)

                tot = length_model.total
                target = decoder.get_freq(tot)
                length_sym = length_model.find(target, &cum, &freq)
                decoder.decode(cum, freq, tot)
                length_model.update(length_sym)
                length = length_sym + MIN_MATCH

                dist = rep_cache[rep_idx]
                rep_use(rep_cache, rep_idx)
            else:
                tot = length_model.total
                target = decoder.get_freq(tot)
                length_sym = length_model.find(target, &cum, &freq)
                decoder.decode(cum, freq, tot)
                length_model.update(length_sym)
                length = length_sym + MIN_MATCH

                tot = dist_model.total
                target = decoder.get_freq(tot)
                b = dist_model.find(target, &cum, &freq)
                decoder.decode(cum, freq, tot)
                dist_model.update(b)

                if b > 0:
                    tot = <unsigned int>(1 << b)
                    target = decoder.get_freq(tot)
                    decoder.decode(target, 1, tot)
                    extra = target
                else:
                    extra = 0
                dist = (1 << b) + <int> extra
                rep_add(rep_cache, dist)

            start = wpos - dist
            if start < 0:
                free(out_buf)
                raise ValueError("Osteceni arhiv: udaljenost izvan opsega")
            if wpos + length > total_size:
                free(out_buf)
                raise ValueError("Osteceni arhiv: duzina poklapanja prelazi ocekivanu velicinu")

            if dist >= length:
                # ne preklapa se - jedan memcpy umjesto petlje
                memcpy(out_buf + wpos, out_buf + start, length)
                if not skip_literal_in_match:
                    for i in range(length):
                        byte_val = out_buf[wpos + i]
                        if order == 0:
                            fm = order0_table
                        else:
                            lit_idx = hist0 if order == 1 else (hist1 * 256 + hist0)
                            fm = lit_list[lit_idx]
                            if fm is None:
                                fm = Fenwick(256, 32768, 48)
                                lit_list[lit_idx] = fm
                        (<Fenwick> fm).update(byte_val)
                        if order == 2:
                            hist1 = hist0
                        if order >= 1:
                            hist0 = byte_val
                wpos += length
            else:
                # preklapa se (RLE stil) - mora byte-po-byte
                for i in range(length):
                    byte_val = out_buf[start + i]
                    out_buf[wpos] = byte_val
                    wpos += 1
                    if not skip_literal_in_match:
                        if order == 0:
                            fm = order0_table
                        else:
                            lit_idx = hist0 if order == 1 else (hist1 * 256 + hist0)
                            fm = lit_list[lit_idx]
                            if fm is None:
                                fm = Fenwick(256, 32768, 48)
                                lit_list[lit_idx] = fm
                        (<Fenwick> fm).update(byte_val)
                        if order == 2:
                            hist1 = hist0
                        if order >= 1:
                            hist0 = byte_val

    result = PyByteArray_FromStringAndSize(<char*> (out_buf + hist_len), wpos - hist_len)
    free(out_buf)
    return result
'''

def _get_temp_dir():
    for _var in ("TMPDIR", "TEMP", "TMP"):
        _v = os.environ.get(_var)
        if _v and os.path.isdir(_v):
            return _v
    if os.name == "nt":
        return os.environ.get("LOCALAPPDATA", "C:\\Windows") + "\\Temp"
    return "/tmp" if os.path.isdir("/tmp") else os.getcwd()


_USE_CYTHON = False
_BACKEND_NOTE = ""
_BUILD_TAG = "v24"  # bump ovo kad se _PYX_SOURCE promijeni

_cache_dir = os.path.join(_get_temp_dir(), "mujopresa_cybuild")
_build_subdir = os.path.join(_cache_dir, "build")
_tagged_dir = os.path.join(_cache_dir, _BUILD_TAG)

try:
    if os.path.isdir(_tagged_dir):
        sys.path.insert(0, _tagged_dir)
        try:
            import mujo_core
            _USE_CYTHON = True
            _BACKEND_NOTE = "Cython (ubrzano C jezgro, keširano)"
        except ImportError:
            sys.path.remove(_tagged_dir)

    if not _USE_CYTHON:
        os.makedirs(_cache_dir, exist_ok=True)
        _pyx_path = os.path.join(_cache_dir, "mujo_core.pyx")
        with open(_pyx_path, "w") as _f:
            _f.write(_PYX_SOURCE)

        if _cache_dir not in sys.path:
            sys.path.insert(0, _cache_dir)

        import pyximport  # dio Cython paketa
        pyximport.install(
            language_level=3,
            build_dir=_build_subdir,
            setup_args={"script_args": ["--quiet"]},
        )
        import mujo_core  # noqa: E402  (kompajlira se ovdje ako treba)

        try:
            os.makedirs(_tagged_dir, exist_ok=True)
            _dst_path = os.path.join(_tagged_dir, os.path.basename(mujo_core.__file__))
            with open(mujo_core.__file__, "rb") as _src, open(_dst_path, "wb") as _dst:
                _dst.write(_src.read())
        except OSError:
            pass  # nije kriticno - samo znaci da ce se fast-path preskociti sljedeci put

        _USE_CYTHON = True
        _BACKEND_NOTE = "Cython (ubrzano C jezgro, prvi build)"
except Exception as _e:
    _USE_CYTHON = False
    _BACKEND_NOTE = f"cist Python (fallback - Cython nedostupan: {type(_e).__name__})"


# =====================================================================
# OPCIONALNE ZAVISNOSTI (soft dependency) za --engine - importuju se
# lijeno, tek kad se stvarno zatrazi taj engine (ne pri ucitavanju modula)
# =====================================================================




# =====================================================================
# CIST PYTHON FALLBACK - identicna logika, koristi se ako Cython/C
# kompajler nisu dostupni. Arhive su kompatibilne sa Cython backendom.
# =====================================================================

if not _USE_CYTHON:

    from array import array

    TOP = 1 << 24
    BOTTOM = 1 << 16
    MASK32 = 0xFFFFFFFF
    MIN_MATCH = 4
    MAX_LEN = MIN_MATCH + 255
    DIST_BUCKETS = 16
    LITERAL, MATCH, REP_MATCH = 0, 1, 2
    REP_BIAS = 2
    NUM_REP = 4
    INSERT_STEP = 4


    class _RangeEncoder:
        def __init__(self):
            self.low = 0
            self.range = MASK32
            self.out = bytearray()

        def encode(self, cum, freq, tot):
            self.range //= tot
            self.low = (self.low + cum * self.range) & MASK32
            self.range = (self.range * freq) & MASK32
            self._norm()

        def _norm(self):
            out = self.out
            while True:
                if ((self.low ^ (self.low + self.range)) & MASK32) < TOP:
                    pass
                elif self.range < BOTTOM:
                    self.range = (-self.low) & (BOTTOM - 1)
                else:
                    break
                out.append((self.low >> 24) & 0xFF)
                self.low = (self.low << 8) & MASK32
                self.range = (self.range << 8) & MASK32

        def finish(self):
            for _ in range(4):
                self.out.append((self.low >> 24) & 0xFF)
                self.low = (self.low << 8) & MASK32
            return bytes(self.out)


    class _RangeDecoder:
        def __init__(self, data):
            self.data = data
            self.pos = 0
            self.low = 0
            self.range = MASK32
            self.code = 0
            for _ in range(4):
                self.code = ((self.code << 8) | self._next()) & MASK32

        def _next(self):
            if self.pos < len(self.data):
                b = self.data[self.pos]; self.pos += 1; return b
            return 0

        def get_freq(self, tot):
            self.range //= tot
            if self.range == 0:
                self.range = 1
            return ((self.code - self.low) & MASK32) // self.range

        def decode(self, cum, freq, tot):
            self.low = (self.low + cum * self.range) & MASK32
            self.range = (self.range * freq) & MASK32
            self._norm()

        def _norm(self):
            _guard = 0
            while True:
                if ((self.low ^ (self.low + self.range)) & MASK32) < TOP:
                    pass
                elif self.range < BOTTOM:
                    self.range = (-self.low) & (BOTTOM - 1)
                    if self.range == 0:
                        self.range = 1
                else:
                    break
                self.code = ((self.code << 8) | self._next()) & MASK32
                self.low = (self.low << 8) & MASK32
                self.range = (self.range << 8) & MASK32
                _guard += 1
                if _guard > 64:
                    break


    class _FenwickModel:
        __slots__ = ('size', 'tree', 'freqs', 'total', 'max_total', 'increment', 'bm')

        def __init__(self, size, max_total, increment=32):
            self.size = size
            self.tree = array('i', [0]) * (size + 1)
            self.freqs = array('i', [1]) * size
            for i in range(size):
                self._add(i, 1)
            self.total = size
            self.max_total = min(max_total, BOTTOM - 1)
            self.increment = increment
            bm = 1
            while bm * 2 <= size:
                bm *= 2
            self.bm = bm

        def _add(self, s, d):
            i = s + 1
            n = self.size
            t = self.tree
            while i <= n:
                t[i] += d
                i += i & (-i)

        def _psum(self, i):
            s = 0
            t = self.tree
            while i > 0:
                s += t[i]
                i -= i & (-i)
            return s

        def cum_and_freq(self, sym):
            return self._psum(sym), self.freqs[sym]

        def find(self, target):
            idx = 0
            remaining = target
            bitmask = self.bm
            t = self.tree
            size = self.size
            while bitmask > 0:
                ni = idx + bitmask
                if ni <= size and t[ni] <= remaining:
                    idx = ni
                    remaining -= t[ni]
                bitmask >>= 1
            cum = target - remaining
            return idx, cum, self.freqs[idx]

        def update(self, sym):
            self._add(sym, self.increment)
            self.freqs[sym] += self.increment
            self.total += self.increment
            if self.total > self.max_total:
                self.tree = array('i', [0]) * (self.size + 1)
                nt = 0
                for i in range(self.size):
                    nf = (self.freqs[i] >> 1) or 1
                    self.freqs[i] = nf
                    self._add(i, nf)
                    nt += nf
                self.total = nt


    class _ContextModel:
        def __init__(self, order):
            self.order = order
            self.tables = {}
            self.history = bytearray(order)

        def key(self):
            return bytes(self.history) if self.order else 0

        def table(self):
            k = self.key()
            t = self.tables.get(k)
            if t is None:
                t = _FenwickModel(256, 32768, 48)
                self.tables[k] = t
            return t

        def update(self, sym):
            self.table().update(sym)
            if self.order:
                self.history.pop(0)
                self.history.append(sym)


    def _hash4(d, i):
        return ((d[i] << 24) | (d[i+1] << 16) | (d[i+2] << 8) | d[i+3]) * 2654435761 & 0xFFFFFFFF


    class _LZMatcher:
        def __init__(self, data, window, max_chain):
            self.data = data
            self.n = len(data)
            self.window = window
            self.max_chain = max_chain
            self.head = {}
            self.prev = array('i', [-1]) * self.n if self.n else array('i', [])

        def insert(self, i):
            if i + 4 <= self.n:
                h = _hash4(self.data, i)
                self.prev[i] = self.head.get(h, -1)
                self.head[h] = i

        def find_match(self, pos):
            n = self.n
            data = self.data
            if pos + 4 > n:
                return 0, 0
            h = _hash4(data, pos)
            candidate = self.head.get(h, -1)
            best_len = 0
            best_dist = 0
            chain = 0
            limit = min(MAX_LEN, n - pos)
            min_candidate = pos - self.window
            while candidate >= 0 and candidate >= min_candidate and chain < self.max_chain:
                if best_len == 0 or (candidate + best_len < n and data[candidate + best_len] == data[pos + best_len]):
                    l = 0
                    while l < limit and data[candidate + l] == data[pos + l]:
                        l += 1
                    if l > best_len:
                        best_len = l
                        best_dist = pos - candidate
                        if best_len >= limit:
                            break
                        if best_len >= 32 and chain >= 8:
                            break
                candidate = self.prev[candidate]
                chain += 1
            return best_len, best_dist

        def match_at_distance(self, pos, dist):
            n = self.n
            data = self.data
            candidate = pos - dist
            if candidate < 0:
                return 0
            limit = min(MAX_LEN, n - pos)
            l = 0
            while l < limit and data[candidate + l] == data[pos + l]:
                l += 1
            return l


    class _Models:
        __slots__ = ('literal', 'flag', 'length', 'dist_bucket', 'rep_index', 'rep_cache')

        def __init__(self, order):
            self.literal = _ContextModel(order)
            self.flag = _FenwickModel(3, 32768, 32)
            self.length = _FenwickModel(256, 32768, 64)
            self.dist_bucket = _FenwickModel(DIST_BUCKETS, 32768, 32)
            self.rep_index = _FenwickModel(NUM_REP, 32768, 32)
            self.rep_cache = [0, 0, 0, 0]

        def rep_use(self, idx):
            """Pomjeri koriscenu udaljenost na pocetak - MORA se zvati identicno
            i na enkoder i na dekoder strani (za NOVE i za PONOVLJENE distance)."""
            dist = self.rep_cache[idx]
            del self.rep_cache[idx]
            self.rep_cache.insert(0, dist)

        def rep_add(self, dist):
            if dist in self.rep_cache:
                self.rep_cache.remove(dist)
            self.rep_cache.insert(0, dist)
            self.rep_cache.pop()

        def rep_find(self, dist):
            try:
                return self.rep_cache.index(dist)
            except ValueError:
                return -1


    def _encode_symbol(enc, model, sym):
        cum, freq = model.cum_and_freq(sym)
        enc.encode(cum, freq, model.total)
        model.update(sym)


    def _decode_symbol(dec, model):
        tot = model.total
        target = dec.get_freq(tot)
        sym, cum, freq = model.find(target)
        dec.decode(cum, freq, tot)
        model.update(sym)
        return sym


    def _compress_bytes_py(data, order, window_bits, max_chain, skip_literal_in_match=True):
        n = len(data)
        window = 1 << window_bits
        models = _Models(order)
        encoder = _RangeEncoder()
        matcher = _LZMatcher(data, window, max_chain)

        pos = 0
        cached = None
        while pos < n:
            rep_best_idx = -1
            rep_best_len = 0
            for ri in range(NUM_REP):
                rd = models.rep_cache[ri]
                if rd > 0:
                    rl = matcher.match_at_distance(pos, rd)
                    if rl > rep_best_len:
                        rep_best_len = rl
                        rep_best_idx = ri
                        if rep_best_len >= 64:
                            break

            if rep_best_len >= 32:
                best_len, best_dist = 0, 0
                cached = None
            elif cached is not None:
                best_len, best_dist = cached
                cached = None
            else:
                best_len, best_dist = matcher.find_match(pos)

            use_rep = rep_best_idx >= 0 and rep_best_len >= MIN_MATCH and (rep_best_len + REP_BIAS >= best_len)
            use_new = (not use_rep) and best_len >= MIN_MATCH

            if use_rep or use_new:
                cur_len = rep_best_len if use_rep else best_len

                if pos + 1 < n and cur_len < 64:
                    nrep_len = 0
                    for ri in range(NUM_REP):
                        rd = models.rep_cache[ri]
                        if rd > 0:
                            rl = matcher.match_at_distance(pos + 1, rd)
                            if rl > nrep_len:
                                nrep_len = rl
                                if nrep_len >= 64:
                                    break
                    if nrep_len >= 32:
                        nb_len, nb_dist = 0, 0
                    else:
                        nb_len, nb_dist = matcher.find_match(pos + 1)
                    next_best = max(nb_len, nrep_len)
                    if next_best > cur_len:
                        if nb_len >= nrep_len:
                            cached = (nb_len, nb_dist)
                        _encode_symbol(encoder, models.flag, LITERAL)
                        byte_val = data[pos]
                        tbl = models.literal.table()
                        cum, freq = tbl.cum_and_freq(byte_val)
                        encoder.encode(cum, freq, tbl.total)
                        models.literal.update(byte_val)
                        matcher.insert(pos)
                        pos += 1
                        continue

                if use_rep:
                    _encode_symbol(encoder, models.flag, REP_MATCH)
                    _encode_symbol(encoder, models.rep_index, rep_best_idx)
                    _encode_symbol(encoder, models.length, rep_best_len - MIN_MATCH)
                    models.rep_use(rep_best_idx)
                    match_len = rep_best_len
                else:
                    _encode_symbol(encoder, models.flag, MATCH)
                    _encode_symbol(encoder, models.length, best_len - MIN_MATCH)
                    b = best_dist.bit_length() - 1
                    _encode_symbol(encoder, models.dist_bucket, b)
                    if b > 0:
                        extra = best_dist - (1 << b)
                        encoder.encode(extra, 1, 1 << b)
                    models.rep_add(best_dist)
                    match_len = best_len

                for i in range(pos, pos + match_len):
                    if match_len < 8 or (i - pos) % INSERT_STEP == 0:
                        matcher.insert(i)
                    if not skip_literal_in_match:
                        models.literal.update(data[i])
                pos += match_len
            else:
                _encode_symbol(encoder, models.flag, LITERAL)
                byte_val = data[pos]
                tbl = models.literal.table()
                cum, freq = tbl.cum_and_freq(byte_val)
                encoder.encode(cum, freq, tbl.total)
                models.literal.update(byte_val)
                matcher.insert(pos)
                pos += 1

        return encoder.finish()


    def _decompress_bytes_py(payload, original_size, order, skip_literal_in_match=True):
        models = _Models(order)
        decoder = _RangeDecoder(payload)
        out = bytearray()

        while len(out) < original_size:
            flag = _decode_symbol(decoder, models.flag)
            if flag == LITERAL:
                tbl = models.literal.table()
                tot = tbl.total
                target = decoder.get_freq(tot)
                sym, cum, freq = tbl.find(target)
                decoder.decode(cum, freq, tot)
                models.literal.update(sym)
                out.append(sym)
            else:
                if flag == REP_MATCH:
                    rep_idx = _decode_symbol(decoder, models.rep_index)
                    length_sym = _decode_symbol(decoder, models.length)
                    length = length_sym + MIN_MATCH
                    dist = models.rep_cache[rep_idx]
                    models.rep_use(rep_idx)
                else:
                    length_sym = _decode_symbol(decoder, models.length)
                    length = length_sym + MIN_MATCH
                    b = _decode_symbol(decoder, models.dist_bucket)
                    if b > 0:
                        tot_extra = 1 << b
                        target_extra = decoder.get_freq(tot_extra)
                        decoder.decode(target_extra, 1, tot_extra)
                        extra = target_extra
                    else:
                        extra = 0
                    dist = (1 << b) + extra
                    models.rep_add(dist)

                start = len(out) - dist
                if start < 0:
                    raise ValueError("Osteceni arhiv")
                for i in range(length):
                    bv = out[start + i]
                    out.append(bv)
                    if not skip_literal_in_match:
                        models.literal.update(bv)

        return out


# =====================================================================
# JEDINSTVENI INTERFEJS - bira Cython ako je dostupan, inace Python
# =====================================================================

if _USE_CYTHON:
    compress_bytes = mujo_core.compress_bytes
    decompress_bytes = mujo_core.decompress_bytes
else:
    compress_bytes = _compress_bytes_py
    decompress_bytes = _decompress_bytes_py


# =====================================================================
# VISOKI NIVO: KOMPRESIJA / DEKOMPRESIJA FAJLOVA (+ engine fallback)
# =====================================================================

MAGIC = b"MUJC"
HEADER_FIXED_FMT = ">4sIBBBBB"   # magic, original_size, order, window_bits, chain, flags, engine_id
HEADER_FIXED_SIZE = struct.calcsize(HEADER_FIXED_FMT)
CHECKSUM_SIZE = 4
_STRUCT_HEADER = struct.Struct(HEADER_FIXED_FMT)
_STRUCT_H = struct.Struct(">H")
_STRUCT_I = struct.Struct(">I")


def _checksum(data):
    """CRC32 (4 bajta) - dovoljno za detekciju osteceenja arhive, manji fiksni
    overhead nego puni SHA-256 (32 bajta) - bitno za male fajlove."""
    import zlib
    return zlib.crc32(data).to_bytes(4, 'big')
DEFAULT_EXT = ".mujo"

FLAG_SKIP_LITERAL_UPDATE = 1
FLAG_CHUNKED = 2
MIN_CHUNK_SIZE = 262144  # 256 KB - ispod ovoga paralelizacija ne vrijedi truda
ENGINE_IDS = {'custom': 0, 'zstd': 1, 'lzma': 2, 'brotli': 3}
ENGINE_NAMES = {v: k for k, v in ENGINE_IDS.items()}


def _compress_chunk_worker(args):
    chunk_bytes, order, window_bits, max_chain, skip_literal_update, history = args
    return compress_bytes(chunk_bytes, order, window_bits, max_chain, skip_literal_update, history)


def _decompress_chunk_worker_seq(chunk_payload, chunk_original_size, order, skip_literal_update, history):
    return bytes(decompress_bytes(chunk_payload, chunk_original_size, order, skip_literal_update, history))


def _split_sizes(original_size, chunk_count):
    """Ista formula na kompresiji i dekompresiji - determinise granice chunkova."""
    chunk_size = (original_size + chunk_count - 1) // chunk_count
    sizes = []
    remaining = original_size
    for _ in range(chunk_count):
        cs = min(chunk_size, remaining)
        sizes.append(cs)
        remaining -= cs
    return chunk_size, sizes


def _parse_archive_meta(data):
    """Cita header i ime fajla BEZ dekompresije - za brzi preview (kao 'unzip -l')."""
    if len(data) < HEADER_FIXED_SIZE + 2:
        sys.exit("[-] Neispravan ili osteceni arhiv.")
    magic, original_size, order, window_bits, max_chain, flags, engine_id = _STRUCT_HEADER.unpack(
        data[:HEADER_FIXED_SIZE])
    if magic != MAGIC:
        sys.exit(f"[-] Nije validan MujoPresa ({DEFAULT_EXT}) arhiv (pogresan magic broj).")
    off = HEADER_FIXED_SIZE
    (name_len,) = _STRUCT_H.unpack(data[off:off + 2])
    off += 2
    filename = data[off:off + name_len].decode('utf-8', errors='replace')
    off += name_len
    if len(data) < off + CHECKSUM_SIZE:
        sys.exit("[-] Neispravan ili osteceni arhiv.")
    checksum = data[off:off + CHECKSUM_SIZE]
    off += CHECKSUM_SIZE
    payload = data[off:]
    return {
        'original_size': original_size, 'order': order, 'window_bits': window_bits,
        'max_chain': max_chain, 'flags': flags, 'engine_id': engine_id,
        'engine': ENGINE_NAMES.get(engine_id, 'custom'),
        'skip_literal_update': bool(flags & FLAG_SKIP_LITERAL_UPDATE),
        'filename': filename, 'checksum': checksum, 'payload': payload,
    }


def _compress_with_engine(data, engine):
    if engine == 'zstd':
        try:
            import zstandard as zstd
        except ImportError:
            sys.exit("[-] zstandard nije instaliran. Pokreni: pip install zstandard")
        return zstd.ZstdCompressor(level=19).compress(bytes(data))
    if engine == 'lzma':
        import lzma
        return lzma.compress(bytes(data), preset=9)
    if engine == 'brotli':
        try:
            import brotli
        except ImportError:
            sys.exit("[-] brotli nije instaliran. Pokreni: pip install brotli")
        return brotli.compress(bytes(data), quality=11)
    raise ValueError(f"Nepoznat engine: {engine}")


def _decompress_with_engine(data, engine):
    if engine == 'zstd':
        try:
            import zstandard as zstd
        except ImportError:
            sys.exit("[-] zstandard nije instaliran. Pokreni: pip install zstandard")
        return zstd.ZstdDecompressor().decompress(data)
    if engine == 'lzma':
        import lzma
        return lzma.decompress(data)
    if engine == 'brotli':
        try:
            import brotli
        except ImportError:
            sys.exit("[-] brotli nije instaliran. Pokreni: pip install brotli")
        return brotli.decompress(data)
    raise ValueError(f"Nepoznat engine: {engine}")


def compress_file(input_path, output_path, order, window_bits, max_chain,
                   skip_literal_update=True, engine='custom', threads=1):
    file_size = os.path.getsize(input_path)
    if window_bits is None:
        window_bits = min(15, max(10, file_size.bit_length())) if file_size > 0 else 10
    if max_chain is None:
        if file_size < 8388608:
            max_chain = 255
        else:
            max_chain = 128
    if threads == 'auto':
        threads = os.cpu_count() or 1
    name_bytes = os.path.basename(input_path).encode('utf-8')[:65535]
    name_field = _STRUCT_H.pack(len(name_bytes)) + name_bytes
    with open(input_path, 'rb') as f:
        if file_size == 0:
            raw = b""
        else:
            import mmap
            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                raw = mm  # koristi se kao bytes-like objekat direktno (bez kopije u RAM)
                checksum = _checksum(raw)
                original_size = len(raw)

                use_chunks = (engine == 'custom' and threads > 1 and
                              original_size >= threads * MIN_CHUNK_SIZE)

                flags_chunked = 0
                if engine == 'custom':
                    if use_chunks:
                        chunk_size, _ = _split_sizes(original_size, threads)
                        hist_size = 1 << window_bits
                        chunk_args = [
                            (raw[o:o + chunk_size], order, window_bits, max_chain, skip_literal_update,
                             bytes(raw[max(0, o - hist_size):o]))
                            for o in range(0, original_size, chunk_size)
                        ]
                        import multiprocessing
                        with multiprocessing.Pool(threads) as pool:
                            compressed_chunks = pool.map(_compress_chunk_worker, chunk_args)
                        payload = _STRUCT_H.pack(len(compressed_chunks))
                        for cc in compressed_chunks:
                            payload += _STRUCT_I.pack(len(cc))
                        payload += b"".join(compressed_chunks)
                        flags_chunked = FLAG_CHUNKED
                    else:
                        payload = compress_bytes(raw, order, window_bits, max_chain, skip_literal_update) if original_size else b""
                else:
                    payload = _compress_with_engine(raw, engine)

                flags = (FLAG_SKIP_LITERAL_UPDATE if skip_literal_update else 0) | flags_chunked
                header = _STRUCT_HEADER.pack(MAGIC, original_size, order, window_bits,
                                      max_chain & 0xFF, flags, ENGINE_IDS[engine])
                final_bytes = header + name_field + checksum + payload
                with open(output_path, 'wb') as out_f:
                    out_f.write(final_bytes)

                ratio = (1 - len(final_bytes) / original_size) * 100 if original_size else 0.0
                thread_note = f" | threads={threads} (chunked)" if use_chunks else ""
                print(f"[+] backend: {_BACKEND_NOTE} | engine: {engine}{thread_note}")
                print(f"[+] order={order} window={1 << window_bits} chain={max_chain} skip_literal_update={skip_literal_update}")
                print(f"[OK] {original_size} -> {len(final_bytes)} bajta ({ratio:.2f}% usteda) -> {output_path}")
                return
    # prazan fajl (mmap ne radi na 0-bajtnim fajlovima)
    checksum = _checksum(b"")
    header = _STRUCT_HEADER.pack(MAGIC, 0, order, window_bits, max_chain & 0xFF,
                          FLAG_SKIP_LITERAL_UPDATE if skip_literal_update else 0, ENGINE_IDS[engine])
    final_bytes = header + name_field + checksum
    with open(output_path, 'wb') as out_f:
        out_f.write(final_bytes)
    print(f"[+] backend: {_BACKEND_NOTE} | engine: {engine}")
    print(f"[OK] 0 -> {len(final_bytes)} bajta -> {output_path}")


def decompress_file(input_path, output_path, threads=1):
    with open(input_path, 'rb') as f:
        data = f.read()
    meta = _parse_archive_meta(data)
    original_size = meta['original_size']
    order = meta['order']
    engine = meta['engine']
    chunked = bool(meta['flags'] & FLAG_CHUNKED)

    if threads == 'auto':
        threads = os.cpu_count() or 1

    if original_size == 0:
        reconstructed = bytearray()
    elif engine == 'custom' and chunked:
        payload = meta['payload']
        off = 0
        (chunk_count,) = _STRUCT_H.unpack(payload[off:off + 2])
        off += 2
        lengths = []
        for _ in range(chunk_count):
            (l,) = _STRUCT_I.unpack(payload[off:off + 4])
            off += 4
            lengths.append(l)
        chunk_payloads = []
        for l in lengths:
            chunk_payloads.append(payload[off:off + l])
            off += l
        _, chunk_orig_sizes = _split_sizes(original_size, chunk_count)
        hist_size = 1 << meta['window_bits']
        # sekvencijalno - svaki chunk koristi kraj vec dekodiranog prethodnog kao history
        # (dictionary carryover znaci da paralelna dekompresija ovdje nije moguca,
        # samo kompresija ostaje paralelna jer koristi sirove ulazne podatke direktno)
        parts = []
        tail = b""
        for i in range(chunk_count):
            part = _decompress_chunk_worker_seq(chunk_payloads[i], chunk_orig_sizes[i], order,
                                                 meta['skip_literal_update'], tail)
            parts.append(part)
            combined_tail = (tail + part)
            tail = combined_tail[-hist_size:]
        reconstructed = b"".join(parts)
    elif engine == 'custom':
        reconstructed = decompress_bytes(meta['payload'], original_size, order, meta['skip_literal_update'])
    else:
        reconstructed = bytearray(_decompress_with_engine(meta['payload'], engine))

    if _checksum(reconstructed) != meta['checksum']:
        sys.exit("[KRITICNO] Provjera integriteta neuspjela.")
    with open(output_path, 'wb') as f:
        f.write(reconstructed)
    print(f"[+] backend: {_BACKEND_NOTE} | engine: {engine}" + (" | chunked" if chunked else ""))
    print(f"[OK] Dekompresija zavrsena -> {output_path}")


def preview_file(input_path):
    """Prikaz metapodataka arhive BEZ dekompresije - poput 'unzip -l'."""
    with open(input_path, 'rb') as f:
        data = f.read()
    meta = _parse_archive_meta(data)
    compressed_size = len(data)
    original_size = meta['original_size']
    ratio = (1 - compressed_size / original_size) * 100 if original_size else 0.0
    print(f"Arhiva:              {input_path}")
    print(f"Originalno ime:      {meta['filename'] or '(nepoznato)'}")
    print(f"Originalna velicina: {original_size} bajta")
    print(f"Komprimovana:        {compressed_size} bajta")
    print(f"Usteda:              {ratio:.2f}%")
    print(f"Engine:              {meta['engine']}")
    if meta['engine'] == 'custom':
        print(f"Order:               {meta['order']}")
        print(f"Window:              {1 << meta['window_bits']}")
        print(f"Chain:               {meta['max_chain']}")
        print(f"Skip-literal-update: {meta['skip_literal_update']}")
    print(f"CRC32 checksum:      {meta['checksum'].hex()}")



# =====================================================================
# CLI
# =====================================================================

def _print_help():
    print("""MujoPresa - LZ77 + adaptivni order-N kompresor (Cython/Python)

Upotreba:
  mujopresa.py compress   -i ULAZ [-o IZLAZ] [opcije]
  mujopresa.py decompress -i ULAZ [-o IZLAZ] [--threads N]
  mujopresa.py info       -i ULAZ

Opcije:
  -i, --input FILE        ulazni fajl (obavezno)
  -o, --output FILE       izlazni fajl (podrazumijevano: <input>.mujo / ime iz arhive)
  --preset {fast,balanced,best}  gotova kombinacija opcija (eksplicitni flagovi imaju prednost)
  --order {0,1,2}         red literal-konteksta (podrazumijevano 0)
  --window-bits N         log2 LZ prozora, 10-20 (podrazumijevano: auto)
  --chain N                max dubina hash-lanca (podrazumijevano: auto)
  --no-skip-literal-update azuriraj literal-kontekst i tokom match-a
  --engine {custom,zstd,lzma,brotli}  (podrazumijevano custom)
  --threads N|auto        broj procesa za paralelnu chunk kompresiju (podrazumijevano 1)
  -h, --help               ovaj tekst""")


PRESETS = {
    'fast':     {'order': 0, 'chain': 64,  'no_skip_literal_update': False},
    'balanced': {'order': 0, 'chain': None, 'no_skip_literal_update': False},
    'best':     {'order': 2, 'chain': 255, 'no_skip_literal_update': True},
}


def _parse_args(argv):
    if not argv or argv[0] in ('-h', '--help'):
        _print_help()
        sys.exit(0)

    mode = argv[0]
    if mode not in ('compress', 'decompress', 'info'):
        sys.exit(f"[-] Nepoznat mode '{mode}'. Ocekivano: compress/decompress/info")

    a = {'input': None, 'output': None, 'order': 0, 'window_bits': None,
         'chain': None, 'no_skip_literal_update': False, 'engine': 'custom', 'threads': '1'}
    explicit = set()

    i = 1
    n = len(argv)
    while i < n:
        tok = argv[i]
        key = tok.split('=', 1)[0]

        def _val():
            if '=' in tok:
                return tok.split('=', 1)[1]
            nonlocal i
            i += 1
            if i >= n:
                sys.exit(f"[-] Nedostaje vrijednost za {key}")
            return argv[i]

        if key in ('-i', '--input'):
            a['input'] = _val()
        elif key in ('-o', '--output'):
            a['output'] = _val()
        elif key == '--preset':
            v = _val()
            if v not in PRESETS:
                sys.exit("[-] --preset mora biti fast/balanced/best")
            a['preset'] = v
        elif key == '--order':
            v = _val()
            if v not in ('0', '1', '2'):
                sys.exit("[-] --order mora biti 0, 1 ili 2")
            a['order'] = int(v)
            explicit.add('order')
        elif key == '--window-bits':
            v = _val()
            if not v.isdigit() or not (10 <= int(v) <= 20):
                sys.exit("[-] --window-bits mora biti cijeli broj 10-20")
            a['window_bits'] = int(v)
            explicit.add('window_bits')
        elif key == '--chain':
            v = _val()
            if not v.isdigit():
                sys.exit("[-] --chain mora biti cijeli broj")
            a['chain'] = int(v)
            explicit.add('chain')
        elif key == '--no-skip-literal-update':
            a['no_skip_literal_update'] = True
            explicit.add('no_skip_literal_update')
        elif key == '--engine':
            v = _val()
            if v not in ('custom', 'zstd', 'lzma', 'brotli'):
                sys.exit("[-] --engine mora biti custom/zstd/lzma/brotli")
            a['engine'] = v
        elif key == '--threads':
            a['threads'] = _val()
        elif key in ('-h', '--help'):
            _print_help()
            sys.exit(0)
        else:
            sys.exit(f"[-] Nepoznata opcija: {tok}")
        i += 1

    if a['input'] is None:
        sys.exit("[-] -i/--input je obavezan")

    preset_name = a.pop('preset', None)
    if preset_name:
        for pkey, pval in PRESETS[preset_name].items():
            if pkey not in explicit:
                a[pkey] = pval

    return mode, a


def main():
    mode, a = _parse_args(sys.argv[1:])

    threads = a['threads']
    if threads != 'auto':
        if not threads.lstrip('-').isdigit():
            sys.exit("[-] --threads mora biti cijeli broj ili 'auto'")
        threads = int(threads)

    try:
        if mode == 'compress':
            output_path = a['output'] or (a['input'] + DEFAULT_EXT)
            compress_file(a['input'], output_path, a['order'], a['window_bits'], a['chain'],
                          skip_literal_update=not a['no_skip_literal_update'], engine=a['engine'], threads=threads)
        elif mode == 'info':
            preview_file(a['input'])
        else:
            if a['output']:
                output_path = a['output']
            else:
                with open(a['input'], 'rb') as f:
                    meta = _parse_archive_meta(f.read())
                output_path = meta['filename'] if meta['filename'] else a['input'] + ".out"
            decompress_file(a['input'], output_path, threads=threads)
    except FileNotFoundError:
        sys.exit(f"[-] '{a['input']}' ne postoji.")
    except IsADirectoryError:
        sys.exit(f"[-] '{a['input']}' je direktorij, ocekivan je fajl.")
    except ValueError as _ve:
        sys.exit(f"[-] {_ve}")


if __name__ == '__main__':
    main()
