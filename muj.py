#!/usr/bin/env python3
"""
MujoPresa Archiver v1.0 – .muj archive tool (like zip/rar)
===========================================================
Multi-file archiver using MujoPresa LZ77 + range coding compression.
Commands: create, extract, list

Usage:
  muj create archive.muj file1.txt file2.txt dir/
  muj create --solid archive.muj src/ data/
  muj extract archive.muj [output_dir/]
  muj list archive.muj
  muj list --verbose archive.muj
"""

import os
import sys
import struct
import zlib
from pathlib import Path
from typing import List, Optional, Tuple

# -----------------------------------------------------------------
# Import compression engine
# -----------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mujopresa_core import compress_bytes, decompress_bytes, CYTHON_ACTIVE

HAS_TQDM = False
try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    pass

# -----------------------------------------------------------------
# Archive format
# -----------------------------------------------------------------
ARCHIVE_MAGIC = b"MUJ1"
FLAG_SOLID = 0x01
FLAG_DIR = 0x01
FLAG_STORED = 0x02

GLOBAL_HEADER_FMT = "<4s I B 7x"           # magic(4) + num_files(4) + flags(1) + reserved(7) = 16
GLOBAL_HEADER_SIZE = struct.calcsize(GLOBAL_HEADER_FMT)

ENTRY_HEADER_FMT = "<H Q Q I Q H B B B"    # path_len(2) + orig_size(8) + comp_size(8) + crc32(4) + mtime(8) + mode(2) + wbits(1) + chain(1) + flags(1) = 35
ENTRY_HEADER_SIZE = struct.calcsize(ENTRY_HEADER_FMT)


class ArchiveEntry:
    __slots__ = ('path', 'orig_size', 'comp_size', 'crc32', 'mtime',
                 'mode', 'window_bits', 'max_chain', 'flags')

    def __init__(self, path: str = "", orig_size: int = 0, comp_size: int = 0,
                 crc32: int = 0, mtime: float = 0, mode: int = 0o644,
                 window_bits: int = 15, max_chain: int = 32, flags: int = 0):
        self.path = path
        self.orig_size = orig_size
        self.comp_size = comp_size
        self.crc32 = crc32
        self.mtime = mtime
        self.mode = mode
        self.window_bits = window_bits
        self.max_chain = max_chain
        self.flags = flags

    def is_dir(self) -> bool:
        return (self.flags & FLAG_DIR) != 0

    def is_stored(self) -> bool:
        return (self.flags & FLAG_STORED) != 0

    def data_size(self) -> int:
        return self.orig_size if self.is_stored() else self.comp_size


# -----------------------------------------------------------------
# Format I/O helpers
# -----------------------------------------------------------------
def _pack_entry(entry: ArchiveEntry) -> bytes:
    path_bytes = entry.path.encode('utf-8')
    return struct.pack(ENTRY_HEADER_FMT,
                       len(path_bytes),
                       entry.orig_size,
                       entry.comp_size,
                       entry.crc32,
                       int(entry.mtime),
                       entry.mode & 0xFFFF,
                       entry.window_bits,
                       entry.max_chain & 0xFF,
                       entry.flags & 0xFF) + path_bytes


def _read_entries(data: bytes) -> Tuple[List[ArchiveEntry], int]:
    if len(data) < GLOBAL_HEADER_SIZE:
        return [], 0
    offset = GLOBAL_HEADER_SIZE
    entries = []
    for _ in range(num_files := struct.unpack_from("<I", data, 4)[0]):
        if offset + ENTRY_HEADER_SIZE > len(data):
            break
        path_len, orig_size, comp_size, crc32_val, mtime_int, mode, wbits, chain, flags = \
            struct.unpack_from(ENTRY_HEADER_FMT, data, offset)
        if offset + ENTRY_HEADER_SIZE + path_len > len(data):
            break
        path = data[offset + ENTRY_HEADER_SIZE: offset + ENTRY_HEADER_SIZE + path_len].decode('utf-8')
        entries.append(ArchiveEntry(
            path=path, orig_size=orig_size, comp_size=comp_size,
            crc32=crc32_val, mtime=mtime_int, mode=mode,
            window_bits=wbits, max_chain=chain, flags=flags
        ))
        offset += ENTRY_HEADER_SIZE + path_len
    return entries, offset


