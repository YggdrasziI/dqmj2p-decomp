#!/usr/bin/env python3
"""Write the source of a library unit from the reference sources that the sweep
found byte-identical.

For a library unit of delinks.txt (made by tools/sweep_names.py), every function
must be a kept sweep hit. The reference sources of those functions (khdays-decomp
keeps one function per file) are merged in address order into one C file:

  - top-level declarations repeated by several files (types, includes, inline
    helpers) are written once;
  - a function defined in a reference file but not in this unit becomes a
    declaration, and a unit function loses `static` (other units may call it);
  - a variable defined in a reference file becomes an `extern` declaration (the
    data belongs to other units until data sections are assigned).

The merged file is compiled (project compiler and flags, -lang=c99) and compared
with the delinked original object (build/<version>/delinks/<unit>.o): same .text
size and bytes with relocated words masked. The symbols our code uses (callees,
data, the unit's own functions) are then renamed to this ROM's names, read from the
original object's relocations and symbols at the same offsets: identifiers of the
text are replaced, and a name that a header uses gets a #define. The file is
written only when the check passes; nothing else is changed.

When the merged file does not match, --drafts writes it out; once edited by hand,
--source checks, renames and writes it the same way.

Usage:
  python tools/lib_unit_gen.py libs/msl/src/c/unit_0201AD84.c [--force]
  python tools/lib_unit_gen.py --all [--split] [--module main] [--limit N] [--drafts DIR]
  python tools/lib_unit_gen.py --source DIR/libs/.../unit_X.c libs/.../unit_X.c
  python tools/lib_unit_gen.py --mark-complete    # after ninja: units at 100 %
"""

import argparse
import os
import re
import struct
import subprocess
import sys
import tempfile
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import compiler_sweep  # noqa: E402
import sweep_names  # noqa: E402

SWEEP_GLOB = os.path.join(ROOT, "build", "{version}", "sweep", "*.json")
HEADER = ("/* {lib}; built from {refs} (CC0) per-function sources of the functions\n"
          " * found byte-identical (tools/lib_unit_gen.py). File bounds are a hypothesis. */\n"
          "#pragma force_active on\n\n")
HEADER_EDITED = ("/* {lib}; built from {refs} (CC0) per-function sources, edited where this\n"
                 " * build differs (comments below); checked by tools/lib_unit_gen.py --source.\n"
                 " * File bounds are a hypothesis. */\n"
                 "#pragma force_active on\n\n")
R_ARM_V4BX = 40
TWEWY_INCLUDE = [os.path.join(ROOT, "libs", "nitro", "include_twewy"),
                 os.path.join(ROOT, "libs", "msl", "include_twewy")]
# Units that match with mwccarm on Windows but not under wibo (CI): never marked
# complete, so that the CI build links the original bytes
NOT_COMPLETE = {
    # 2.0/sp2p2 under wibo orders the switch tree of GX_VRAMCNT_SetSubBG_ around
    # case 4 instead of 0x80 (8 bytes shorter); Windows builds the original's
    "libs/nitro/src/gx/unit_020038A8.c",
}


# --- ELF ---------------------------------------------------------------------

