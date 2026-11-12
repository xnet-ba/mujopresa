<!-- xnet-ba-matrix-header-v1 -->
<div align="center">

<img src="https://capsule-render.vercel.app/api?type=waving&color=00FF41&height=140&section=header&text=mujopresa&fontColor=000000&fontSize=42&fontAlignY=32&desc=eksperimentalni%20lossless%20kompresor%20napisan%20u%20Pythonu&descAlignY=55" width="100%" alt="mujopresa"/>

[![Typing SVG](https://readme-typing-svg.demolab.com?font=Fira+Code&size=16&duration=2800&pause=800&color=00FF41&center=true&vCenter=true&width=700&lines=xnet-ba+%3A%3A+open-source+AI+iz+BiH;citaj+README+pa+pokreni;PR+dobrodosli)](https://github.com/xnet-ba/mujopresa)

[![Stars](https://img.shields.io/github/stars/xnet-ba/mujopresa?style=flat-square&color=00FF41&labelColor=000000)](https://github.com/xnet-ba/mujopresa/stargazers)
[![License](https://img.shields.io/github/license/xnet-ba/mujopresa?style=flat-square&color=00FF41&labelColor=000000)](LICENSE)
[![Top lang](https://img.shields.io/github/languages/top/xnet-ba/mujopresa?style=flat-square&color=00FF41&labelColor=000000)](https://github.com/xnet-ba/mujopresa)
[![Last commit](https://img.shields.io/github/last-commit/xnet-ba/mujopresa?style=flat-square&color=00FF41&labelColor=000000)](https://github.com/xnet-ba/mujopresa/commits)
[![matrix](https://img.shields.io/badge/MATRIX-xnet--ba-00FF41?style=flat-square&labelColor=000000)](https://github.com/xnet-ba)

</div>

---
# MujoPresa

MujoPresa je eksperimentalni lossless kompresor napisan u Pythonu, koji kombinuje LZ77 algoritam, adaptivno statističko modeliranje i range coding.

Projekat je dizajniran kao samostalni kompresor sa vlastitim `.mujo` arhivskim formatom, automatskim Cython ubrzanjem i Python fallback implementacijom.

## Sadržaj

- [🚀 Glavne karakteristike](#-glavne-karakteristike)
- [🧠 Kako radi](#-kako-radi)
- [▶️ Korištenje](#-korištenje)
- [⚡ Cython ubrzanje](#-cython-ubrzanje)
- [📦 Instalacija](#-instalacija)
- [📁 .mujo format](#-mujo-format)
- [🔐 Integritet](#-integritet)
- [📊 Cilj projekta](#-cilj-projekta)
- [⚠️ Status](#-status)
- [🧪 Planirani razvoj](#-planirani-razvoj)
- [v1.1.0 – modularno jezgro](#v110--modularno-jezgro)


## Sadržaj

- [🚀 Glavne karakteristike](#-glavne-karakteristike)
- [🧠 Kako radi](#-kako-radi)
- [▶️ Korištenje](#-korištenje)
- [⚡ Cython ubrzanje](#-cython-ubrzanje)
- [📦 Instalacija](#-instalacija)
- [📁 .mujo format](#-mujo-format)
- [🔐 Integritet](#-integritet)
- [📊 Cilj projekta](#-cilj-projekta)
- [⚠️ Status](#-status)
- [🧪 Planirani razvoj](#-planirani-razvoj)
- [v1.1.0 – modularno jezgro](#v110--modularno-jezgro)


## 🚀 Glavne karakteristike

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

## 🧠 Kako radi

Osnovni MujoPresa pipeline:

```
```
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
```
```


## ▶️ Korištenje

```bash
```bash
Kompresija: python3 mujopresa.py compress -i input.txt
Dobija se: input.txt.mujo
```
```

```bash
```bash
Dekompresija: python3 mujopresa.py decompress -i input.txt.mujo
```
```

Metapodaci se mogu pregledati bez dekompresije: python3 mujopresa.py info -i input.txt.mujo


## ⚡ Cython ubrzanje

MujoPresa prvo pokušava koristiti Cython backend.
Ako su dostupni:

- Cython
- Python development headers
- C/C++ compiler

jezgro se automatski kompajlira i koristi kao ubrzana implementacija.

Ako Cython ili kompajler nisu dostupni, MujoPresa automatski prelazi na čisti Python backend.

Cython + C compiler
```
```
        │
        ├── DA ──► ubrzano C jezgro
        │
        └── NE ──► Python fallback
```
```


## 📦 Instalacija

Potrebno je: Python3

Preporučeno za ubrzano jezgro: pip install cython

Opcionalni engine-i:
```bash
```bash
pip install zstandard
```
```
```bash
```bash
pip install brotli
```
```


## 📁 .mujo format

MujoPresa koristi vlastiti arhivski format sa magic oznakom: MUJO

Arhiva sadrži osnovne metapodatke potrebne za dekodiranje, originalno ime fajla, compression podatke i CRC32 checksum.

Tipična ekstenzija: .mujo


## 🔐 Integritet

MujoPresa koristi CRC32 za provjeru integriteta rekonstruisanih podataka.
Ako checksum ne odgovara, dekompresija završava greškom:

```text
```text
[KRITICNO] Provjera integriteta neuspjela.
```
```

CRC32 je namijenjen prvenstveno detekciji slučajne korupcije podataka, a ne kriptografskoj zaštiti.


## 📊 Cilj projekta

MujoPresa je prvenstveno istraživački i eksperimentalni compression projekat.

Cilj je istraživati kombinaciju:

- LZ77
- adaptivnog statističkog modeliranja
- order-N konteksta
- Fenwick struktura
- range codinga
- rep-distance predikcije
- lazy matchinga
- Cython optimizacije
- paralelne kompresije


## ⚠️ Status

MujoPresa je trenutno eksperimentalni razvojni projekat.
Ne treba ga posmatrati kao zamjenu za dobro testirane standardne formate kao što su:

- Zstandard
- LZMA/XZ
- Brotli

Rezultate treba porediti benchmark testovima na različitim vrstama podataka.


## 🧪 Planirani razvoj

Mogući budući razvoj:

- streaming compression/decompression
- veći i fleksibilniji window
- bolji match finder
- optimal parsing
- napredniji distance model
- bolji rep cache
- Order-3/Order-4 modeli
- bolji context mixing
- multithreaded decompression
- metadata versioning
- jači integrity hash
- benchmark suite
- fuzz testing
- archive recovery
- self-test mode
- GUI/web interface


## v1.1.0 – modularno jezgro

- `mujopresa_core.py` – stabilno LZ77 + range coding jezgro (v1.1.0)
- `muj.py` – `.muj` arhiver (`create` / `extract` / `list`)
- `benchmark.py` – poređenje sa gzip / bzip2 / zip / zstd
- `mujopresa.py` – legacy monolit, ostaje radi kompatibilnosti

---

<div align="center">
<img src="https://raw.githubusercontent.com/xnet-ba/xnet-ba/main/assets/divider.svg" width="100%" alt=""/>
<sup>part of <a href="https://github.com/xnet-ba">xnet-ba matrix</a> — open-source AI iz BiH</sup>
</div>
