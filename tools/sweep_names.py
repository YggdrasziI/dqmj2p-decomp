#!/usr/bin/env python3
"""Name library functions and propose library units from compiler_sweep.py results.

Reads the --json files of tools/compiler_sweep.py (reference library sources compiled
with this project's flags and searched in the ROM) and keeps the hits that are whole
library functions of this ROM:

  - found at exactly one address;
  - at the start of a dsd function of the same size (the whole function, not a
    prefix of a longer one);
  - more than 8 bytes;
  - inside a library region of notes/SPLITS.md (LIBRARY_REGIONS), so that a small
    game function that happens to compile the same is not named.

Name policy (SPEC.md): khdays-decomp's name first (it follows the NitroSDK /
NitroSystem API), twewy's only where khdays has no hit. Names that only repeat an
address of the other game (`func_02017bec`), carry its overlay (`Ov105_...`) or a
suffix that makes a repeated name unique (`ForceStopStrm_2`) are not names and are
skipped, as are names that two functions would get.

Units: a run of consecutive dsd functions that are all kept hits from the same
reference folder becomes one proposed unit `libs/<lib>/src/<folder>/unit_<start>.c`
(.text only). Its bounds are those of the run: a hypothesis, the real file may hold
the unnamed functions around it, or the run may join two files.

Usage:
  python tools/sweep_names.py build/jpn/sweep/*.json            # report only
  python tools/sweep_names.py build/jpn/sweep/*.json --apply    # write symbols.txt
                                                                # and delinks.txt
"""

import argparse
import glob
import json
import os
import re
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FUNC = re.compile(r"^(\S+) kind:function\((arm|thumb),size=(0x[0-9a-f]+)\S*\) addr:(0x[0-9a-f]+)")
# Library regions of notes/SPLITS.md: (module, start, end)
LIBRARY_REGIONS = [
    ("main", 0x02000800, 0x0203C130),  # crt0, NitroSDK, MSL, NitroSystem
    ("itcm", 0x01FF8000, 0x01FF8580),  # NitroSDK
]
REFERENCES = ("khdays", "twewy")  # name priority
# Not names: an address of the other game, its overlay prefix, a "_2" made unique
NOT_A_NAME = re.compile(r"^(func|data|sub|unk|FUN)_|_[0-9a-fA-F]{7,8}$|^Ov\d+_|_\d+$")
MIN_SIZE = 9


def config_dir(module, version):
    cfg = os.path.join(ROOT, "config", version, "arm9")
    return cfg if module == "main" else os.path.join(cfg, module)


def read_functions(module, version):
    """{address: [name, size, line index]} and the file lines."""
    path = os.path.join(config_dir(module, version), "symbols.txt")
    with open(path, encoding="utf-8", newline="") as f:
        lines = f.read().splitlines(keepends=True)
    funcs = {}
    for i, line in enumerate(lines):
        m = FUNC.match(line)
        if m:
            funcs[int(m[4], 16)] = [m[1], int(m[3], 16), i]
    return funcs, lines


def origin(src):
    """(reference, library, folder) of a reference source path."""
    parts = src.replace("\\", "/").split("/")
    ref = next((r for r in REFERENCES if any(r in p for p in parts)), None)
    i = parts.index("libs")
    lib = parts[i + 1]
    if ref == "twewy":  # twewy: libs/nitro/src/<folder>/<file>.c
        folder = parts[i + 3]
    else:  # khdays: libs/<lib>/<folder>/...
        folder = parts[i + 2]
        if lib == "nitro" and folder == "nns":  # NitroSystem code kept in khdays' nitro
            lib, folder = "nns", "misc"
    return ref, lib, folder


def module_of(addr):
    for module, lo, hi in LIBRARY_REGIONS:
        if lo <= addr < hi:
            return module
    return None