# -----------------------------------------------------------------
# Archive creation
# -----------------------------------------------------------------
def _collect(input_paths: List[str]) -> List[Tuple[str, bool]]:
    items = []
    for inp in input_paths:
        p = Path(inp)
        if not p.exists():
            print(f"[-] Skipping '{inp}': not found")
            continue
        if p.is_dir():
            items.append((str(p.as_posix()) + '/', True))
            for f in sorted(p.rglob('*')):
                if f.is_dir():
                    items.append((str(f.as_posix()) + '/', True))
                else:
                    items.append((str(f.as_posix()), False))
        else:
            items.append((str(p.as_posix()), False))
    return items


def create_archive(output_path: str, input_paths: List[str],
                   window_bits: int = 15, max_chain: int = 32,
                   solid: bool = False, use_lazy: bool = True,
                   use_repcache: bool = True, show_progress: bool = True,
                   store_small: bool = False) -> int:
    collected = _collect(input_paths)
    if not collected:
        print("[-] No files to archive")
        return 1

    entries = []
    file_data = []  # parallel list, None for dirs
    total_orig = 0

    for relpath, is_dir in collected:
        if is_dir:
            entry = ArchiveEntry(path=relpath, flags=FLAG_DIR, mode=0o755)
            try:
                s = os.stat(relpath.rstrip('/'))
                entry.mtime = s.st_mtime
            except OSError:
                pass
            entries.append(entry)
            file_data.append(None)
        else:
            try:
                with open(relpath, 'rb') as fh:
                    data = fh.read()
            except OSError as e:
                print(f"[-] Error reading '{relpath}': {e}")
                continue
            s = os.stat(relpath)
            entry = ArchiveEntry(
                path=relpath, orig_size=len(data),
                crc32=zlib.crc32(data) & 0xFFFFFFFF,
                mtime=s.st_mtime, mode=s.st_mode & 0o777,
                window_bits=window_bits, max_chain=max_chain,
            )
            entries.append(entry)
            file_data.append(data)
            total_orig += len(data)

    ndirs = sum(1 for e in entries if e.is_dir())
    nfiles = len(entries) - ndirs

    if show_progress:
        print(f"[+] MujoPresa Archiver v1.0  |  Files: {nfiles}  Directories: {ndirs}  |  Data: {total_orig:,} B")
        print(f"[+] Window: {1<<window_bits}  Chain: {max_chain}  Solid: {solid}")

    archive_flags = 0
    if solid:
        archive_flags |= FLAG_SOLID

    data_indices = [(i, e, d) for i, (e, d) in enumerate(zip(entries, file_data)) if d is not None]

    with open(output_path, 'wb') as f:
        f.write(struct.pack(GLOBAL_HEADER_FMT, ARCHIVE_MAGIC, len(entries), archive_flags))

        if solid:
            # Write all headers, then all data compressed as one stream
            for e in entries:
                f.write(_pack_entry(e))
            if data_indices:
                all_data = b''.join(d for _, _, d in data_indices)
                if show_progress:
                    print(f"    Solid compressing {nfiles} files ({len(all_data):,} B)...")
                compressed = compress_bytes(all_data, window_bits, max_chain,
                                             use_lazy, use_repcache, None)
                if show_progress:
                    pct = len(compressed)/max(1,len(all_data))*100
                    print(f"    -> {len(compressed):,} B ({pct:.1f}%)")
                f.write(compressed)
        else:
            # Write headers first, then data
            header_offsets = []
            for e in entries:
                header_offsets.append(f.tell())
                f.write(_pack_entry(e))

            for idx, e, data in data_indices:
                if store_small and len(data) < 64:
                    compressed = data
                    e.flags |= FLAG_STORED
                    e.comp_size = len(data)
                else:
                    if show_progress:
                        print(f"    [{e.path}] {len(data):,} B", end="")
                    compressed = compress_bytes(data, e.window_bits, e.max_chain,
                                                 use_lazy, use_repcache, None)
                    e.comp_size = len(compressed)
                    if show_progress:
                        pct = len(compressed)/max(1,len(data))*100
                        print(f" -> {len(compressed):,} B ({pct:.1f}%)")

                pos = f.tell()
                f.seek(header_offsets[idx])
                f.write(_pack_entry(e))
                f.seek(pos)
                f.write(compressed)

    archive_size = os.path.getsize(output_path)
    if total_orig > 0:
        ratio = (1 - archive_size / total_orig) * 100
        print(f"[OK] {output_path}: {total_orig:,} -> {archive_size:,} B ({ratio:.2f}%)")
    else:
        print(f"[OK] {output_path} ({archive_size:,} B)")
    return 0


