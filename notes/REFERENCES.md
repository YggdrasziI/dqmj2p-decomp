# References

## Tools

- [AetiasHax/ds-decomp](https://github.com/AetiasHax/ds-decomp) (`dsd`, MIT): ROM
  extract, analysis, delink, linker script, ROM rebuild, checks, objdiff config. Docs:
  `README.md`, `docs/delinks.md`, `docs/symbols.md`, `docs/relocs.md`. Undocumented
  commands worth knowing: `fix {thumb-nop, ctor-symbols, ctor-zero, ...}`, `dump {elf-symbols,
  ambig-relocs, elf-function}`, `sig {list, apply, new}`.
- [AetiasHax/ds-rom](https://github.com/AetiasHax/ds-rom): ROM extract / build library
  used by dsd.
- [AetiasHax/unarm](https://github.com/AetiasHax/unarm): ARM disassembler used by
  objdiff for DS projects.
- [AetiasHax/dsd-ghidra](https://github.com/AetiasHax/dsd-ghidra): Ghidra loader and
  `SyncDsd.java` script (`tools/ghidra_sync.py`).
- [encounter/objdiff](https://github.com/encounter/objdiff): local diff (reads
  `objdiff.json`), `objdiff-cli report` / `diff`.
- mwccarm / mwldarm: `http://decomp.aetias.com/files/mwccarm.zip` (downloaded by
  `ninja` into `tools/mwccarm/`).
- [decompals/wibo](https://github.com/decompals/wibo): runs mwccarm on Linux (CI).
- [PrimeDecomp/mwcc-tools](https://github.com/PrimeDecomp/mwcc-tools) (CC0): a
  decompilation of the GameCube CodeWarrior compilers. Read to locate mwccarm's back
  end (`CodeGen.c`, `Coloring.c`, `Scheduler.c`); no code taken.
- [decomp.me](https://decomp.me): compiler tests (`mwcc_30_131` = 2.0/sp1p5).

## Hardware documentation

- Rodrigo Copetti, *Architecture of consoles: Nintendo DS*
  ([article source](https://github.com/flipacholas/Architecture-of-consoles/blob/master/articles/nintendo-ds.Rmd.md)):
  ARM946E-S and ARM7TDMI, ITCM / DTCM, memory map, 2D / 3D engines, sound, cartridge.
  Context only, never evidence for a name or a structure. Used for: autoload and
  memory map bounds (`notes/SPLITS.md`), `-proc arm946e` and ARM/Thumb interworking
  (`notes/COMPILER.md`), recognising I/O register accesses (0x04000000...) in game
  code.
- GBATEK (problemkaputt.de/gbatek.htm): register-level reference for the same
  purposes.

## Emulators (source only)

**GPL: read only, no code taken** into this repository, not even adapted. Not needed for matching (the ROM checksum already proves
the build); used for dynamic analysis: which code runs on which screen, who writes a
field, the size and lifetime of an object. What a trace shows is evidence of behaviour,
not of a name (names still need the proofs of `SPEC.md`). BIOS and firmware dumps they
need stay outside git, like `arm7_bios.bin`.

| Folder | Upstream | License | Commit | Debug features |
|---|---|---|---|---|
| `melonDS` | [melonDS-emu/melonDS](https://github.com/melonDS-emu/melonDS) | GPLv3 | 497aa6cd (2026-11-06) | GDB stub (`src/debug/`, CMake option `ENABLE_GDBSTUB`, on by default); free BIOS replacement in `freebios/` |
| `desmume` | [TASEmulators/desmume](https://github.com/TASEmulators/desmume) | GPLv2 | fc504ee0 (2026-11-06) | GDB stub (`desmume/src/gdbstub/`), Lua scripting (`lua-engine.cpp`), debugger and memory viewer in the Windows front end |

Neither is built for this project yet (the DeSmuME Windows front end is a Visual Studio
project).

## Reference projects

When reusing code, record the commit it was taken from and the license in
`notes/PROVENANCE.md`.

DS projects, split by what may be done with the code (see "Reuse permissions").

### Reusable: code may be taken

Log each reuse in `notes/PROVENANCE.md`.

| Folder | Project | License | dsd | Compiler | Notes |
|---|---|---|---|---|---|
| `twewy` | [Yotona/twewy](https://github.com/Yotona/twewy) | CC0 | 0.12.1 | 2.0/sp1p5 (+1.2/sp4 per file) | template of `tools/configure.py`; libs c, cpp, cri, nitro, runtime |
| `ph` | [zeldaret/ph](https://github.com/zeldaret/ph) | CC0 | 0.11.0 | 2.0/sp1p5 | reference dsd project, docs `build_system.md`, `decompiling.md` |
| `khdays-decomp` | [Yokimitsuro/khdays-decomp](https://github.com/Yokimitsuro/khdays-decomp) | CC0 | own build | 3.0_patch4 | heavily customised; `libs/{nitro,nns,msl}`: sweep fingerprints and per-function sources of the library units (`notes/PROVENANCE.md`) |
| `advance-wars-dual-strike-decomp` | [sturmds/advance-wars-dual-strike-decomp](https://github.com/sturmds/advance-wars-dual-strike-decomp) | CC0 | 0.12.1 | metroskrew 2.0-72 | `tools/dsd_init.py` works around `dsd init` aborting |
| `Rayman-RR-usa-decomp` | [liolu/Rayman-RR-usa-decomp](https://github.com/liolu/Rayman-RR-usa-decomp) | CC0 | own build | 2.0, SDK files 1.2/sp2p3 | clone at dfb4f24 (2026-11-06); `src/nitro`, `src/nnsys`, `src/auto` reusable. **`src/donor/` is not**, nor any other file whose header says it was reused from another project (`from https://... (license: ...)`, also found in `src/nitro`): most come from projects without a license (mkds, DQIV, and `ntrtwl/NitroSDK`, Nintendo's leaked SDK); a file that cites a CC0 project already listed here is taken from that project instead |
| `heroes-of-mana-decomp` | [liolu/heroes-of-mana-decomp](https://github.com/liolu/heroes-of-mana-decomp) | CC0 | own build | 2.0/sp1p5, SDK files 1.2/sp2p3 or sp3 | clone at d29040d (2026-11-06); same layout and same `src/donor/` restriction as Rayman |
| `dqix-decomp` | [DQIX/dqix-decomp](https://github.com/DQIX/dqix-decomp) | Unlicense | 0.10.2 | 2.0/sp2p2, SDK 2.0/sp1p2 | Dragon Quest IX, USA and JPN; C++ game code by subsystem (`src/Graphics`, `src/Filesystem`, ...) with ITCM files (`src/Graphics/itcm`, `src/NitroSDK/itcm`); clone at b48f352; public-domain dedication, reuse allowed like CC0 (owner, 2026-10-26) |

### Hints only: no code taken

Reading them is fine (structure, names to confirm, build patterns); the code is written here from the ROM.

| Folder | Project | License | dsd | Compiler | Notes |
|---|---|---|---|---|---|
| `st` | [zeldaret/st](https://github.com/zeldaret/st) | CC0 | 0.12.0 | dsi/1.2p1 | libs nitro, nns, c, cpp, runtime; **CONTRIBUTING forbids AI use for its library decompilation and mostly-AI PRs**: library code not reused, build patterns only |
| `pokebw2` | [fuddlesworth/pokebw2](https://github.com/fuddlesworth/pokebw2) | GPL-3.0 | fork | dsi/1.1p1 | `docs/configs.md` (ambiguous relocs); `lib/{nitro,nnsys,msl}` |
| `DQIV-DECOMP` | [GoldieLeGenie/DQIV-DECOMP](https://github.com/GoldieLeGenie/DQIV-DECOMP) | none | 0.10.2 | 2.0/sp1p6, libs 2.0/sp1p2 | libs MSL, NitroSDK, NitroSystem, Runtime |
| `fe11-us` | [Eebit/fe11-us](https://github.com/Eebit/fe11-us) | none | 0.12.0 | 2.0/sp2p2 | game code |
| `pokediamond` | [pret/pokediamond](https://github.com/pret/pokediamond) | none | not checked | not checked | `arm9/lib/NitroSDK`, `libnns`, `MSL_C` (b623abef1) |
| `pmd-sky` | [pret/pmd-sky](https://github.com/pret/pmd-sky) | none | not checked | not checked | `lib/NitroSDK` (e3f02199) |
| `pokeheartgold` | [pret/pokeheartgold](https://github.com/pret/pokeheartgold) | none | not checked | not checked | little library source in `lib/` (ee26049b9) |
| `pokeplatinum` | [pret/pokeplatinum](https://github.com/pret/pokeplatinum) | none | not checked | not checked | little library source in `lib/` (c6689696d) |
| `pokeranger2` | [Kuruyia/pokeranger2](https://github.com/Kuruyia/pokeranger2) | none | not checked | not checked | little library source in `lib/` (55b4e0c) |

Dragon Quest projects (same series, other developers and engines): **dqix-decomp**
(Unlicense, reusable) and **DQIV-DECOMP** (no license, hints only) are the closest
references for Square Enix conventions and file formats. Their names are candidates
to confirm in this ROM, never evidence.

Sibling projects of the owner:

- [`dqmj2-decomp`](https://github.com/YggdrasziI/dqmj2-decomp) (Dragon Quest Monsters:
  Joker 2, DS, CC0): the base game of this one and the source of this project's tools,
  documents, library units and names (ported by function matching, `notes/DIFF_DQMJ2.md`).
  Its `notes/MATCHING.md` applies here for code shared by both games.
- `ts2-castaway-decomp` (The Sims 2: Castaway, DS): template of dqmj2-decomp.
- `DQMJ-decomp` (DQM Joker, Europe): ROM only so far.
- `mlab-decomp`, `spyro-dotd-decomp` (Wii, methodology).

## Reuse permissions

- **Reusable**: CC0 (twewy, ph, khdays-decomp, advance-wars-dual-strike-decomp) and Unlicense (dqix-decomp): allowed by the owner (2026-10-26 and 2026-11-01), logged in `notes/PROVENANCE.md`.
- **Hints only**: no license (DQIV-DECOMP, fe11-us, pokediamond, pmd-sky, pokeheartgold, pokeplatinum, pokeranger2) or GPL (pokebw2): no code copied (owner, 2026-11-01).
- zeldaret/st is CC0, but its CONTRIBUTING forbids AI use for its library decompilation: kept in hints only (owner, 2026-11-01).
- A clone that gains a license later moves table after checking it again.
