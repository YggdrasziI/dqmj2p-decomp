#!/usr/bin/env python3
"""Search source forms of one function until its compiled bytes equal the original.

mwccarm's register allocation depends on declaration order, scopes and copies in ways that
cannot be predicted by hand, but a unit compiles in a fraction of a second, so a random
search is practical. Variants live in a temporary directory: the repository is never
written. A result counts only when the bytes equal the target (`score` 0), and then
`report_diff.py` and `ninja` remain the proof. The output form is a compiler fact, not
evidence of the original source.

  python tools/permute.py <unit> <symbol> [--seconds N] [--workers N] [--out DIR]
  python tools/permute.py src/ov005/unit_XXXXXXXX func_ov005_xxxxxxxx

With 0 reached, mutations that are not needed are reverted and the function, from its
signature to its closing brace, is written to <out>/<symbol>.txt after a line naming the
source file. Inserted locals use `__typeof__`: write the real type when keeping a result.
"""
import argparse
import difflib
import json
import os
import random
import re
import struct
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

# The project's game compiler (MWCC_DEFAULT_VERSION of tools/configure.py)
CC = os.path.join("tools", "mwccarm", re.search(
    r'^MWCC_DEFAULT_VERSION = "([^"]+)"',
    open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "configure.py"), encoding="utf-8").read(),
    re.M)[1], "mwccarm.exe")
# mwccarm refuses an include folder that does not exist: keep the existing ones
INCLUDES = [d for d in ("include", "libs/c/include", "libs/cpp/include", "libs/nitro/include",
                        "libs/runtime/include") if os.path.isdir(d)]
FLAGS = ("-O4,p -enum int -char signed -proc arm946e -gccext,on -fp soft -inline noauto -RTTI off "
         "-interworking -w off -sym on -gccinc -nolink -msgstyle gcc -enc SJIS -str reuse "
         "-Cpp_exceptions off").split() + [x for d in INCLUDES for x in ("-i", d)] + \
        "-lang=c++ -d REGION_JPN".split()


def elf_func(path, name):
    """Return (bytes, {reloc offset: target symbol name}) of a function symbol."""
    b = open(path, "rb").read()
    shoff = struct.unpack_from("<I", b, 32)[0]
    shes, shnum = struct.unpack_from("<HH", b, 46)
    S = [struct.unpack_from("<10I", b, shoff + i * shes) for i in range(shnum)]
    # name, type, flags, addr, offset, size, link, info, align, entsize
    for s in S:
        if s[1] == 2:
            strs = S[s[6]]
            for o in range(s[4], s[4] + s[5], 16):
                n, val, size, info, oth, shndx = struct.unpack_from("<IIIBBH", b, o)
                e = b.index(b"\0", strs[4] + n)
                if b[strs[4] + n:e].decode("latin1") == name and shndx and shndx < shnum:
                    sec = S[shndx]
                    data = b[sec[4] + val : sec[4] + val + size]
                    rel = {}
                    for r in S:
                        if r[1] in (4, 9) and r[7] == shndx:
                            for q in range(r[4], r[4] + r[5], r[9]):
                                off, inf = struct.unpack_from("<II", b, q)
                                if val <= off < val + size:
                                    word = struct.unpack_from("<I", b, sec[4] + off)[0]
                                    rel[off - val] = target_name(b, S, r[6], inf >> 8, inf & 0xFF, word)
                    return data, rel
    return None, None