def read_obj(path):
    """(text bytes, {offset: name} of functions, [(offset, type, name, addend)]
    relocations; the addend of a REL entry is 0, its word holds it)."""
    data = open(path, "rb").read()
    shoff, = struct.unpack_from("<I", data, 0x20)
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", data, 0x2E)
    secs = [struct.unpack_from("<IIIIIIIIII", data, shoff + i * shentsize) for i in range(shnum)]

    def cstr(sec, off):
        start = secs[sec][4] + off
        return data[start:data.index(b"\0", start)].decode("latin-1")

    # mwccarm writes one .text section per function, in source order; the link
    # lays them out in that order, each at its alignment.
    text = [i for i, s in enumerate(secs) if s[1] == 1 and cstr(shstrndx, s[0]) == ".text"]
    if not text:
        return None
    base, code = {}, bytearray()
    for i in text:
        align = max(secs[i][8], 1)
        code += b"\0" * (-len(code) % align)
        base[i] = len(code)
        code += data[secs[i][4]:secs[i][4] + secs[i][5]]
    syms, funcs = [], {}
    for s in secs:
        if s[1] == 2:
            for off in range(s[4], s[4] + s[5], 16):
                st_name, value, size, info, _, shndx = struct.unpack_from("<IIIBBH", data, off)
                name = cstr(s[6], st_name) if st_name else ""
                syms.append(name)
                if info & 0xF == 2 and shndx in base and not name.startswith("$"):
                    funcs[base[shndx] + (value & ~1)] = name
    relocs = []
    for s in secs:
        if s[1] in (4, 9) and s[7] in base:
            for off in range(s[4], s[4] + s[5], 12 if s[1] == 4 else 8):
                r_off, r_info = struct.unpack_from("<II", data, off)
                addend = struct.unpack_from("<i", data, off + 8)[0] if s[1] == 4 else 0
                if r_info & 0xFF != R_ARM_V4BX:
                    relocs.append((base[s[7]] + r_off, r_info & 0xFF, syms[r_info >> 8], addend))
    return bytes(code), funcs, sorted(relocs)


def masked(code, relocs):
    b = bytearray(code)
    for off, _, _, _ in relocs:
        b[off:off + 4] = b"\0\0\0\0"
    return bytes(b)


# --- C chunks ----------------------------------------------------------------

def chunks(text):
    """Top-level chunks of a C file: preprocessor lines, declarations ending with
    ';', and function definitions ending with their closing brace."""
    out, i, n, start = [], 0, len(text), 0
    depth = 0
    while i < n:
        c = text[i]
        if depth == 0 and c == "#" and strip_comments(text[start:i]).strip() == "":
            j = i
            while True:
                e = text.find("\n", j)
                e = n if e < 0 else e
                if text[e - 1:e] == "\\":
                    j = e + 1
                    continue
                break
            out.append(text[i:e].strip())
            i = start = e
            continue
        if text.startswith("//", i):
            e = text.find("\n", i)
            i = n if e < 0 else e
            continue
        if text.startswith("/*", i):
            e = text.find("*/", i + 2)
            i = n if e < 0 else e + 2
            continue
        if c in "\"'":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            i = j + 1
            continue
        if c in "({[":
            depth += 1
        elif c in ")}]":
            depth -= 1
            if depth == 0 and c == "}":
                head = text[start:i]
                brace = top_brace(head)
                if brace is not None and re.search(r"\)\s*$", strip_comments(head[:brace])):
                    out.append(text[start:i + 1].strip())
                    start = i + 1
        elif c == ";" and depth == 0:
            out.append(text[start:i + 1].strip())
            start = i + 1
        i += 1
    rest = text[start:].strip()
    if strip_comments(rest).strip():
        out.append(rest)
    return [strip_comments(c).strip() for c in out if strip_comments(c).strip()]


def strip_comments(s):
    return re.sub(r"/\*.*?\*/|//[^\n]*", "", s, flags=re.S)


def top_brace(head):
    depth = 0
    for i, c in enumerate(head):
        if c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        elif c == "{" and depth == 0:
            return i
    return None


def function_name(chunk):
    """Name of a function definition chunk, or None."""
    if chunk.startswith("#") or not chunk.endswith("}"):
        return None
    head = chunk[:top_brace(chunk)]
    if not re.search(r"\)\s*$", head) or re.match(r"\s*(typedef|struct|union|enum)\b[^(]*$", head):
        return None
    m = re.search(r"(\w+)\s*\([^()]*(\([^()]*\)[^()]*)*\)\s*$", head)
    return m.group(1) if m else None