def collect(files):
    """[(address, reference, name, library, folder, size)] of the unique hits."""
    raw = []
    for path in files:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for src, funcs in data.items():
            ref, lib, folder = origin(src)
            size_of = funcs.get("<size>", {})
            for name, per_version in funcs.items():
                if name.startswith("<"):
                    continue
                addrs = {a for h in per_version.values() if h for a in h}
                if len(addrs) != 1:
                    continue
                addr = addrs.pop()
                if isinstance(addr, str):
                    continue  # overlay hit: no library region in overlays
                size = max((size_of.get(name) or {}).values() or [0])
                raw.append((addr, ref, name, lib, folder, size))
    return raw


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("json", nargs="+")
    ap.add_argument("--version", default="jpn")
    ap.add_argument("--apply", action="store_true", help="write symbols.txt and delinks.txt")
    args = ap.parse_args()
    files = [p for pattern in args.json for p in glob.glob(pattern)]

    modules = {m: read_functions(m, args.version) for m, _, _ in LIBRARY_REGIONS}
    kept = defaultdict(dict)  # (module, addr) -> ref -> (name, lib, folder)
    skipped = defaultdict(int)
    for addr, ref, name, lib, folder, size in collect(files):
        module = module_of(addr)
        if module is None:
            skipped["outside the library regions"] += 1
            continue
        func = modules[module][0].get(addr)
        if func is None:
            skipped["not at a dsd function start"] += 1
            continue
        if size != func[1]:
            skipped["size differs from the dsd function"] += 1
            continue
        if size < MIN_SIZE:
            skipped["8 bytes or less"] += 1
            continue
        kept[(module, addr)].setdefault(ref, (name, lib, folder))

    # Name of each function: first reference in priority order with a real name
    chosen = {}
    for key, per_ref in kept.items():
        for ref in REFERENCES:
            if ref in per_ref and not NOT_A_NAME.search(per_ref[ref][0]):
                chosen[key] = (ref,) + per_ref[ref]
                break
        else:
            skipped["no real name in the references"] += 1
    by_name = defaultdict(list)
    for key, (ref, name, lib, folder) in chosen.items():
        by_name[name].append(key)
    for name, keys in by_name.items():
        if len(keys) > 1:
            print(f"name {name} for several functions: "
                  + ", ".join(f"{m}:0x{a:08X}" for m, a in keys) + " (skipped)")
            for key in keys:
                del chosen[key]
    # A name already used by another symbol of the module
    for (module, addr), (ref, name, lib, folder) in list(chosen.items()):
        funcs, lines = modules[module]
        other = [a for a, f in funcs.items() if f[0] == name and a != addr]
        if other:
            print(f"name {name} already used at 0x{other[0]:08X} (skipped)")
            del chosen[(module, addr)]

    renames = []
    for (m, a), v in sorted(chosen.items()):
        old = modules[m][0][a][0]
        if old == v[1]:
            continue
        if not old.startswith("func_"):  # a name set before (dsd or by hand) is kept
            print(f"keeps {old} at 0x{a:08X} (the reference names it {v[1]})")
            continue
        renames.append((m, a, old, v[1]))
    print(f"kept hits: {len(kept)}, named: {len(chosen)}, renames: {len(renames)}")
    for why, n in sorted(skipped.items()):
        print(f"  skipped ({why}): {n}")

    # Units: runs of consecutive dsd functions from the same reference folder
    units = []
    for module, lo, hi in LIBRARY_REGIONS:
        funcs = modules[module][0]
        run = None
        for addr in sorted(a for a in funcs if lo <= a < hi):
            v = kept.get((module, addr))
            label = None
            if v:
                ref = next(r for r in REFERENCES if r in v)
                label = (v[ref][1], v[ref][2])
            if run and label == run["label"] and addr == run["end"]:
                run["end"] = addr + funcs[addr][1]
                run["count"] += 1
                continue
            if run:
                units.append(run)
            run = {"module": module, "label": label, "start": addr,
                   "end": addr + funcs[addr][1], "count": 1} if label else None
        if run:
            units.append(run)
    # A Thumb function can end on 2 bytes: the padding up to the next word belongs to
    # the file (the next section starts aligned), unless a function starts there.
    for u in units:
        if u["end"] % 4 and not any(u["end"] <= a < u["end"] + (-u["end"] % 4)
                                    for a in modules[u["module"]][0]):
            u["end"] += -u["end"] % 4
    print(f"proposed units: {len(units)} ({sum(u['count'] for u in units)} functions)")
    for u in units:
        lib, folder = u["label"]
        u["path"] = f"libs/{lib}/src/{folder}/unit_{u['start']:08X}.c"
        print(f"  {u['module']:<5} {u['start']:08X}-{u['end']:08X} {u['count']:4} {u['path']}")

    # Our sources that use an old name
    sources = [p for d in ("src", "include", "libs") for p in glob.glob(os.path.join(ROOT, d, "**", "*.*"), recursive=True)]
    text = {p: open(p, encoding="utf-8", errors="replace").read() for p in sources if os.path.isfile(p)}
    for module, addr, old, new in renames:
        for p, t in text.items():
            if re.search(r"\b" + re.escape(old) + r"\b", t):
                print(f"  {os.path.relpath(p, ROOT)} uses {old} (renamed {new})")

    if not args.apply:
        return
    for module, addr, old, new in renames:
        funcs, lines = modules[module]
        i = funcs[addr][2]
        assert lines[i].startswith(old + " ")
        lines[i] = new + lines[i][len(old):]
    for module in modules:
        path = os.path.join(config_dir(module, args.version), "symbols.txt")
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write("".join(modules[module][1]))
    for module in modules:
        mine = [u for u in units if u["module"] == module]
        if mine:
            add_units(os.path.join(config_dir(module, args.version), "delinks.txt"), mine)
    print("written")


def add_units(path, units):
    """Add the units to delinks.txt, all file blocks in address order; an
    existing block on the same range is kept as it is."""
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    nl = "\r\n" if "\r\n" in text else "\n"
    head, *blocks = text.replace("\r\n", "\n").rstrip("\n").split("\n\n")
    start_of = lambda b: int(re.search(r"start:(0x[0-9a-f]+)", b)[1], 16)
    taken = [(start_of(b), int(re.search(r"end:(0x[0-9a-f]+)", b)[1], 16)) for b in blocks]
    for u in units:
        if any(s < u["end"] and u["start"] < e for s, e in taken):
            print(f"  {u['path']}: overlaps an existing file (not added)")
            continue
        blocks.append(f"{u['path']}:\n    .text       start:0x{u['start']:08x} end:0x{u['end']:08x}")
    blocks.sort(key=start_of)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(nl.join("\n\n".join([head] + blocks).split("\n")) + nl)


if __name__ == "__main__":
    sys.exit(main())
