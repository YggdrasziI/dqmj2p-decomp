#!/usr/bin/env python3
"""Compile C/C++ files with several mwccarm versions and look for their functions in
this ROM's code.

Every function of each compiled object is searched in the extracted ARM9 main, ITCM,
DTCM and overlay modules (overlay hits are written `ovNNN:0x...`, since overlays share
one address range), with the words touched by a relocation masked (calls, pointers),
so a hit means the same instructions were generated: it gives the function's address
(a library signature) and which compilers produce it. A function found at one address
only is reported with that address; several addresses mean the bytes are not
distinctive (often tiny functions).

Usage:
  python tools/compiler_sweep.py <file.c> [<file.c> ...]
      [--compilers 2.0/sp1p5,2.0/sp2p3 | all]   (default: all of tools/mwccarm)
      [--include <dir>] ...                      (extra include dirs)
      [--ref <reference project root>]           (adds its include/ and libs/*/include)
      [--flags "<mwccarm flags>"]                (default: tools/configure.py's)
      [--min-size 12]                            (smaller functions are not reported)
      [--json out.json]                          (machine-readable results: source ->
                                                  function -> compiler -> addresses;
                                                  "<size>" -> function -> compiler -> bytes)

Example (NitroSDK OS sources of twewy, every compiler):
  python tools/compiler_sweep.py --ref path/to/twewy \\
      path/to/twewy/libs/nitro/src/os/*.c
"""

import argparse
import ast
import glob
import json
import os
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MWCC_ROOT = os.path.join(ROOT, "tools", "mwccarm")
EXTRACT = os.path.join(ROOT, "extract", "jpn", "arm9")
OVERLAYS = os.path.join(ROOT, "extract", "jpn", "arm9_overlays")

# Options that a compiler family rejects ("Unknown option"): dropped for that family.
UNSUPPORTED = {"1.2": ["-ipa file"]}

# ELF relocation types that patch a code or data word (ARM) or a BL pair (Thumb)
R_ARM_PC24, R_ARM_ABS32, R_ARM_REL32, R_ARM_THM_CALL = 1, 2, 3, 10
R_ARM_XPC25, R_ARM_THM_XPC22 = 15, 16
R_ARM_CALL, R_ARM_JUMP24, R_ARM_THM_JUMP24 = 28, 29, 30
MASKED_RELOCS = {R_ARM_PC24, R_ARM_ABS32, R_ARM_REL32, R_ARM_THM_CALL, R_ARM_XPC25,
                 R_ARM_THM_XPC22, R_ARM_CALL, R_ARM_JUMP24, R_ARM_THM_JUMP24}


def project_flags():
    """DEFAULT_CC_FLAGS of tools/configure.py, read from its source (importing it
    would parse the command line): COMMON_CC_FLAGS plus the literal extras."""
    tree = ast.parse(open(os.path.join(ROOT, "tools", "configure.py"), encoding="utf-8").read())
    values = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            values[node.targets[0].id] = node.value
    flags = [ast.literal_eval(e) for e in values["COMMON_CC_FLAGS"].elts]
    # DEFAULT_CC_FLAGS = " ".join((*COMMON_CC_FLAGS, "-ipa file", ...))
    for e in values["DEFAULT_CC_FLAGS"].args[0].elts:
        if isinstance(e, ast.Constant):
            flags.append(e.value)
    return " ".join(flags)


def flags_for(flags, version):
    for opt in UNSUPPORTED.get(version.split("/")[0], []):
        flags = flags.replace(opt, " ")
    return flags.split()


