#!/usr/bin/env python3
"""Dump mwccarm's front-end IR (the IRO optimizer's flowgraph) for one source file.

mwccarm 2.0/sp1p5 still contains the IRO optimizer's logging code, but nothing turns
it on. This script writes a patched copy of the compiler under build/irdump/ (three
bytes changed, see PATCHES) that writes "<source>.log" next to the source: for each
function, the IR after every IRO pass. The object code is unchanged. The back end's
dumps (PCode, register coloring, scheduling) cannot be enabled the same way: their
print function is an empty stub in this build.

The source is compiled with its own command from build.ninja (`ninja -t commands`),
from a copy under build/irdump/, so the log never lands in src/.

Usage:
  python tools/mwcc_irdump.py <src/...cpp> [--list]
      [--func <IR name>]        (as in "Dumping function <name> ..."; default: all)
      [--pass "before IRO_Delinearize" | all]   (default: the last pass)
      [--out <file>]            (default: stdout)
      [--version jpn]

--list prints the function names and passes found in the log.
"""

import argparse
import hashlib
import os
import re
import shlex
import shutil
import struct
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "build", "irdump")

# SHA-1 of the compiler the patches were found in.
COMPILER_SHA1 = {"2.0/sp1p5": "6153c610734690650e563e03c5bbf45c9a060ee6"}
TRACE_COMPILER = os.path.join("tools", "mwccarm", "2.0", "sp1p5", "mwccarm.exe")

# (virtual address, original bytes, patched bytes) for mwccarm 2.0/sp1p5
PATCHES = [
    # IRO init clears the log flag: set it instead ("mov byte ptr [0x64641c], 1")
    (0x4F4CD2, "c6051c64640000", "c6051c64640001"),
    # "Dumping function %s before/after %s": skip the test of the dump option
    (0x5602A4, "75", "eb"),
    (0x560344, "75", "eb"),
]

DUMP_RE = re.compile(r"^(?:Dumping|Optimizing) function (\S+) ((?:before|after) .*?) \(tick \d+\)$")


def va_to_offset(data, va):
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    sections = struct.unpack_from("<H", data, pe + 6)[0]
    opt_size = struct.unpack_from("<H", data, pe + 20)[0]
    image_base = struct.unpack_from("<I", data, pe + 24 + 28)[0]
    rva = va - image_base
    table = pe + 24 + opt_size
    for i in range(sections):
        vsize, vaddr, _, raw = struct.unpack_from("<IIII", data, table + i * 40 + 8)
        if vaddr <= rva < vaddr + max(vsize, 1):
            return raw + rva - vaddr
    raise ValueError("address 0x%x is in no section" % va)


def patched_compiler(compiler):
    """Write the patched copy of `compiler` (with its DLLs) under build/irdump/."""
    rel = os.path.relpath(os.path.dirname(compiler), os.path.join(ROOT, "tools", "mwccarm"))
    version = rel.replace(os.sep, "/")
    data = open(compiler, "rb").read()
    if hashlib.sha1(data).hexdigest() != COMPILER_SHA1.get(version):
        sys.exit("mwcc_irdump: only mwccarm %s is supported (got %s)"
                 % (", ".join(COMPILER_SHA1), version))
    out_dir = os.path.join(WORK, "mwccarm", version)
    out = os.path.join(out_dir, "mwccarm_ir.exe")
    os.makedirs(out_dir, exist_ok=True)
    for name in os.listdir(os.path.dirname(compiler)):
        if name.lower().endswith(".dll"):
            shutil.copy2(os.path.join(os.path.dirname(compiler), name), out_dir)
    patched = bytearray(data)
    for va, old, new in PATCHES:
        off = va_to_offset(data, va)
        old, new = bytes.fromhex(old), bytes.fromhex(new)
        if data[off:off + len(old)] != old:
            sys.exit("mwcc_irdump: unexpected bytes at 0x%x" % va)
        patched[off:off + len(new)] = new
    open(out, "wb").write(patched)
    return out