def variable_definition(chunk):
    """True for a top-level variable definition (not extern, typedef or prototype)."""
    if chunk.startswith("#") or not chunk.endswith(";"):
        return False
    if re.match(r"(extern|typedef)\b", chunk):
        return False
    body = re.sub(r"\{.*\}", "{}", chunk, flags=re.S)
    if re.match(r"(struct|union|enum)\s+\w*\s*\{\}\s*;$", body.strip()):
        return False  # a type definition alone
    head = body.split("=")[0]
    if re.search(r"\w\s*\([^()]*\)\s*;?$", head.strip()) and "=" not in body:
        return False  # a prototype
    return "(" not in head or re.search(r"\(\s*\*", head) is not None


def merge(sources, unit_names):
    """Top-level chunks of one C file made of the reference sources."""
    seen, out = set(), []
    for path in sources:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        for chunk in chunks(text):
            name = function_name(chunk)
            if name is not None:
                head = chunk[:top_brace(chunk)].strip()
                if name in unit_names:
                    chunk = re.sub(r"^\s*static\s+(?!inline)", "", chunk)
                    # A function's static variable becomes a file-level extern
                    # declaration: its data stays with the rest of the data
                    hoisted = []

                    def hoist(m):
                        hoisted.append(f"extern {m[1].strip()} {m[2]};")
                        return ""
                    chunk = re.sub(r"(?m)^[ \t]*static\s+(?!inline)([\w\s\*]+?)\s*\b(\w+(?:\[[^\]]*\])*)\s*(?:=[^;]*)?;[ \t]*\n?",
                                   hoist, chunk)
                    for decl in hoisted:
                        if re.sub(r"\s+", " ", decl) not in seen:
                            seen.add(re.sub(r"\s+", " ", decl))
                            out.append(decl)
                elif not re.search(r"\binline\b", head):
                    chunk = re.sub(r"^\s*static\s+", "", head) + ";"
            elif variable_definition(chunk):
                decl = re.sub(r"\s*=.*;$", ";", chunk, flags=re.S)
                chunk = "extern " + re.sub(r"^\s*static\s+", "", decl)
            key = re.sub(r"\s+", " ", chunk)
            if key in seen:
                continue
            seen.add(key)
            out.append(chunk)
    # Each reference file declares what it uses before its function: once merged,
    # the directives, types, declarations and inline helpers of every file go first,
    # then the emitted functions in the order given. One file keeps its own order
    # (a #define may follow the type whose member it names).
    if len(sources) == 1:
        return out
    return sorted(out, key=chunk_rank)


def chunk_rank(chunk):
    if chunk.startswith("#"):
        return 0
    if function_name(chunk) is not None:
        head = chunk[:top_brace(chunk)]
        return 3 if re.search(r"\binline\b", head) else 4
    flat = re.sub(r"\{.*\}", "{}", chunk, flags=re.S)
    if re.match(r"(typedef\b|(struct|union|enum)\s+\w*\s*\{\}\s*;$)", flat):
        return 1
    return 2


def defines(chunk, ident):
    """True when a chunk defines the type `ident` (typedef or tagged definition)."""
    if chunk.startswith("#") or not chunk.endswith(";"):
        return False
    flat = re.sub(r"\{.*\}", "{}", chunk, flags=re.S)
    return bool(re.match(r"typedef\b.*\b" + ident + r"\s*;$", flat, re.S)
                or re.match(r"(struct|union|enum)\s+" + ident + r"\s*\{\}", flat))


def compile_chunks(parts, tmp, extra=()):
    """Compile, dropping a local type definition that a header already gives
    (mwccarm: "identifier 'X' redeclared") until it compiles."""
    parts = list(parts)
    err = ""
    for _ in range(20):
        obj, err = compile_c("\n\n".join(parts) + "\n", tmp, extra)
        if obj is not None:
            return obj, parts, ""
        m = re.search(r"identifier '(\w+)' redeclared", err)
        drop = [i for i, c in enumerate(parts) if m and defines(c, m[1])]
        if not drop:
            return None, parts, err
        del parts[drop[0]]
    return None, parts, err