def target_name(b, S, symtab, index, rtype, word):
    """What a relocation refers to, as (name, content), independent of the rest of the unit.

    The names are numbered by the compiler across the unit, so they are normalised: a
    function-local static `name$<n>` loses its number and an anonymous literal `@<n>` is
    just `@`. A data object also carries its first bytes (its size, or up to a NUL when it
    has none, at most 64; `.bss` objects their size), compared when either side is
    anonymous (see same_target). A section symbol of an absolute word (R_ARM_ABS32, addend
    in the word) is replaced by the symbol defined at that offset of the section, the way
    the delinked target names it."""
    s = S[symtab]
    n, val, size, info, oth, shndx = struct.unpack_from("<IIIBBH", b, s[4] + index * 16)
    nm = sym_name(b, S, symtab, index)
    if (info & 0xF) == 3 and rtype == 2 and 0 < shndx < len(S):  # STT_SECTION + addend
        for o in range(s[4], s[4] + s[5], 16):
            n2, v2, z2, i2, _, x2 = struct.unpack_from("<IIIBBH", b, o)
            if x2 == shndx and v2 == word and n2 and (i2 & 0xF) != 3 and not sym_name(b, S, symtab, (o - s[4]) // 16).startswith("$"):
                return target_name(b, S, symtab, (o - s[4]) // 16, 0, 0)
        return ("%s+0x%x" % (nm, word), None)
    nm = "@" if re.match(r"^@\d+$", nm) else re.sub(r"\$\d+$", "", nm)
    if rtype == 2 and word:  # an absolute word: its addend is part of the target
        nm += "+0x%x" % word
    content = None
    if 0 < shndx < len(S) and (info & 0xF) != 2 and S[shndx][1] != 0:  # data, not a function
        sec = S[shndx]
        if sec[1] == 8:  # SHT_NOBITS
            content = "bss%d" % size
        else:
            start = sec[4] + val
            if size:
                end = start + min(size, 64)
            else:
                end = b.find(b"\0", start, start + 64)
                end = start + 64 if end < 0 else end
            content = b[start:end].hex()
    return (nm, content)


def same_target(a, b):
    """Equal names, or the same content when either side is an anonymous literal."""
    if a is None or b is None:
        return a is b
    if a[0] == b[0] and (a[0] != "@" or a[1] == b[1]):
        return True
    if "@" in (a[0], b[0]) and a[1] is not None and b[1] is not None:
        x, y = a[1], b[1]
        return x == y or (not x.startswith("bss") and not y.startswith("bss") and (x.startswith(y) or y.startswith(x)) and min(len(x), len(y)) > 0)
    return False


def sym_name(b, S, symtab, index):
    """Name of a relocation's symbol; a section symbol is named by its section."""
    s = S[symtab]
    n, val, size, info, oth, shndx = struct.unpack_from("<IIIBBH", b, s[4] + index * 16)
    strs = S[s[6]]
    e = b.index(b"\0", strs[4] + n)
    nm = b[strs[4] + n:e].decode("latin1")
    if not nm and 0 < shndx < len(S):
        sh = S[shndx]
        shstr = S[struct.unpack_from("<H", b, 50)[0]]
        e = b.index(b"\0", shstr[4] + sh[0])
        nm = b[shstr[4] + sh[0]:e].decode("latin1")
    return nm


WRITE = re.compile(r"^\s*(?:[\w:<>]+[\s*&]+)*(\w+)\s*(?:[-+*/|&^]|<<|>>)?=(?!=)")


def depends(a, b):
    """True if two statements cannot be swapped: one writes a local the other uses."""
    for x, y in ((a, b), (b, a)):
        m = WRITE.match(x)
        if m and re.search(r"\b%s\b" % re.escape(m.group(1)), y):
            return True
    return False


def score(path, name, tgt, trel):
    d, rel = elf_func(path, name)
    if d is None:
        return 10**6
    if len(d) != len(tgt):
        return 5000 + abs(len(d) - len(tgt))
    n = 0
    for i in range(0, len(tgt), 4):
        if i in trel or i in rel:
            # A relocated word: compare what it refers to (a swapped pair of
            # calls has the same bytes but other targets).
            if not same_target(trel.get(i), rel.get(i)):
                n += 1
            continue
        if d[i : i + 4] != tgt[i : i + 4]:
            n += 1
    return n


DECL = re.compile(r"^(\s*)((?:const\s+)?(?:unsigned\s+)?[A-Za-z_]\w*(?:\s*\*+\s*|\s+)(?:const\s+)?)([a-z_A-Z]\w*)(\s*=\s*(.*))?;\s*$")
KW = {"return", "goto", "delete", "else", "case", "break", "continue", "new"}


def is_decl(l):
    m = DECL.match(l)
    return m if m and m.group(2).split()[0] not in KW else None


def block_end(L, i):
    return not L[i].rstrip().endswith("{") and not L[i].strip().startswith("}")


COUNTER = re.compile(r"for \((int|u32) (\w+) = ([^;]+); (\(int\))?\2 (<=?) ")


def counter_type(L):
    """Cycle a loop counter through `int i; i <`, `u32 i; (int)i <` and `u32 i; i <` (also `<=`)."""
    c = [i for i, l in enumerate(L) if COUNTER.search(l)]
    if not c:
        return L
    i = random.choice(c)
    m = COUNTER.search(L[i])
    forms = ["for (int {0} = {1}; {0} {2} ", "for (u32 {0} = {1}; (int){0} {2} ", "for (u32 {0} = {1}; {0} {2} "]
    cur = 0 if m.group(1) == "int" else (1 if m.group(4) else 2)
    new = random.choice([f for j, f in enumerate(forms) if j != cur]).format(m.group(2), m.group(3), m.group(5))
    L[i] = L[i][: m.start()] + new + L[i][m.end() :]
    return L


def split_top(s, ops):
    """Split s at its top-level occurrences of the operators in ops (longest first).

    Returns ([(offset, part), ...], [operator, ...]). Only () and [] nesting is tracked:
    templates, casts with <>, character literals and ?: are not parsed."""
    parts, depth, i, start, found = [], 0, 0, 0, []
    while i < len(s):
        ch = s[i]
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif depth == 0:
            for op in ops:
                if (s.startswith(op, i) and not (op in ("<", ">") and s[i + 1 : i + 2] in ("<", ">", "="))
                        and not (i > 0 and s[i - 1] in "<>-=!")):
                    parts.append((start, s[start:i]))
                    found.append(op)
                    i += len(op)
                    start = i
                    break
            else:
                i += 1
                continue
            continue
        i += 1
    parts.append((start, s[start:]))
    return parts, found


def insert_ok(L, i):
    """True when a declaration can be inserted right before line i in the same scope."""
    if L[i].lstrip().startswith("}"):
        return False
    j = i - 1
    while j >= 0 and not L[j].strip():
        j -= 1
    if j < 0:
        return True
    prev = L[j].strip()
    if re.match(r"^(case\b.*|default\s*):$", prev):
        return False
    return prev.endswith((";", "{", "}"))


def hoist_compare(L):
    """Read one side of an `if (a OP b)` comparison into a local first."""
    c = []
    for i, l in enumerate(L):
        m = re.match(r"^(\s*)if \((.*)\) \{$", l)
        if not m or not insert_ok(L, i):
            continue
        parts, ops = split_top(m.group(2), ("==", "!=", "<=", ">=", "<", ">", "&&", "||"))
        if ops not in (["=="], ["!="], ["<="], [">="], ["<"], [">"]):
            continue
        for off, side in parts:
            lead = len(side) - len(side.lstrip())
            side = side.strip()
            if re.search(r"[(\[]|->", side) and not side.startswith("!"):
                c.append((i, m.group(1), m.start(2) + off + lead, side))
    if not c:
        return L
    i, ind, at, side = random.choice(c)
    n = "cmp%d_" % i
    while l_has(L, n):
        n += "_"
    L[i] = L[i][:at] + n + L[i][at + len(side) :]
    L.insert(i, "%s__typeof__(%s) %s = %s;" % (ind, side, n, side))
    return L


def l_has(L, name):
    return re.search(r"\b%s\b" % re.escape(name), "\n".join(L)) is not None


def assigns(line, name):
    n = re.escape(name)
    return re.search(r"\b%s\s*(=(?!=)|\+\+|--|[-+*/|&^]=)|(\+\+|--)\s*%s\b" % (n, n), line) is not None


def element_pointer(L):
    """Replace `a[i].` / `&a[i]` by a local pointer `p = &a[i]` from its first use to the end of
    that block, or to the first assignment of `a` or `i`. Only plain arrays (not `o->a[i]`)."""
    c = []
    for i, l in enumerate(L):
        if not l.rstrip().endswith(";") or not insert_ok(L, i):
            continue
        for m in re.finditer(r"(?<![.>\w])([a-z_]\w*)\[(\w+)\]\.", l):
            if not assigns(l, m.group(2)):
                c.append((i, m.group(1), m.group(2)))
    if not c:
        return L
    i, a, x = random.choice(c)
    p = "%s_%s_p" % (a, x)
    while l_has(L, p):
        p += "_"
    ind = re.match(r"^(\s*)", L[i]).group(1)
    pat = re.compile(r"(?<![.>\w])(?:&%s\[%s\](?![.\[])|%s\[%s\]\.)" % ((re.escape(a), re.escape(x)) * 2))
    j = i
    while j < len(L) and (not L[j].strip() or re.match(r"^(\s*)", L[j]).group(1).startswith(ind)):
        L[j] = pat.sub(lambda m: p if m.group(0).startswith("&") else p + "->", L[j])
        if j > i and (assigns(L[j], x) or assigns(L[j], a)):
            break
        j += 1
    L.insert(i, "%s__typeof__(%s[0]) *%s = &%s[%s];" % (ind, a, p, a, x))
    return L


FOCUS = None  # (texts of the body lines near a difference, texts of all body lines); None: all


def in_focus(line):
    """A line may be mutated: near a difference (--focus), or inserted by a mutation."""
    return FOCUS is None or line in FOCUS[0] or line not in FOCUS[1]


HOIST = re.compile(r"(?<![\w.>])([A-Za-z_]\w*(?:(?:->|\.)\w+)+(?:\([^()]*\))?|[A-Za-z_]\w*\([^()]*\))")


def hoist_expr(L):
    """Read a call or member expression of a statement into a local just before it."""
    c = []
    for i, l in enumerate(L):
        if not l.rstrip().endswith(";") or not insert_ok(L, i) or not in_focus(l):
            continue
        if re.match(r"^\s*(return|case|default|break|continue)\b", l):
            continue
        for m in HOIST.finditer(l):
            after = l[m.end() :].lstrip()
            if l[: m.start()].rstrip().endswith(("&", "++", "--")):
                continue
            if after.startswith(("++", "--", "(", "[")) or (after.startswith("=") and not after.startswith("==")):
                continue
            if re.match(r"[-+*/|&^]=", after):
                continue
            c.append((i, m.start(), m.group(1)))
    if not c:
        return L
    i, at, e = random.choice(c)
    n = "t%d_" % i
    while l_has(L, n):
        n += "_"
    ind = re.match(r"^(\s*)", L[i]).group(1)
    L[i] = L[i][:at] + n + L[i][at + len(e) :]
    L.insert(i, "%s__typeof__(%s) %s = %s;" % (ind, e, n, e))
    return L


def scope_block(L):
    """Put one or two consecutive simple statements in their own { } block."""
    c = [i for i, l in enumerate(L) if l.strip().endswith(";") and block_end(L, i) and in_focus(l)
         and not re.match(r"^\s*(case|default|return|break)\b", l)]
    if not c:
        return L
    i = random.choice(c)
    j = i
    if i + 1 < len(L) and L[i + 1].strip().endswith(";") and block_end(L, i + 1) and random.random() < 0.5:
        j = i + 1
    ind = re.match(r"^(\s*)", L[i]).group(1)
    return L[:i] + [ind + "{"] + ["    " + x for x in L[i : j + 1]] + [ind + "}"] + L[j + 1 :]


def mutate(L):
    L = list(L)
    k = random.random()
    idx = [i for i in range(len(L)) if in_focus(L[i])]
    if k < 0.12:  # a subexpression in a local, or statements in their own scope
        R = random.choice((hoist_expr, hoist_expr, scope_block))(list(L))
        if R != L:
            return R
    k = random.random()
    if k < 0.15:  # loop counter type, compared operand in a local, element pointer
        R = random.choice((counter_type, hoist_compare, element_pointer))(list(L))
        if R != L:
            return R
    k = random.random()
    if k < 0.28:  # swap adjacent simple statements
        c = [i for i in idx if i + 1 < len(L) and block_end(L, i) and block_end(L, i + 1) and L[i].strip().endswith(";") and L[i + 1].strip().endswith(";")
             and not depends(L[i], L[i + 1])]
        if c:
            i = random.choice(c)
            L[i], L[i + 1] = L[i + 1], L[i]
    elif k < 0.48:  # move a declaration without initializer anywhere above its use
        c = [i for i in idx if is_decl(L[i]) and not is_decl(L[i]).group(4)]
        if c:
            i = random.choice(c)
            l = L.pop(i)
            L.insert(random.randrange(0, min(i + 3, len(L))), l)
    elif k < 0.66:  # split `T x = e;` into a declaration at the top and an assignment
        c = [i for i in idx if is_decl(L[i]) and is_decl(L[i]).group(4)]
        if c:
            i = random.choice(c)
            m = is_decl(L[i])
            L[i] = "%s%s = %s;" % (m.group(1), m.group(3), m.group(5))
            L.insert(random.randrange(0, 4) if len(L) > 4 else 0, "    %s%s;" % (m.group(2).replace("const ", ""), m.group(3)))
    elif k < 0.76:  # add or drop const on an initialised local
        c = [i for i in idx if is_decl(L[i]) and is_decl(L[i]).group(4)]
        if c:
            i = random.choice(c)
            L[i] = L[i].replace("const ", "", 1) if "const " in L[i] else re.sub(r"^(\s*)", r"\1const ", L[i], count=1)
    elif k < 0.88:  # commute an operator
        c = [i for i in idx if re.search(r"\S+ (==|!=|\+|\*|&|\|) \S+", L[i])]
        if c:
            i = random.choice(c)
            ms = list(re.finditer(r"([\w\->\.\[\]]+) (==|!=|\+|\*|&|\|) ([\w\->\.\[\]]+)", L[i]))
            if ms:
                m = random.choice(ms)
                L[i] = L[i][: m.start()] + m.group(3) + " " + m.group(2) + " " + m.group(1) + L[i][m.end() :]
    elif k < 0.95:  # a copy of a local: `T y = x;` right after its declaration, then use y
        c = [i for i in idx if is_decl(L[i]) and is_decl(L[i]).group(4)]
        if c:
            i = random.choice(c)
            m = is_decl(L[i])
            n = m.group(3)
            nn = n + "_"
            if not l_has(L, nn):
                L.insert(i + 1, "%s%s%s = %s;" % (m.group(1), m.group(2), nn, n))
                for j in range(i + 2, len(L)):
                    if random.random() < 0.7:
                        L[j] = re.sub(r"\b%s\b" % n, nn, L[j])
    else:  # compound assignment split `x += e;` <-> `x = x + e;`
        c = [i for i in idx if re.search(r"^\s*\w+ [-+|&]= ", L[i])]
        if c:
            i = random.choice(c)
            m = re.match(r"^(\s*)(\w+) ([-+|&])= (.*);", L[i])
            if m:
                L[i] = "%s%s = %s %s %s;" % (m.group(1), m.group(2), m.group(2), m.group(3), m.group(4))
    return L



def locate(symbol):
    """Regex for the definition of a mangled or plain symbol."""
    f = re.match(r"_Z(\d+)", symbol)
    if f:  # free function: _Z<length><name><parameters>
        name = symbol[f.end() : f.end() + int(f.group(1))]
        return r"^[^\n;#]*\b%s\([^;{]*\{\n" % re.escape(name)
    m = re.match(r"_ZNK?(\d+)", symbol)
    if not m:
        return r"^[^\n;#]*\b%s\([^;{]*\{\n" % re.escape(symbol)
    p = m.end(1)
    n = int(m.group(1))
    cls = symbol[p : p + n]
    rest = symbol[p + n :]
    c = re.match(r"(C|D)[0-9]", rest)
    if c:
        name = cls if c.group(1) == "C" else "~" + cls
    else:
        q = re.match(r"(\d+)", rest)
        if not q:
            return None
        name = rest[q.end() : q.end() + int(q.group(1))]
    return r"^[^\n;#]*\b%s::%s\([^;{]*\{\n" % (cls, re.escape(name))


def diff_lines(unit, symbol):
    """Our source lines of the differing instructions (tools/why_diff.py's row kinds)."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import why_diff
    rows, _ = why_diff.load_rows(unit, symbol)
    kinds, _ = why_diff.analyse(rows)
    return {r["line"] for r, k in zip(rows, kinds) if r["line"] and k not in (None, "pool", "branch")}


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OBJDIFF = os.path.join(ROOT, "objdiff-cli.exe" if os.name == "nt" else "objdiff-cli")


def objdiff_score(target, ob, symbol, js):
    """100 minus objdiff's match percent of the function (0: a match), as in the report."""
    if os.path.exists(js):
        os.remove(js)
    r = subprocess.run([OBJDIFF, "diff", "-1", target, "-2", ob, symbol, "-o", js, "--format", "json"],
                       capture_output=True)
    if r.returncode or not os.path.exists(js):
        return None
    with open(js, encoding="utf-8") as f:
        d = json.load(f)
    for s in d.get("right", {}).get("symbols", []):
        if s.get("name") == symbol and s.get("match_percent") is not None:
            return 0 if s["match_percent"] >= 100 else 100 - s["match_percent"]
    return None


class Search:
    """Compiles variants of one function and scores them. With `target` (the delinked
    object), the score is objdiff's (100 minus the match percent, times `scale`);
    without it, the count of differing words (score())."""

    def __init__(self, pre, post, srcdir, symbol, tgt, trel, tmp, workers, target=None, scale=1.0):
        self.pre, self.post, self.srcdir, self.symbol = pre, post, srcdir, symbol
        self.tgt, self.trel, self.tmp = tgt, trel, tmp
        self.target, self.scale = target, scale
        self.ex = ThreadPoolExecutor(workers)
        self.workers = workers

    def run(self, i, lines):
        c = os.path.join(self.tmp, "v%d.cpp" % i)
        ob = os.path.join(self.tmp, "v%d.o" % i)
        with open(c, "w", encoding="utf-8", errors="surrogateescape", newline="\n") as f:
            f.write(self.pre + "\n".join(lines) + "\n" + self.post)
        if os.path.exists(ob):
            os.remove(ob)
        r = subprocess.run([CC] + FLAGS + ["-i", self.srcdir, "-c", c, "-o", ob], capture_output=True)
        if r.returncode or not os.path.exists(ob):
            return 10**6
        if self.target:
            v = objdiff_score(self.target, ob, self.symbol, os.path.join(self.tmp, "v%d.json" % i))
            return 10**6 if v is None else v * self.scale
        return score(ob, self.symbol, self.tgt, self.trel)

    def batch(self, C):
        return list(self.ex.map(self.run, range(len(C)), C))


def cleanup(s, body, L, goal=0):
    """Revert every difference from the original body that a score of `goal` does not need
    (0: the match; the best score for a closest form)."""
    changed = True
    while changed:
        changed = False
        ops = [o for o in difflib.SequenceMatcher(None, body, L, autojunk=False).get_opcodes() if o[0] != "equal"]
        C = [L[: o[3]] + body[o[1] : o[2]] + L[o[4] :] for o in ops]
        for k in range(0, len(C), s.workers):
            part = C[k : k + s.workers]
            R = s.batch(part)
            ok = [x for r, x in zip(R, part) if r <= goal]
            if ok:
                L = min(ok, key=lambda x: sum(a != b for a, b in zip(x, body)))
                changed = True
                break
    return L


def main():
    a = argparse.ArgumentParser()
    a.add_argument("unit")
    a.add_argument("symbol")
    a.add_argument("--seconds", type=int, default=60)
    a.add_argument("--workers", type=int, default=16)
    a.add_argument("--out", default=None)
    a.add_argument("--scorer", choices=("objdiff", "words"), default="objdiff",
                   help="objdiff: the report's match percent (default); words: differing words, faster")
    a.add_argument("--focus", type=int, default=None, metavar="N",
                   help="mutate only lines within N lines of a difference (objdiff line numbers)")
    o = a.parse_args()
    src = o.unit + ".cpp"
    target = os.path.join("build", "jpn", "delinks", o.unit.replace("/", os.sep) + ".o")
    tgt, trel = elf_func(target, o.symbol)
    if tgt is None:
        sys.exit("symbol not in target: " + o.symbol)
    text = open(src, newline="", encoding="utf-8").read().replace("\r\n", "\n")
    rx = locate(o.symbol)
    m = re.search(rx, text, re.M) if rx else None
    if not m:
        sys.exit("definition not found: " + o.symbol)
    s0 = m.end()
    e = text.index("\n}\n", s0) + 1
    pre, body, post = text[:s0], text[s0:e].split("\n")[:-1], text[e:]
    if o.focus is not None:
        global FOCUS
        first = pre.count("\n") + 1  # file line of body[0]
        near = {i for ln in diff_lines(o.unit, o.symbol) for i in range(len(body)) if abs(first + i - ln) <= o.focus}
        if near:
            FOCUS = ({body[i] for i in near}, set(body))
            print("focus:", len(near), "of", len(body), "lines", flush=True)
    with tempfile.TemporaryDirectory(prefix="perm_") as tmp:
        # objdiff's percent scaled to words, so the search thresholds keep their meaning
        target_o = os.path.abspath(target) if o.scorer == "objdiff" else None
        s = Search(pre, post, os.path.dirname(os.path.abspath(src)), o.symbol, tgt, trel, tmp, o.workers,
                   target_o, len(tgt) / 400.0)
        best = cur = start = s.run(0, body)
        print("start", cur, "of", len(tgt) // 4, flush=True)
        bestL = curL = body
        t0 = time.time()
        n = 0
        while time.time() - t0 < o.seconds and best > 0:
            C = []
            for _ in range(o.workers):
                L = curL
                for _ in range(random.choice((1, 1, 2, 3))):
                    L = mutate(L)
                C.append(L)
            R = s.batch(C)
            n += len(C)
            v, L = min(zip(R, C), key=lambda x: x[0])
            if v < 5000 and (v <= cur or random.random() < 0.015):
                cur, curL = v, L
            if v < best:
                best, bestL = v, L
                print(round(time.time() - t0), n, "best", best, flush=True)
            if cur > best + 6:
                cur, curL = best, bestL
        print("done", n, "variants, best", best, flush=True)
        if 0 < best < start and o.out:  # not a match: keep the closest form for the next run
            bestL = cleanup(s, body, bestL, best)
            os.makedirs(o.out, exist_ok=True)
            with open(os.path.join(o.out, o.symbol + ".best.txt"), "w", encoding="utf-8", newline="\n") as f:
                f.write("%s score %s (start %s)\n%s\n%s\n}\n" % (src, round(best, 3), round(start, 3), m.group(0).rstrip("\n"), "\n".join(bestL)))
        if best == 0 and o.out:
            bestL = cleanup(s, body, bestL)
            os.makedirs(o.out, exist_ok=True)
            with open(os.path.join(o.out, o.symbol + ".txt"), "w", encoding="utf-8", newline="\n") as f:
                f.write(src + "\n" + m.group(0).rstrip("\n") + "\n" + "\n".join(bestL) + "\n}\n")
        s.ex.shutdown()
    return 0 if best == 0 else 1


if __name__ == "__main__":
    main()
