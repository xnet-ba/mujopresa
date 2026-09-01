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


▶️ Korištenje

Kompresija: python3 mujopresa.py compress -i input.txt
Dobija se: input.txt.mujo

Dekompresija: python3 mujopresa.py decompress -i input.txt.mujo

Metapodaci se mogu pregledati bez dekompresije: python3 mujopresa.py info -i input.txt.mujo


⚡ Cython ubrzanje

MujoPresa prvo pokušava koristiti Cython backend.
Ako su dostupni:

-Cython
-Python development headers
-C/C++ compiler

jezgro se automatski kompajlira i koristi kao ubrzana implementacija.

Ako Cython ili kompajler nisu dostupni, MujoPresa automatski prelazi na čisti Python backend.

Cython + C compiler
        │
        ├── DA ──► ubrzano C jezgro
        │
        └── NE ──► Python fallback


📦 Instalacija

Potrebno je: Python3

Preporučeno za ubrzano jezgro: pip install cython

Opcionalni engine-i:
pip install zstandard
pip install brotli


📁 .mujo format

MujoPresa koristi vlastiti arhivski format sa magic oznakom: MUJO

Arhiva sadrži osnovne metapodatke potrebne za dekodiranje, originalno ime fajla, compression podatke i CRC32 checksum.

Tipična ekstenzija: .mujo


🔐 Integritet

MujoPresa koristi CRC32 za provjeru integriteta rekonstruisanih podataka.
Ako checksum ne odgovara, dekompresija završava greškom:

[KRITICNO] Provjera integriteta neuspjela.

CRC32 je namijenjen prvenstveno detekciji slučajne korupcije podataka, a ne kriptografskoj zaštiti.


📊 Cilj projekta

MujoPresa je prvenstveno istraživački i eksperimentalni compression projekat.

Cilj je istraživati kombinaciju:

-LZ77
-adaptivnog statističkog modeliranja
-order-N konteksta
-Fenwick struktura
-range codinga
-rep-distance predikcije
-lazy matchinga
-Cython optimizacije
-paralelne kompresije


⚠️ Status

MujoPresa je trenutno eksperimentalni razvojni projekat.
Ne treba ga posmatrati kao zamjenu za dobro testirane standardne formate kao što su:

-Zstandard
-LZMA/XZ
-Brotli

Rezultate treba porediti benchmark testovima na različitim vrstama podataka.


🧪 Planirani razvoj

Mogući budući razvoj:

streaming compression/decompression
veći i fleksibilniji window
bolji match finder
optimal parsing
napredniji distance model
bolji rep cache
Order-3/Order-4 modeli
bolji context mixing
multithreaded decompression
metadata versioning
jači integrity hash
benchmark suite
fuzz testing
archive recovery
self-test mode
GUI/web interface

