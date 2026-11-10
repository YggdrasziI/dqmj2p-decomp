# Provenance of reused code

Every file or function taken from another project, with the project, its commit
(`git -C <clone> rev-parse HEAD`), its license (or its absence and the owner's
approval, see `notes/REFERENCES.md`) and the changes made.

## Build system and scripts (`tools/`)

Source: [dqmj2-decomp](https://github.com/YggdrasziI/dqmj2-decomp) (sibling project,
same owner, CC0-1.0), commit `80b83b28a403213e23764af195af92a52b4bd7a9`, copied on
2026-11-10. Its build files come from ts2-castaway-decomp and
[Yotona/twewy](https://github.com/Yotona/twewy) (CC0-1.0); see its own
`notes/PROVENANCE.md`.

| Our file | Changes |
|---|---|
| `tools/configure.py` | `GAME = "dqmj2p"`; only version `jpn` (B2JJ); `TWEWY_HEADER_SOURCES` empty |
| `tools/progress_badges.py` | title; `REGIONS` empty and overlay labels reduced to ov004 until the region map exists |
| `tools/unit_candidates.py` | `GAME_REGIONS`: ov000-ov007, whole `.text`, ov004 (DS Protect) left out |
| `tools/ghidra_sync.py` | `DQMJ2P_GHIDRA_PROJECT`, project name `dqmj2p_jpn` |
| `tools/asm_compare.py`, `tools/permute.py` | usage examples without DQMJ2 addresses |
| every other file of `tools/` | none |

Downloaded binaries (`dsd.exe` 0.12.1, `objdiff-cli.exe` 3.8.1, `tools/mwccarm/`) were
copied from dqmj2-decomp's local copies, same versions as `configure.py` downloads.

## Project documents

`SPEC.md`, `README.md`, `notes/`, `.vscode/tasks.json`, `.github/workflows/build.yml`
(image tag `pro`, wibo from the image), `.gitignore`, `.gitattributes` and `LICENSE`
(CC0-1.0) follow dqmj2-decomp (same commit), rewritten for this ROM.