# --- generation --------------------------------------------------------------

def compile_c(text, tmp, extra=()):
    src = os.path.join(tmp, "unit.c")
    obj = os.path.join(tmp, "unit.o")
    with open(src, "w", encoding="utf-8") as f:
        f.write(text)
    if os.path.exists(obj):
        os.remove(obj)
    ver = sweep_names_version()
    exe = os.path.join(ROOT, "tools", "mwccarm", *ver.split("/"), "mwccarm.exe")
    cmd = [exe, *compiler_sweep.flags_for(compiler_sweep.project_flags(), ver), "-lang=c99",
           *[a for d in extra for a in ("-i", d)],
           "-i", os.path.join(ROOT, "include"), "-i", os.path.join(ROOT, "libs", "nitro", "include"),
           "-c", src, "-o", obj]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 or not os.path.exists(obj):
        return None, (res.stdout + res.stderr).strip()
    return obj, ""


def sweep_names_version():
    m = re.search(r'^MWCC_DEFAULT_VERSION = "([^"]+)"',
                  open(os.path.join(ROOT, "tools", "configure.py"), encoding="utf-8").read(), re.M)
    return m[1]


def unit_blocks(version):
    """[(module, path, start, end)] of library units in delinks.txt."""
    out = []
    for module in ("main", "itcm"):
        path = os.path.join(sweep_names.config_dir(module, version), "delinks.txt")
        text = open(path, encoding="utf-8").read().replace("\r\n", "\n")
        for block in text.split("\n\n")[1:]:
            lines = block.strip().split("\n")
            if not lines[0].startswith("libs/"):
                continue
            m = re.search(r"\.text\s+start:(0x[0-9a-f]+) end:(0x[0-9a-f]+)", block)
            if m:
                out.append((module, lines[0].rstrip(":"), int(m[1], 16), int(m[2], 16),
                            "complete" in [l.strip() for l in lines[1:]]))
    return out


def kept_sources(version):
    """{(module, address): (reference, name, source path)} of whole-function hits."""
    import glob
    files = glob.glob(SWEEP_GLOB.format(version=version))
    modules = {m: sweep_names.read_functions(m, version)[0] for m, _, _ in sweep_names.LIBRARY_REGIONS}
    kept = defaultdict(dict)
    import json
    for path in files:
        data = json.load(open(path, encoding="utf-8"))
        for src, funcs in data.items():
            ref, _, _ = sweep_names.origin(src)
            sizes = funcs.get("<size>", {})
            for name, per in funcs.items():
                if name.startswith("<"):
                    continue
                addrs = {a for h in per.values() if h for a in h}
                if len(addrs) != 1:
                    continue
                addr = addrs.pop()
                if isinstance(addr, str):
                    continue
                module = sweep_names.module_of(addr)
                f = modules.get(module, {}).get(addr) if module else None
                size = max((sizes.get(name) or {}).values() or [0])
                if f and f[1] == size:
                    kept[(module, addr)].setdefault(ref, (name, src))
    out = {}
    for key, per in kept.items():
        ref = next(r for r in sweep_names.REFERENCES if r in per)
        out[key] = (ref,) + per[ref]
    return out, modules


def slice_obj(orig, lo, hi):
    """The part [lo, hi) of an object read by read_obj, at offset 0."""
    code, funcs, relocs = orig
    return (code[lo:hi], {o - lo: n for o, n in funcs.items() if lo <= o < hi},
            [(o - lo, t, n, a) for o, t, n, a in relocs if lo <= o < hi])