def modules():
    """[(name, base address, bytes)] of the extracted ARM9 modules; name is None for
    main, ITCM and DTCM (plain addresses) and "ovNNN" for an overlay."""
    out = []
    for name, yaml_name in (("main", "arm9.yaml"), ("itcm", "itcm.yaml"), ("dtcm", "dtcm.yaml")):
        bin_path = os.path.join(EXTRACT, yaml_name.replace(".yaml", ".bin"))
        with open(os.path.join(EXTRACT, yaml_name), encoding="utf-8") as f:
            base = next(int(line.split(":")[1]) for line in f if line.startswith("base_address"))
        with open(bin_path, "rb") as f:
            out.append((None, base, f.read()))
    # overlays.yaml: a list of "- id: N" entries with base_address and file_name
    overlay = None
    for line in open(os.path.join(OVERLAYS, "overlays.yaml"), encoding="utf-8"):
        key, _, value = line.strip().lstrip("- ").partition(": ")
        if key == "id":
            overlay = {"id": int(value)}
        elif key in ("base_address", "file_name") and overlay is not None:
            overlay[key] = value
            if "base_address" in overlay and "file_name" in overlay:
                with open(os.path.join(OVERLAYS, overlay["file_name"]), "rb") as f:
                    out.append((f"ov{overlay['id']:03d}", int(overlay["base_address"]), f.read()))
                overlay = None
    return out


def fmt(hit):
    return hit if isinstance(hit, str) else f"0x{hit:08X}"


def read_elf(path):
    """{name: (bytes, mask, alignment)} for each function of an ELF32 relocatable
    object; alignment is 2 for Thumb functions (odd symbol value), 4 for ARM."""
    data = open(path, "rb").read()
    shoff, = struct.unpack_from("<I", data, 0x20)
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", data, 0x2E)
    sections = []
    for i in range(shnum):
        sections.append(struct.unpack_from("<IIIIIIIIII", data, shoff + i * shentsize))

    def name_at(strtab, off):
        start = sections[strtab][4] + off
        return data[start:data.index(b"\0", start)].decode("latin-1")

    def sec_name(i):
        return name_at(shstrndx, sections[i][0])

    contents = {i: bytearray(data[s[4]:s[4] + s[5]]) for i, s in enumerate(sections) if s[1] == 1}
    masks = {i: bytearray(b"\xff" * len(c)) for i, c in contents.items()}
    symbols = []
    for s in sections:
        if s[1] == 2:  # SHT_SYMTAB
            for off in range(s[4], s[4] + s[5], 16):
                st_name, value, size, info, _, shndx = struct.unpack_from("<IIIBBH", data, off)
                if info & 0xF == 2 and shndx in contents:  # STT_FUNC
                    symbols.append((name_at(s[6], st_name), shndx, value, size))
    for s in sections:
        # SHT_REL (8-byte entries) or SHT_RELA (12 bytes, what mwccarm writes),
        # applying to section sh_info
        if s[1] in (9, 4) and s[7] in masks:
            mask = masks[s[7]]
            for off in range(s[4], s[4] + s[5], 8 if s[1] == 9 else 12):
                r_off, r_info = struct.unpack_from("<II", data, off)
                if r_info & 0xFF in MASKED_RELOCS:
                    mask[r_off:r_off + 4] = b"\0" * 4
    funcs = {}
    for name, shndx, value, size in symbols:
        start = value & ~1
        if size == 0 or not sec_name(shndx).startswith(".text"):
            continue
        code = bytes(contents[shndx][start:start + size])
        mask = bytes(masks[shndx][start:start + size])
        funcs[name] = (code, mask, 2 if value & 1 else 4)
    return funcs


def find(code, mask, align, mods):
    """Addresses where code matches, ignoring the bytes whose mask is 0."""
    # Anchor on the longest run of unmasked bytes, then check the whole function.
    best, run_start = (0, 0), None
    for i, m in enumerate(mask + b"\0"):
        if m and run_start is None:
            run_start = i
        elif not m and run_start is not None:
            if i - run_start > best[1] - best[0]:
                best = (run_start, i)
            run_start = None
    a, b = best
    if b - a < 4:
        return []
    anchor = code[a:b]
    hits = []
    for name, base, blob in mods:
        pos = blob.find(anchor)
        while pos != -1:
            start = pos - a
            if start >= 0 and start + len(code) <= len(blob) and start % align == 0:
                window = blob[start:start + len(code)]
                if all(m == 0 or x == y for x, y, m in zip(window, code, mask)):
                    hits.append(base + start if name is None else f"{name}:0x{base + start:08X}")
            pos = blob.find(anchor, pos + 1)
    return sorted(set(hits), key=fmt)


