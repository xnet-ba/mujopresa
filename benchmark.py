#!/usr/bin/env python3
"""
MujoPresa vs gzip / bzip2 / zip / zstd benchmark
=================================================
Compares compression ratio and speed on diverse test data.
"""

import os
import sys
import time
import hashlib
import io
import struct

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

# ── Test data generators ──────────────────────────────────────────

def make_text_repetitive(size: int) -> bytes:
    """Highly repetitive English text (good for LZ)"""
    paragraph = (
        "The quick brown fox jumps over the lazy dog. "
        "Pack my box with five dozen liquor jugs. "
        "How vexingly quick daft zebras jump! "
        "Sphinx of black quartz, judge my vow. "
        "The five boxing wizards jump quickly. "
    )
    return (paragraph * (size // len(paragraph) + 1))[:size].encode()

def make_text_natural(size: int) -> bytes:
    """Natural language text (HTML/JSON-like)"""
    import json, string, random
    rng = random.Random(42)
    parts = []
    for _ in range(size // 200 + 1):
        obj = {
            "id": rng.randint(0, 100000),
            "name": ''.join(rng.choices(string.ascii_lowercase, k=rng.randint(5, 20))),
            "email": f"user{rng.randint(0,9999)}@example.com",
            "data": ''.join(rng.choices(string.ascii_letters + ' ', k=rng.randint(50, 150))),
        }
        parts.append(json.dumps(obj).encode())
    return b'\n'.join(parts)[:size]

def make_binary_compressible(size: int) -> bytes:
    """Binary data with patterns (zeros, repeating words)"""
    chunk = bytes(range(256)) * 4 + b'\x00' * 64 + b'\xff' * 32
    return (chunk * (size // len(chunk) + 1))[:size]

def make_binary_incompressible(size: int) -> bytes:
    """Random-looking data (hard to compress)"""
    import hashlib
    out = bytearray()
    seed = b"benchmark"
    while len(out) < size:
        seed = hashlib.sha256(seed).digest()
        out.extend(seed)
    return bytes(out[:size])

def make_mixed(size: int) -> bytes:
    """Mix of text + binary (like a tar archive)"""
    parts = []
    remaining = size
    while remaining > 0:
        chunk_size = min(remaining, 1024)
        choice = (size - remaining) // 1024 % 4
        if choice == 0:
            parts.append(make_text_repetitive(chunk_size))
        elif choice == 1:
            parts.append(make_text_natural(chunk_size))
        elif choice == 2:
            parts.append(make_binary_compressible(chunk_size))
        else:
            parts.append(make_binary_incompressible(chunk_size))
        remaining -= chunk_size
    return b''.join(parts)


# ── Compressor wrappers ───────────────────────────────────────────

def _compress_mujo(data: bytes) -> bytes:
    from mujopresa_core import compress_bytes as cb
    return cb(data, 15, 32)

def _decompress_mujo(data: bytes, orig_size: int) -> bytes:
    from mujopresa_core import decompress_bytes as db
    return bytes(db(data, orig_size, 15))

def _compress_gzip(data: bytes) -> bytes:
    import gzip
    return gzip.compress(data, compresslevel=6)

def _decompress_gzip(data: bytes) -> bytes:
    import gzip
    return gzip.decompress(data)

def _compress_bzip2(data: bytes) -> bytes:
    import bz2
    return bz2.compress(data, compresslevel=9)

def _decompress_bzip2(data: bytes) -> bytes:
    import bz2
    return bz2.decompress(data)

def _compress_zip(data: bytes) -> bytes:
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr('data', data)
    return buf.getvalue()

def _decompress_zip(data: bytes) -> bytes:
    import zipfile
    buf = io.BytesIO(data)
    with zipfile.ZipFile(buf, 'r') as zf:
        return zf.read('data')

def _compress_zstd(data: bytes) -> bytes:
    import pyzstd
    return pyzstd.compress(data, 3)

def _decompress_zstd(data: bytes) -> bytes:
    import pyzstd
    return pyzstd.decompress(data)


# ── Benchmark runner ──────────────────────────────────────────────

COMPRESSORS = [
    ("MujoPresa", _compress_mujo, _decompress_mujo, True),
    ("gzip -6",   _compress_gzip, _decompress_gzip, False),
    ("bzip2 -9",  _compress_bzip2, _decompress_bzip2, False),
    ("zip -6",    _compress_zip, _decompress_zip, False),
    ("zstd -3",   _compress_zstd, _decompress_zstd, False),
]

TEST_FILES = [
    ("Text (repetitive)",    make_text_repetitive, 256 * 1024),
    ("Text (natural)",       make_text_natural, 256 * 1024),
    ("Binary (patterns)",    make_binary_compressible, 256 * 1024),
    ("Binary (random)",      make_binary_incompressible, 256 * 1024),
    ("Mixed content",        make_mixed, 256 * 1024),
]


def run_benchmark():
    results = []

    for tname, tgen, tsize in TEST_FILES:
        print(f"\n  Generating '{tname}' ({tsize:,} B)...", end="", flush=True)
        data = tgen(tsize)
        print(" done.")

        for cname, cfunc, dfunc, needs_orig in COMPRESSORS:
            # Warmup
            _ = cfunc(data[:1024])

            # Compress
            t0 = time.perf_counter()
            compressed = cfunc(data)
            t1 = time.perf_counter()
            comp_time = t1 - t0

            # Decompress
            t2 = time.perf_counter()
            if needs_orig:
                decompressed = dfunc(compressed, len(data))
            else:
                decompressed = dfunc(compressed)
            t3 = time.perf_counter()
            decomp_time = t3 - t2

            # Verify
            if data != decompressed:
                print(f"    [!] {cname}: DATA MISMATCH!")
                continue

            ratio = len(compressed) / len(data) * 100
            savings = (1 - len(compressed) / len(data)) * 100
            results.append((tname, cname, len(data), len(compressed),
                           savings, comp_time, decomp_time))

    return results


def print_table(results):
    # Group by test file
    tests = sorted(set(r[0] for r in results))
    compressors = sorted(set(r[1] for r in results), key=lambda x: (
        0 if 'Mujo' in x else 1 if 'zstd' in x else 2 if 'gzip' in x else 3 if 'zip' in x else 4
    ))

    print("\n" + "=" * 120)
    print("  KOMPRESIJA BENCHMARK — MujoPresa vs gzip / bzip2 / zip / zstd")
    print("  Test data: 256 KB per file")
    print("=" * 120)

    for test_name in tests:
        print(f"\n  ┌─ {test_name}")
        print(f"  │ {'Kompresor':<18} {'Original':>10} {'Komprim':>10} {'Ušteda':>8} "
              f"{'Compress':>10} {'Decompress':>10} {'Br. (MB/s)':>10}")
        print(f"  │ {'':-<18} {'-'*10:>10} {'-'*10:>10} {'-'*8:>8} "
              f"{'-'*10:>10} {'-'*10:>10} {'-'*10:>10}")

        for r in sorted(results, key=lambda x: (
            0 if test_name == x[0] and 'Mujo' in x[1] else
            1 if test_name == x[0] and 'zstd' in x[1] else
            2 if test_name == x[0] and 'gzip' in x[1] else
            3 if test_name == x[0] and 'bzip2' in x[1] else
            4 if test_name == x[0] and 'zip' in x[1] else 99
        )):
            if r[0] != test_name:
                continue
            tname, cname, orig, comp, savings, ct, dt = r
            speed = (orig / 1024 / 1024) / ct if ct > 0 else 0
            print(f"  │ {cname:<18} {orig:>10,} {comp:>10,} "
                  f"{savings:>7.2f}% {ct:>8.4f}s {dt:>8.4f}s {speed:>8.2f}")

    # Summary: best ratio per test
    print(f"\n  ┌─ REZIME — najbolji odnos kompresije")
    print(f"  │ {'Test':<30} {'1. mjesto':>20} {'2. mjesto':>20} {'3. mjesto':>20}")
    print(f"  │ {'':-<30} {'-'*20:>20} {'-'*20:>20} {'-'*20:>20}")
    for test_name in tests:
        ranked = sorted([r for r in results if r[0] == test_name], key=lambda x: x[4], reverse=True)
        top3 = ranked[:3]
        line = f"  │ {test_name:<30}"
        for _, cname, _, _, savings, _, _ in top3:
            line += f" {cname} ({savings:.1f}%)".rjust(20)
        print(line)

    # Overall ranking
    print(f"\n  ┌─ UKUPNI REZULTATI (prosjek svih testova)")
    print(f"  │ {'Kompresor':<18} {'Prosjek uštede':>16} {'Prosj. compress':>16} "
          f"{'Prosj. decompress':>16} {'Ukupno vrijeme':>16}")
    print(f"  │ {'':-<18} {'-'*16:>16} {'-'*16:>16} {'-'*16:>16} {'-'*16:>16}")

    comp_averages = {}
    for cname in set(r[1] for r in results):
        cr = [r for r in results if r[1] == cname]
        avg_savings = sum(r[4] for r in cr) / len(cr)
        avg_ct = sum(r[5] for r in cr) / len(cr)
        avg_dt = sum(r[6] for r in cr) / len(cr)
        total_time = sum(r[5] + r[6] for r in cr)
        comp_averages[cname] = (avg_savings, avg_ct, avg_dt, total_time)

    for cname, (avg_savings, avg_ct, avg_dt, total_time) in sorted(
            comp_averages.items(), key=lambda x: x[1][0], reverse=True):
        print(f"  │ {cname:<18} {avg_savings:>14.2f}% "
              f"{avg_ct:>13.4f}s {avg_dt:>14.4f}s {total_time:>13.4f}s")

    print()
    print("  (testirano na 256 KB po testu, Python 3.12, Windows)")
    print()


if __name__ == '__main__':
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    results = run_benchmark()
    print_table(results)