def compile_command(source, version):
    obj = os.path.join("build", version, os.path.splitext(source)[0] + ".o")
    r = subprocess.run(["ninja", "-t", "commands", obj], cwd=ROOT, capture_output=True, text=True)
    lines = r.stdout.strip().splitlines()
    if r.returncode or not lines:
        sys.exit("mwcc_irdump: no build command for %s\n%s" % (obj, r.stdout + r.stderr))
    # posix=False keeps the backslashes of Windows paths, and also the quotes
    args = [a[1:-1] if len(a) > 1 and a[0] == a[-1] == '"' else a
            for a in shlex.split(lines[-1], posix=False)]
    # The patches are for 2.0/sp1p5's binary, while the build may use another 2.0
    # (MWCC_DEFAULT_VERSION). Trace with sp1p5 and the build's flags: the trace only
    # explains the build's code when sp1p5 compiles the unit to the same bytes (true
    # for every unit checked so far, notes/COMPILER.md).
    args[0] = TRACE_COMPILER
    return args


def run(source, version):
    source = os.path.normpath(os.path.relpath(os.path.abspath(source), ROOT))
    args = compile_command(source, version)
    compiler = os.path.join(ROOT, args[0])
    copy = os.path.join(WORK, source)
    os.makedirs(os.path.dirname(copy), exist_ok=True)
    shutil.copy2(os.path.join(ROOT, source), copy)
    log = os.path.splitext(copy)[0] + ".log"
    if os.path.exists(log):
        os.remove(log)
    out = []
    i = 1
    while i < len(args):
        a = args[i]
        if a == "-MD":
            pass
        elif a == "-o":
            out += ["-o", os.path.dirname(copy)]
            i += 1
        elif os.path.normpath(a) == source:
            out.append(copy)
        else:
            out.append(a)
        i += 1
    r = subprocess.run([patched_compiler(compiler)] + out, cwd=ROOT, capture_output=True, text=True)
    if r.returncode:
        sys.exit("mwcc_irdump: compile failed\n" + r.stdout + r.stderr)
    if not os.path.exists(log):
        sys.exit("mwcc_irdump: the compiler wrote no log")
    return open(log, encoding="latin-1").read().splitlines()


def sections(lines):
    """[(function, pass, lines)] for every dump with a body. IR names are not
    qualified (two classes' func_34 are both "func_34"): the n-th function of a
    name in the log is "name#n" from the second one on."""
    result, current, count, key = [], None, {}, None
    for line in lines:
        m = DUMP_RE.match(line)
        if m or line.startswith("Starting function "):
            current = None
        if line.startswith("Starting function "):
            name = line[len("Starting function "):].strip()
            count[name] = count.get(name, 0) + 1
            key = name if count[name] == 1 else "%s#%d" % (name, count[name])
        if m and line.startswith("Dumping"):
            current = (key or m.group(1), m.group(2), [])
            result.append(current)
        elif current is not None:
            current[2].append(line)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("source")
    ap.add_argument("--func")
    ap.add_argument("--pass", dest="pass_name")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--version", default="jpn")
    a = ap.parse_args()

    dumps = sections(run(a.source, a.version))
    if a.func:
        dumps = [d for d in dumps if d[0] == a.func]
        if not dumps:
            sys.exit("mwcc_irdump: no dump for function %s (see --list)" % a.func)
    if a.list:
        seen = {}
        for func, pass_name, _ in dumps:
            seen.setdefault(func, []).append(pass_name)
        for func, passes in seen.items():
            print(func)
            for p in passes:
                print("    " + p)
        return
    if a.pass_name != "all":
        by_func = {}
        for d in dumps:
            if a.pass_name is None or d[1] == a.pass_name:
                by_func[d[0]] = d  # the last matching dump of each function
        dumps = list(by_func.values())
        if not dumps:
            sys.exit("mwcc_irdump: no dump for pass '%s' (see --list)" % a.pass_name)
    text = "".join("== %s %s ==\n%s\n" % (f, p, "\n".join(body).strip("\n") + "\n")
                   for f, p, body in dumps)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(text)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
