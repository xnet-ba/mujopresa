#!/usr/bin/env python3
"""MujoPresa fuzz – fixed seeds, fast sizes.

Run: python -m unittest discover -s tests -v
"""
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mujopresa_core import compress_bytes, decompress_bytes


def rand_bytes(rng: random.Random, n: int, alphabet: int) -> bytes:
    return bytes(rng.randrange(alphabet) for _ in range(n))


class TestFuzz(unittest.TestCase):
    def test_seeds(self):
        for seed in [1, 2, 3]:
            rng = random.Random(seed)
            for _ in range(5):
                n = rng.randrange(1, 3000)
                alphabet = rng.choice([2, 4, 16, 256])
                wb = rng.choice([10, 12, 15])
                chain = rng.choice([4, 16, 32])
                d = rand_bytes(rng, n, alphabet)
                comp = compress_bytes(d, wb, chain)
                out = bytes(decompress_bytes(comp, len(d), wb))
                self.assertEqual(out, d, f'seed={seed} n={n} alpha={alphabet} wb={wb} chain={chain}')

    def test_low_entropy(self):
        rng = random.Random(2026)
        for alpha in [2, 3]:
            d = rand_bytes(rng, 20000, alpha)
            comp = compress_bytes(d, 15, 32)
            out = bytes(decompress_bytes(comp, len(d), 15))
            self.assertEqual(out, d)
            self.assertLess(len(comp), len(d), 'low entropy must shrink')


if __name__ == '__main__':
    unittest.main()
