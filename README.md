# MujoPresa

MujoPresa je eksperimentalni lossless kompresor napisan u Pythonu, koji kombinuje LZ77 algoritam, adaptivno statističko modeliranje i range coding.

Projekat je dizajniran kao samostalni kompresor sa vlastitim `.mujo` arhivskim formatom, automatskim Cython ubrzanjem i Python fallback implementacijom.

🚀 Glavne karakteristike

- LZ77 kompresija
- Hash-chain match finder
- Lazy matching
- Rep-distance cache
- Adaptive Order-0, Order-1 i Order-2 modeli
- Fenwick tree statistički modeli
- Range coder
- Cython/C ubrzano jezgro
- Automatski Python fallback
- Vlastiti `.mujo` arhivski format
- CRC32 provjera integriteta
- Chunked kompresija
- Paralelna kompresija preko multiprocessing-a
- Dictionary/history carryover između chunkova
- Pregled metapodataka arhive bez dekompresije
- Više compression engine-a:
  - `custom`
  - `zstd`
  - `lzma`
  - `brotli`

🧠 Kako radi

Osnovni MujoPresa pipeline:

                  ┌──────────────────┐
                  │      INPUT       │
                  └────────┬─────────┘
                           │
                           ▼
                  ┌──────────────────┐
                  │  LZ77 MATCHER    │
                  │                  │
                  │ Hash Chain       │
                  │ Lazy Matching    │
                  │ Rep Distances    │
                  └────────┬─────────┘
                           │
              ┌────────────┴────────────┐
              │                         │
              ▼                         ▼
       ┌──────────────┐        ┌────────────────┐
       │   LITERAL    │        │ MATCH / REP    │
       └──────┬───────┘        └───────┬────────┘
              │                        │
              ▼                        ▼
       ┌──────────────┐        ┌────────────────┐
       │ Order-N      │        │ Length         │
       │ Context      │        │ Distance       │
       │ Model        │        │ Rep Index      │
       └──────┬───────┘        └───────┬────────┘
              │                        │
              └───────────┬────────────┘
                          ▼
                 ┌──────────────────┐
                 │   RANGE CODER    │
                 └────────┬─────────┘
                          │
                          ▼
                 ┌──────────────────┐
                 │   .MUJO ARCHIVE  │
                 └──────────────────┘
