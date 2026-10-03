#!/usr/bin/env python3
"""MujoPresa roundtrip tests – deterministic, no external deps.

Run: python -m unittest discover -s tests -v
"""
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mujopresa_core import compress_bytes, decompress_bytes, __version__


def rt(data: bytes, window_bits=15, max_chain=16) -> bytes:
    comp = compress_bytes(data, window_bits, max_chain)
    return bytes(decompress_bytes(comp, len(data), window_bits))


class TestRoundtrip(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(rt(b''), b'')

    def test_single(self):
        self.assertEqual(rt(bytes([42])), bytes([42]))

    def test_zeros(self):
        self.assertEqual(rt(bytes(1000)), bytes(1000))

    def test_incremental(self):
        d = bytes(range(256)) * 4
        self.assertEqual(rt(d), d)

    def test_repeated(self):
        d = b'hello world this is a test ' * 500
        self.assertEqual(rt(d), d)

    def test_random_sizes(self):
        rng = random.Random(12345)
        for n in [200, 1000, 10000, 50000]:
            d = bytes(rng.randrange(256) for _ in range(n))
            self.assertEqual(rt(d), d, f'n={n}')

    def test_chain_depths(self):
        rng = random.Random(99)
        d = bytes(rng.randrange(256) for _ in range(5000))
        for chain in [4, 16, 32]:
            self.assertEqual(rt(d, 15, chain), d, f'chain={chain}')

    def test_window_bits(self):
        rng = random.Random(7)
        d = bytes(rng.randrange(256) for _ in range(8000))
        for wb in [10, 12, 15]:
            self.assertEqual(rt(d, wb, 16), d, f'wb={wb}')

    def test_self_source(self):
        src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           'mujopresa_core.py')
        with open(src, 'rb') as fh:
            d = fh.read()
        self.assertEqual(rt(d), d)

    def test_version(self):
        self.assertRegex(__version__, r'^\d+\.\d+\.\d+$')


if __name__ == '__main__':
    unittest.main()