def generate(module, path, start, end, kept, modules, version, tmp, orig=None, source=None):
    """(source text, "ok") or (None, reason). `orig` is the original object of
    [start, end) (default: the delinked object of `path`). The last candidate
    text is left in generate.draft, also on failure. `source` is a hand-edited
    draft used instead of the merged reference sources."""
    generate.draft = None
    funcs = sorted(a for a in modules[module] if start <= a < end)
    missing = [a for a in funcs if (module, a) not in kept]
    if missing:
        return None, f"no reference source for 0x{missing[0]:08X}"
    sources, refs = [], []
    for a in funcs:
        ref, name, src = kept[(module, a)]
        if src not in sources:
            sources.append(src)
        if ref not in refs:
            refs.append(ref)
    unit_names = {kept[(module, a)][1] for a in funcs}
    if orig is None:
        orig_path = os.path.join(ROOT, "build", version, "delinks", os.path.splitext(path)[0] + ".o")
        orig = read_obj(orig_path) if os.path.exists(orig_path) else None
        if orig is None:
            return None, f"no delinked object {orig_path} (run ninja)"
    o_code, o_funcs, o_relocs = orig

    # mwccarm 2.0 emits a file's functions in reverse source order: try the sources
    # in reverse address order first.
    orders = [list(reversed(sources)), sources]
    # twewy's sources include twewy's own NitroSDK headers, searched first
    extra = TWEWY_INCLUDE if refs == ["twewy"] else []
    if source is not None:
        with open(source, encoding="utf-8") as f:
            orders = [[f.read().strip()]]
    for order in orders:
        merged = order if source is not None else merge(order, unit_names)
        obj, parts, err = compile_chunks(merged, tmp, extra)
        body = "\n\n".join(parts) + "\n"
        generate.draft = body
        if obj is None:
            return None, "compile error: " + (err.splitlines()[0] if err else "?")
        code, our_funcs, relocs = read_obj(obj)
        # The unit may end with the padding after a Thumb function
        if sorted(our_funcs) == sorted(o_funcs) and len(code) <= len(o_code) < len(code) + 4 \
                and not any(o_code[len(code):]):
            o_code = o_code[:len(code)]
            break
    else:
        return None, (f"layout differs: ours {len(code):#x} bytes "
                      + str({hex(k): v for k, v in sorted(our_funcs.items())})[:200])
    if masked(code, relocs) != masked(o_code, o_relocs):
        a, b = masked(code, relocs), masked(o_code, o_relocs)
        ends = sorted(our_funcs) + [len(code)]
        bad = [our_funcs[lo] for lo, hi in zip(ends, ends[1:]) if a[lo:hi] != b[lo:hi]]
        return None, "bytes differ in " + ", ".join(bad)
    if [(o, t) for o, t, _, _ in relocs] != [(o, t) for o, t, _, _ in o_relocs]:
        return None, "relocations differ"
    if [a for _, _, _, a in relocs] != [a for _, _, _, a in o_relocs]:
        return None, "relocation addends differ"
    renames = {}
    for off, name in our_funcs.items():
        if name != o_funcs[off]:
            renames[name] = o_funcs[off]
    for (off, _, ours, _), (_, _, orig_name, _) in zip(relocs, o_relocs):
        if ours != orig_name:
            if renames.get(ours, orig_name) != orig_name:
                return None, f"{ours} maps to two symbols"
            renames[ours] = orig_name
    lib = {"nitro": "NitroSDK", "nns": "NitroSystem", "msl": "MSL"}.get(path.split("/")[1], path.split("/")[1])
    names = {"khdays": "khdays-decomp", "twewy": "twewy"}
    text = (HEADER if source is None else HEADER_EDITED).format(
        lib=lib, refs=" and ".join(names[r] for r in refs))
    # The reference's names of callees, data and the unit's own functions become
    # this ROM's (an identifier rename; strings and comments hold none of them)
    if renames:
        # A name that a header macro uses keeps its spelling there: map it with a
        # #define after the last leading directive
        headers = header_text()
        hidden = [k for k in renames if re.search(r"\b" + re.escape(k) + r"\b", headers)]
        pattern = re.compile(r"\b(" + "|".join(map(re.escape, renames)) + r")\b")
        body = pattern.sub(lambda m: renames[m[1]], body)
        if hidden:
            lines = body.split("\n")
            at = max((i + 1 for i, l in enumerate(lines) if l.startswith("#include")), default=0)
            lines[at:at] = ["", "/* used by header macros under the reference's names */"] + \
                [f"#define {k} {renames[k]}" for k in hidden]
            body = "\n".join(lines)
    text += body
    generate.draft = text
    obj, err = compile_c(text, tmp, extra)
    if obj is None:
        return None, "compile error after renames: " + err.splitlines()[0]
    code, our_funcs, relocs = read_obj(obj)
    if masked(code, relocs) != masked(o_code, o_relocs) \
            or [r[1:] for r in relocs] != [r[1:] for r in o_relocs] or our_funcs != o_funcs:
        return None, "check after renames failed"
    return text, "ok"


