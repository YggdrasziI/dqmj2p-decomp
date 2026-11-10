# Dragon Quest Monsters: Joker 2 Professional (DS) decompilation — specification

## Goal

Rebuild Dragon Quest Monsters: Joker 2 Professional (Nintendo DS, Japan) from C/C++
source written from scratch so that, compiled with the original compiler and flags,
it produces ARM9 modules (main, ITCM, DTCM, overlays 0-7) identical byte for byte to
the original, and so the original ROM.

Professional is an expanded re-release of Dragon Quest Monsters: Joker 2. Most of its
code is expected to be shared with that game, decompiled in the sibling project
[dqmj2-decomp](https://github.com/YggdrasziI/dqmj2-decomp): names, library units and
game units proven there are ported here by function matching, and the differences are
mapped in `notes/DIFF_DQMJ2.md`.

Out of scope: a port, game assets, any redistribution of game files, the ARM7 binary
(standard SDK component, rebuilt from the extracted binary as is).

## Target ROM

| Item | Value | Evidence |
|---|---|---|
| Game code | `B2JJ` (Japan), maker `GD` (Square Enix) | ROM header 0x0C, 0x10 (`extract/jpn/header.yaml`) |
| Internal title | `DQM-JOKER2-P` | ROM header 0x00 |
| Revision | 0 | ROM header 0x1E |
| ROM SHA-1 | `602d6271c9993072cdcfb2d60958cb7b5f534b2a` (256 MiB) | `config/jpn/build.sha1` |
| ARM9 main | load 0x02000000, entry 0x02000800, 0x964B4 bytes in the ROM, compressed | ROM header 0x20-0x2C, `extract/jpn/arm9/arm9.yaml` |
| ARM9 sections | `.text` 0x02000000-0x020D3FE0, `.rodata` -0x020DCB30, `.ctor` empty, `.data` 0x020DCB40-0x020E3300, `.bss` 0x020E3300-0x021D7240 | `config/jpn/arm9/delinks.txt` (dsd analysis) |
| ITCM | 0x01FF8000, `.text` -0x01FFF894, `.bss` 0x01FFF8A0-0x01FFFBC0 | `config/jpn/arm9/itcm/delinks.txt` |
| DTCM | 0x027E0000, 0x60 bytes data + 0x20 bss | `config/jpn/arm9/dtcm/delinks.txt` |
| ARM9 overlays | 8, all loaded at 0x021D7240 (one at a time), compressed; overlay 4 is protected by "DS Protect" 1.28 (dsd decrypts it on extract, `enable_dsprot: true`) | `extract/jpn/arm9_overlays/overlays.yaml`, `config/jpn/arm9/config.yaml` |
| ARM7 | 0x27130 bytes, no overlays (same size as DQMJ2's) | ROM header 0x3C, 0x5C |
| NitroSDK | version word `0x04027539` (major 4, minor 2, relstep 30009), same as DQMJ2 | `extract/jpn/arm9/arm9.yaml`, `sdk_version` |
| Other SDK markers | `[SDK+NINTENDO:BACKUP]` 0x02000B8C, `[SDK+UBIQUITOUS:CPS]` 0x02000BA4, `[SDK+NINTENDO:WiFi2.1.30003.0709200229]` 0x02000BBC, `[SDK+UBIQUITOUS:SSL]` 0x02000BE4, `[SDK+NINTENDO:DWC3.1.30006.20090131.2222_DWC_3_1_PLUS6]` 0x02000BFC (same strings and addresses as DQMJ2) | strings in `extract/jpn/arm9/arm9.bin` |
| Code size | 1927254 bytes, 13616 functions over all modules (dsd analysis) | `build/jpn/report.json` |
| Build date | unknown | — |

Overlays (dsd analysis, `config/jpn/arm9/overlays/ov<NNN>/delinks.txt`):

| Overlay | `.text` end | `.bss` end | Functions |
|---|---|---|---|
| ov000 | 0x0222B7E0 | 0x02247860 | 1948 |
| ov001 | 0x0222D9A8 | 0x02249AA0 | 2246 |
| ov002 | 0x021DA46C | 0x021DB400 | 76 |
| ov003 | 0x021E0104 | 0x021E0F40 | 199 |
| ov004 | 0x021D8E9C | 0x021D8F00 | 55 (DS Protect) |
| ov005 | 0x021E6E24 | 0x021F02E0 | 317 |
| ov006 | 0x02207914 | 0x0221C760 | 2064 |
| ov007 | 0x021DA06C | 0x021DB2E0 | 100 |

Main module: 6464 functions; ITCM: 146 (counts of `kind:function` in `symbols.txt`).
Which overlay of this ROM corresponds to which DQMJ2 overlay is not known yet
(`notes/DIFF_DQMJ2.md`).

Game files: 1747 files under `extract/jpn/files`. Their roles are not analysed.

## Versions

Professional was released in Japan only: the project has one version, `jpn`.

| Name | Release | Game code / maker | ROM SHA-1 | Status |
|---|---|---|---|---|
| `jpn` | Dragon Quest Monsters: Joker 2 Professional (Japan) | `B2JJ` / `GD` | `602d6271…` | enabled, only version |

The version model of dqmj2-decomp is kept (`VERSIONS`, `DISABLED_VERSIONS` and
`REGION_<NAME>` in `tools/configure.py`; `--version` in the scripts, default `jpn`), so
that sources shared with DQMJ2 keep the same form.

## Toolchain

Evidence and the candidate analysis go in `notes/COMPILER.md`.

- ds-decomp (`dsd`) **v0.12.1**: extract, analysis (`dsd init`), delink, linker script,
  ROM rebuild (overlay compression, DS Protect re-encryption), checks, objdiff config.
- objdiff-cli **v3.8.1** (report, diff with the unarm disassembler), objdiff GUI.
- mwccarm / mwldarm from `decomp.aetias.com/files/mwccarm.zip` (in `tools/mwccarm/`).
  `configure.py` uses 2.0/sp2p2 with the flags of dqmj2-decomp; they are not proven for
  this ROM yet (`notes/COMPILER.md`).
- Linker flags: `-proc arm946e -interworking -dead -nostdlib -m Entry`, as in
  dqmj2-decomp.
- Optional: Ghidra 11.2.1 with the dsd-ghidra v0.7.0 extension, outside the repository
  (`tools/ghidra_sync.py` pushes the dsd symbols to a Ghidra project).

## Acceptance criteria

- Every build ends with `build\jpn\dqmj2p_jpn.nds: OK` (`ninja`), after `dsd check
  modules` (main, ITCM, DTCM, overlays 0-7 OK) and `dsd check symbols`.
- The ROM check uses `config/jpn/build.sha1` (the original ROM) when `arm7_bios.bin`
  is in the project root, and `config/jpn/build_nobios.sha1` otherwise. Without the
  BIOS, dsd cannot encrypt the secure area, so exactly four bytes differ from the
  original: the secure area CRC (0x6C-0x6D) and the header CRC (0x15E-0x15F); every
  other byte is identical (checked 2026-11-10 with `cmp`). With the owner's ARM7 BIOS
  dump the rebuilt ROM is identical to the original (checked 2026-11-10). BIOS dumps
  are never versioned or pushed anywhere.
- A file in `delinks.txt` is `complete` only when objdiff reports 100 % for all its
  functions and data and the build still matches.
- A unit ported from dqmj2-decomp is `complete` here only after it builds and matches
  against this ROM; matching in DQMJ2 is not evidence for this ROM.
- Every name, compiler fact or structure layout rests on evidence. A name proven in
  dqmj2-decomp is evidence here only for a function whose bytes match (relocations
  masked) and whose context agrees (`tools/port_symbols.py`, `notes/DIFF_DQMJ2.md`).
  Names given in Ghidra are not evidence by themselves.
- Reused code from another project is logged with its commit and license in
  `notes/PROVENANCE.md`.

## Naming conventions

As in dqmj2-decomp:

- dsd default names stay until proven: `func_<addr>`, `data_<addr>`; in overlays,
  `func_ov<NNN>_<addr>`, `data_ov<NNN>_<addr>`.
- Game units: `src/main/unit_<start address>.cpp`, `src/ov<NNN>/unit_<start
  address>.cpp`, with addresses of **this** ROM (a ported unit is renamed after its
  address here).
- Unknown classes and structs: `Unk<Name>` or `Unk<address>`.
- SDK and library code under `libs/<lib>/{include,src}`, units
  `libs/<lib>/src/<folder>/unit_<start address>.c`.
- Code, comments, notes and commit messages are in English.

## Layout

```
README.md SPEC.md LICENSE
config/jpn/               build.sha1, build_nobios.sha1, arm9/ (dsd configs:
                          config.yaml, delinks.txt, symbols.txt, relocs.txt;
                          itcm/, dtcm/, overlays/ov000 ... ov007/)
extract/                  baserom_dqmj2p_jpn.nds and dsd extraction (git-ignored)
build/                    build output (git-ignored)
src/  include/  libs/     sources and headers (game, SDK, MSL), when added
tools/                    configure.py and scripts
progress/jpn/             progress badges and history
notes/                    reference documentation
.github/workflows/        CI: delink, link, rebuild and check the ROM in a private image
```

## Build

```
pip install -r tools/requirements.txt
python tools/configure.py # generate build.ninja (first run: then `ninja` downloads the tools)
ninja                     # delink, compile, link, check modules and symbols, rebuild
                          # the ROM, check its SHA-1, write build/jpn/report.json
```

Put the ROM at `extract/baserom_dqmj2p_jpn.nds`. Useful targets: `ninja objdiff`,
`ninja report`, `ninja dis`, `ninja apply`. On Windows use native tools, not WSL.