# -----------------------------------------------------------------
# Archive listing
# -----------------------------------------------------------------
def list_archive(archive_path: str, verbose: bool = False) -> int:
    if not os.path.exists(archive_path):
        print(f"[-] '{archive_path}' not found")
        return 1

    data = Path(archive_path).read_bytes()
    if len(data) < GLOBAL_HEADER_SIZE:
        print("[-] Invalid/corrupt archive")
        return 1

    magic, num_files, flags = struct.unpack_from(GLOBAL_HEADER_FMT, data, 0)
    if magic != ARCHIVE_MAGIC:
        print(f"[-] Not a MujoPresa archive")
        return 1

    entries, _ = _read_entries(data)
    solid = bool(flags & FLAG_SOLID)

    print(f"\nArchive: {archive_path}  ({num_files} entries, {'solid' if solid else 'normal'})")
    print(f"{'='*70}")
    print(f"  {'Path':<45} {'Size':>8} {'Comp':>8} {'Ratio':>6} {'CRC':>8}")
    print(f"  {'-'*45} {'-'*8} {'-'*8} {'-'*6} {'-'*8}")

    total_orig = 0
    total_comp = 0
    for e in entries:
        if e.is_dir():
            display_path = e.path.rstrip('/') + '/'
            print(f"  {display_path:<45} {'<DIR>':>8}")
        else:
            csize = e.data_size()
            ratio = (1 - csize / e.orig_size) * 100 if e.orig_size else 0
            print(f"  {e.path:<45} {e.orig_size:>8,} {csize:>8,} {ratio:>5.1f}% {e.crc32:08x}")
            total_orig += e.orig_size
            total_comp += csize

    if total_orig:
        ratio = (1 - total_comp / total_orig) * 100
        print(f"  {'-'*45} {'-'*8} {'-'*8} {'-'*6} {'-'*8}")
        print(f"  {'TOTAL':<45} {total_orig:>8,} {total_comp:>8,} {ratio:>5.1f}%")
    print()
    return 0


# -----------------------------------------------------------------
# Archive extraction
# -----------------------------------------------------------------
def extract_archive(archive_path: str, output_dir: str = ".",
                    show_progress: bool = True) -> int:
    if not os.path.exists(archive_path):
        print(f"[-] '{archive_path}' not found")
        return 1

    raw = Path(archive_path).read_bytes()
    if len(raw) < GLOBAL_HEADER_SIZE:
        print("[-] Invalid/corrupt archive")
        return 1

    magic, num_files, flags = struct.unpack_from(GLOBAL_HEADER_FMT, raw, 0)
    if magic != ARCHIVE_MAGIC:
        print(f"[-] Not a MujoPresa archive")
        return 1

    entries, data_start = _read_entries(raw)
    solid = bool(flags & FLAG_SOLID)

    if solid:
        file_entries = [e for e in entries if not e.is_dir()]
        if file_entries:
            total_orig = sum(e.orig_size for e in file_entries)
            if show_progress:
                print(f"[+] Solid decompressing {len(file_entries)} files ({total_orig:,} B)...")
            solid_data = raw[data_start:]
            try:
                reconstructed = decompress_bytes(solid_data, total_orig,
                                                  file_entries[0].window_bits)
            except Exception as e:
                print(f"[-] Decompression failed: {e}")
                return 1
            pos = 0
            for e in file_entries:
                chunk = reconstructed[pos:pos + e.orig_size]
                pos += e.orig_size
                _write_file(e, chunk, output_dir)
        for e in entries:
            if e.is_dir():
                _write_file(e, b'', output_dir)
    else:
        offset = data_start
        for e in entries:
            if e.is_dir():
                _write_file(e, b'', output_dir)
            else:
                chunk = raw[offset:offset + e.data_size()]
                if not e.is_stored():
                    if show_progress:
                        print(f"    Decompressing: {e.path}")
                    chunk = decompress_bytes(chunk, e.orig_size, e.window_bits)
                _write_file(e, chunk, output_dir)
                offset += e.data_size()
                if zlib.crc32(chunk) & 0xFFFFFFFF != e.crc32:
                    print(f"    [!] CRC mismatch: {e.path}")

    print(f"[OK] Extracted {num_files} entries to '{output_dir}'")
    return 0