def header_text():
    """Text of every header of the include directories (cached)."""
    if not hasattr(header_text, "text"):
        parts = []
        for inc in (os.path.join(ROOT, "include"), os.path.join(ROOT, "libs", "nitro", "include"),
                    *TWEWY_INCLUDE):
            for dirpath, _, files in os.walk(inc):
                for name in files:
                    if name.endswith(".h"):
                        with open(os.path.join(dirpath, name), encoding="utf-8", errors="replace") as f:
                            parts.append(f.read())
        header_text.text = "\n".join(parts)
    return header_text.text


def write(target, text):
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def file_groups(module, start, end, kept, modules):
    """[(start, end)] runs of consecutive functions from one reference file; the
    padding after a run stays with it, and the last run keeps the unit's end."""
    funcs = sorted(a for a in modules[module] if start <= a < end)
    groups = []
    for a in funcs:
        src = kept.get((module, a), (None, None, None))[2]
        if groups and groups[-1][2] == src:
            groups[-1][1] = a + modules[module][a][1]
        else:
            if groups:
                groups[-1][1] = a
            groups.append([a, a + modules[module][a][1], src])
    if groups:
        groups[-1][1] = end
    return [(lo, hi) for lo, hi, _ in groups]


def split_block(module, version, path, parts):
    """Replace the delinks.txt block of `path` by one block per part."""
    cfg = os.path.join(sweep_names.config_dir(module, version), "delinks.txt")
    with open(cfg, encoding="utf-8", newline="") as f:
        text = f.read()
    nl = "\r\n" if "\r\n" in text else "\n"
    text = text.replace("\r\n", "\n")
    m = re.search(r"(?m)^" + re.escape(path) + r":\n(    .*\n?)+", text)
    new = "\n".join(f"{p}:\n    .text       start:0x{lo:08x} end:0x{hi:08x}\n" for p, lo, hi in parts)
    text = text[:m.start()] + new + text[m.end():]
    with open(cfg, "w", encoding="utf-8", newline="") as f:
        f.write(text.replace("\n", nl))