def compilers(arg):
    if arg != "all":
        return arg.split(",")
    found = []
    for family in sorted(os.listdir(MWCC_ROOT)):
        for version in sorted(os.listdir(os.path.join(MWCC_ROOT, family))):
            if os.path.isfile(os.path.join(MWCC_ROOT, family, version, "mwccarm.exe")):
                found.append(f"{family}/{version}")
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sources", nargs="+")
    parser.add_argument("--compilers", default="all")
    parser.add_argument("--include", action="append", default=[])
    parser.add_argument("--ref", help="reference project root")
    parser.add_argument("--flags", help="mwccarm flags (default: tools/configure.py's)")
    parser.add_argument("--define", action="append", default=[], help="extra -d defines")
    parser.add_argument("--min-size", type=int, default=12)
    parser.add_argument("--json")
    args = parser.parse_args()
    if args.flags is None:
        args.flags = project_flags()

    includes = list(args.include)
    if args.ref:
        includes.append(os.path.join(args.ref, "include"))
        includes += glob.glob(os.path.join(args.ref, "libs", "*", "include"))
    sources = [p for s in args.sources for p in (glob.glob(s) or [s])]
    mods = modules()
    versions = compilers(args.compilers)
    results = {}  # source -> function -> compiler -> [addresses] or None (compile error)
    with tempfile.TemporaryDirectory() as tmp:
        for src in sources:
            lang = "-lang=c++" if src.endswith(".cpp") else "-lang=c99"
            per_func = results.setdefault(src, {})
            for ver in versions:
                obj = os.path.join(tmp, "out.o")
                if os.path.exists(obj):
                    os.remove(obj)
                exe = os.path.join(MWCC_ROOT, *ver.split("/"), "mwccarm.exe")
                if not os.path.isfile(exe):
                    print(f"no compiler {ver} ({exe})", file=sys.stderr)
                    per_func.setdefault("<compile>", {})[ver] = None
                    continue
                cmd = [exe, *flags_for(args.flags, ver), lang]
                for d in args.define:
                    cmd += ["-d", d]
                for inc in includes:
                    cmd += ["-i", inc]
                cmd += ["-c", src, "-o", obj]
                out = subprocess.run(cmd, capture_output=True, text=True)
                if out.returncode != 0 or not os.path.exists(obj):
                    per_func.setdefault("<compile>", {})[ver] = None
                    continue
                for name, (code, mask, align) in read_elf(obj).items():
                    if len(code) < args.min_size:
                        continue
                    per_func.setdefault(name, {})[ver] = find(code, mask, align, mods)
                    # Code size, to tell a hit on a whole function from one on its start
                    per_func.setdefault("<size>", {}).setdefault(name, {})[ver] = len(code)

    totals = {ver: 0 for ver in versions}
    for src, funcs in results.items():
        print(f"== {os.path.basename(src)}")
        failed = [v for v, r in funcs.get("<compile>", {}).items() if r is None]
        if failed:
            print(f"   compile failed with: {', '.join(failed)}")
        for name, per_ver in sorted(funcs.items()):
            if name.startswith("<"):  # <compile>, <size>
                continue
            unique = {v: h[0] for v, h in per_ver.items() if h and len(h) == 1}
            multi = {v for v, h in per_ver.items() if h and len(h) > 1}
            for v in unique:
                totals[v] += 1
            if unique:
                addrs = sorted(set(unique.values()), key=fmt)
                print(f"   {name}: {', '.join(fmt(a) for a in addrs)} "
                      f"with {', '.join(sorted(unique))}")
            elif multi:
                print(f"   {name}: several addresses with {', '.join(sorted(multi))}")
    print("== unique matches per compiler")
    for ver, n in totals.items():
        print(f"   {ver}: {n}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({src: {fn: {v: h for v, h in pv.items()} for fn, pv in funcs.items()}
                       for src, funcs in results.items()}, f, indent=1)


if __name__ == "__main__":
    main()
