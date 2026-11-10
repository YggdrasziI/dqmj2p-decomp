#!/usr/bin/env python3
"""Print the evidence used for the region map of a module (notes/SPLITS.md).

For one module (main, itcm or ovNNN) of a game version:

  blocks   runs of ARM or Thumb functions from symbols.txt (a run ends at a mode
           change or at a gap of 0x40 bytes or more), with their function count;
  strings  the printable strings that the code of an address range loads (`load`
           relocations into the module or into main), one line each;
  sweep    with --sweep, the unique hits of tools/compiler_sweep.py --json files,
           shown as the runs of consecutive hits from the same JSON file (the
           file name is the label, e.g. nns_g3d.json -> nns_g3d).

Usage:
  python tools/module_map.py main
  python tools/module_map.py ov004 --strings 0x021E116C 0x021FA028
  python tools/module_map.py main --sweep sweep/*.json

A string, a mode or a sweep hit is evidence for what a range holds, not for its
exact bounds: the bounds of a unit are only proven when the unit matches.
"""

import argparse
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FUNC = re.compile(r"(\S+) kind:function\((arm|thumb),size=(0x[0-9a-f]+)\S*\) addr:(0x[0-9a-f]+)")
RELOC = re.compile(r"from:(0x[0-9a-f]+) kind:(\S+) to:(0x[0-9a-f]+) module:(\S+)")
MAIN_BASE = 0x02000000


def overlay_base(ext, module):
    """Load address of an overlay, from the extract's overlays.yaml."""
    wanted, current = int(module[2:]), None
    with open(os.path.join(ext, "arm9_overlays", "overlays.yaml"), encoding="utf-8") as f:
        for line in f:
            key, _, value = line.strip().lstrip("- ").partition(":")
            if key == "id":
                current = int(value)
            elif key == "base_address" and current == wanted:
                return int(value)
    sys.exit(f"{module}: no base_address in overlays.yaml")


def paths(version, module):
    """(config folder, binary, load address) of a module."""
    cfg = os.path.join(ROOT, "config", version, "arm9")
    ext = os.path.join(ROOT, "extract", version)
    if module == "main":
        return cfg, os.path.join(ext, "arm9", "arm9.bin"), MAIN_BASE
    if module == "itcm":
        return os.path.join(cfg, "itcm"), os.path.join(ext, "arm9", "itcm.bin"), 0x01FF8000
    if re.fullmatch(r"ov\d{3}", module):
        return (os.path.join(cfg, "overlays", module),
                os.path.join(ext, "arm9_overlays", module + ".bin"), overlay_base(ext, module))
    sys.exit(f"unknown module {module!r} (main, itcm or ovNNN)")


def functions(cfg):
    out = []
    with open(os.path.join(cfg, "symbols.txt"), encoding="utf-8") as f:
        for line in f:
            m = FUNC.match(line)
            if m:
                out.append((int(m[4], 16), int(m[3], 16), m[1], m[2]))
    return sorted(out)


def blocks(funcs):
    runs = []
    for addr, size, _, mode in funcs:
        if runs and runs[-1][2] == mode and addr - runs[-1][1] < 0x40:
            runs[-1][1] = addr + size
            runs[-1][3] += 1
        else:
            runs.append([addr, addr + size, mode, 1])
    return runs


def c_string(data, offset):
    if not 0 <= offset < len(data):
        return None
    end = data.find(b"\0", offset)
    s = data[offset:end if end >= 0 else len(data)]
    if 4 <= len(s) < 120 and all(32 <= c < 127 or c in (9, 10, 13) for c in s):
        return s.decode("ascii")
    return None


def strings(version, module, lo, hi):
    cfg, binary, base = paths(version, module)
    with open(binary, "rb") as f:
        own = f.read()
    with open(paths(version, "main")[1], "rb") as f:
        main = f.read()
    out = []
    with open(os.path.join(cfg, "relocs.txt"), encoding="utf-8") as f:
        for line in f:
            m = RELOC.match(line)
            if not m or m[2] != "load":
                continue
            src, dst, target = int(m[1], 16), int(m[3], 16), m[4]
            if not lo <= src < hi:
                continue
            if target == "main":
                s = c_string(main, dst - MAIN_BASE)
                where = "main"
            elif target == module or (module.startswith("ov") and target == f"overlay({int(module[2:])})"):
                s = c_string(own, dst - base)
                where = module
            else:
                continue
            if s is not None:
                out.append((src, dst, where, s))
    return out


def sweep_runs(files, module):
    """Runs of consecutive unique hits with the same label, in this module."""
    import json
    hits = {}
    for path in files:
        label = os.path.splitext(os.path.basename(path))[0]
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for funcs in data.values():
            for name, per_version in funcs.items():
                if name.startswith("<"):  # <compile>, <size>
                    continue
                addrs = {a for h in per_version.values() if h for a in h}
                if len(addrs) != 1:
                    continue
                a = addrs.pop()
                if isinstance(a, str):  # "ovNNN:0x..." for an overlay
                    mod, _, a = a.partition(":")
                    a = int(a, 16)
                else:
                    mod = "itcm" if a < MAIN_BASE else "main"
                if mod == module:
                    hits[a] = (label, name)
    runs = []
    for a in sorted(hits):
        label, name = hits[a]
        if runs and runs[-1][2] == label:
            runs[-1][1] = a
            runs[-1][3] += 1
        else:
            runs.append([a, a, label, 1, name])
    return runs


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("module", help="main, itcm or ovNNN")
    ap.add_argument("--version", default="jpn")
    ap.add_argument("--strings", nargs=2, metavar=("START", "END"),
                    help="strings loaded by the code of this range")
    ap.add_argument("--sweep", nargs="+", metavar="JSON", help="compiler_sweep.py --json files")
    args = ap.parse_args()
    cfg = paths(args.version, args.module)[0]

    if args.strings:
        lo, hi = (int(x, 16) for x in args.strings)
        rows = strings(args.version, args.module, lo, hi)
        for src, dst, where, s in rows:
            print(f"{src:08X} -> {where} {dst:08X} {s!r}")
        print(f"{len(rows)} strings")
        return
    if args.sweep:
        files = [p for pattern in args.sweep for p in glob.glob(pattern)]
        for start, last, label, n, first in sweep_runs(files, args.module):
            print(f"{start:08X}-{last:08X} {label:<16} {n:4} hits (first {first})")
        return
    for start, end, mode, n in blocks(functions(cfg)):
        print(f"{mode:<5} {start:08X}-{end:08X} {n:5} functions")


if __name__ == "__main__":
    main()