def mark_complete(version):
    """Add `complete` to every libs/ unit of delinks.txt that has a source and is at
    100 % for all its functions and sections in build/<version>/report.json."""
    import json
    with open(os.path.join(ROOT, "build", version, "report.json"), encoding="utf-8") as f:
        report = json.load(f)
    full = set()
    for u in report["units"]:
        if not u["name"].startswith("libs/") or not os.path.exists(os.path.join(ROOT, u["name"] + ".c")):
            continue
        scores = [s.get("fuzzy_match_percent") for s in u.get("sections", [])] + \
                 [f.get("fuzzy_match_percent") for f in u.get("functions", [])]
        if scores and all(x == 100.0 for x in scores):
            full.add(u["name"] + ".c")
    count = 0
    for module in ("main", "itcm"):
        cfg = os.path.join(sweep_names.config_dir(module, version), "delinks.txt")
        with open(cfg, encoding="utf-8", newline="") as f:
            text = f.read()
        nl = "\r\n" if "\r\n" in text else "\n"
        blocks = text.replace("\r\n", "\n").split("\n\n")
        for i, block in enumerate(blocks[1:], 1):
            lines = block.split("\n")
            path = lines[0].rstrip(":")
            if path in full and path not in NOT_COMPLETE and "    complete" not in lines:
                lines.insert(1, "    complete")
                blocks[i] = "\n".join(lines)
                count += 1
        with open(cfg, "w", encoding="utf-8", newline="") as f:
            f.write("\n\n".join(blocks).replace("\n", nl))
    print(f"marked complete: {count}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("units", nargs="*", help="unit paths (libs/.../unit_XXXXXXXX.c)")
    ap.add_argument("--all", action="store_true", help="every library unit without a source")
    ap.add_argument("--module", choices=("main", "itcm"))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--force", action="store_true", help="overwrite an existing source")
    ap.add_argument("--version", default="jpn")
    ap.add_argument("--split", action="store_true",
                    help="split a unit that does not build as one into one unit per reference file")
    ap.add_argument("--source", metavar="DRAFT",
                    help="check and write one unit from a hand-edited draft (a --drafts file)")
    ap.add_argument("--drafts", metavar="DIR",
                    help="write the last candidate source of a failed unit under DIR")
    ap.add_argument("--mark-complete", action="store_true",
                    help="only mark the libs/ units at 100 %% in report.json complete")
    args = ap.parse_args()
    if args.source and (len(args.units) != 1 or args.all):
        sys.exit("--source takes exactly one unit")
    if args.mark_complete:
        return mark_complete(args.version)

    kept, modules = kept_sources(args.version)
    blocks = unit_blocks(args.version)
    if args.units:
        wanted = {u.replace("\\", "/") for u in args.units}
        blocks = [b for b in blocks if b[1] in wanted]
    elif not args.all:
        sys.exit(__doc__)
    if args.module:
        blocks = [b for b in blocks if b[0] == args.module]
    done = 0
    with tempfile.TemporaryDirectory() as tmp:
        for module, path, start, end, complete in blocks:
            target = os.path.join(ROOT, path)
            if os.path.exists(target) and not args.force:
                continue
            if args.limit is not None and done >= args.limit:
                break
            text, why = generate(module, path, start, end, kept, modules, args.version, tmp,
                                 source=args.source)
            print(f"{path}: {why}")
            if text is None and args.drafts and generate.draft:
                write(os.path.join(args.drafts, path), generate.draft)
            if text is not None:
                write(target, text)
                done += 1
                continue
            # Split into one unit per reference file (each compiled alone in the sweep)
            groups = file_groups(module, start, end, kept, modules)
            if len(groups) < 2 or not args.split:
                continue
            orig_path = os.path.join(ROOT, "build", args.version, "delinks",
                                     os.path.splitext(path)[0] + ".o")
            if not os.path.exists(orig_path):
                continue
            orig = read_obj(orig_path)
            folder = os.path.dirname(path)
            parts, results = [], []
            for lo, hi in groups:
                sub_path = f"{folder}/unit_{lo:08X}.c"
                text, why = generate(module, sub_path, lo, hi, kept, modules, args.version, tmp,
                                     orig=slice_obj(orig, lo - start, hi - start))
                print(f"  {sub_path}: {why}")
                parts.append((sub_path, lo, hi))
                results.append(text)
            if any(results):
                split_block(module, args.version, path, parts)
                for (sub_path, _, _), text in zip(parts, results):
                    if text is not None:
                        write(os.path.join(ROOT, sub_path), text)
                        done += 1
    print(f"written: {done}")


if __name__ == "__main__":
    main()