def _write_file(entry: ArchiveEntry, data: bytes, output_dir: str):
    dest = Path(output_dir) / entry.path
    if entry.is_dir():
        dest.mkdir(parents=True, exist_ok=True)
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    if entry.mtime:
        try:
            os.utime(dest, (entry.mtime, entry.mtime))
        except Exception:
            pass


# -----------------------------------------------------------------
# CLI
# -----------------------------------------------------------------
def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="MujoPresa Archiver – .muj archive tool (like zip/rar)",
    )
    sub = parser.add_subparsers(dest='mode', required=True)

    p = sub.add_parser('create', help='Create .muj archive')
    p.add_argument('output', help='Output .muj file')
    p.add_argument('input', nargs='+', help='Files/directories to archive')
    p.add_argument('--window-bits', type=int, choices=range(10, 21), default=15,
                   help='Sliding window size (2^N, 10-20, default 15=32KB)')
    p.add_argument('--chain', type=int, default=32, help='LZ chain depth (default 32)')
    p.add_argument('--solid', action='store_true', help='Solid compression (all files as one stream)')
    p.add_argument('--no-lazy', action='store_true', help='Disable lazy matching')
    p.add_argument('--no-repcache', action='store_true', help='Disable rep-cache')
    p.add_argument('--no-progress', action='store_true')
    p.add_argument('--store-small', action='store_true', help='Store files <64B uncompressed')

    p = sub.add_parser('extract', aliases=['x'], help='Extract .muj archive')
    p.add_argument('input', help='Archive file')
    p.add_argument('output', nargs='?', default='.', help='Output directory (default: .)')
    p.add_argument('--no-progress', action='store_true')

    p = sub.add_parser('list', aliases=['ls'], help='List archive contents')
    p.add_argument('input', help='Archive file')
    p.add_argument('--verbose', '-v', action='store_true')

    p = sub.add_parser('selftest', help='Quick engine roundtrip check')

    args = parser.parse_args()

    if args.mode == 'create':
        return create_archive(
            args.output, args.input,
            window_bits=args.window_bits,
            max_chain=args.chain,
            solid=args.solid,
            use_lazy=not args.no_lazy,
            use_repcache=not args.no_repcache,
            show_progress=not args.no_progress,
            store_small=args.store_small,
        )
    elif args.mode in ('extract', 'x'):
        return extract_archive(
            args.input, args.output or '.',
            show_progress=not args.no_progress,
        )
    elif args.mode in ('list', 'ls'):
        return list_archive(args.input, verbose=args.verbose)
    elif args.mode == 'selftest':
        return selftest()

    return 0


def selftest() -> int:
    import random
    rng = random.Random(12345)
    cases = [
        b'',
        bytes(64),
        b'hello world ' * 100,
        bytes(rng.randrange(256) for _ in range(5000)),
    ]
    for i, data in enumerate(cases):
        comp = compress_bytes(data, 15, 16)
        out = bytes(decompress_bytes(comp, len(data), 15))
        if out != data:
            print(f'[-] selftest case {i}: FAIL')
            return 1
        print(f'[+] case {i}: {len(data)} -> {len(comp)} OK')
    print('[OK] selftest passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
