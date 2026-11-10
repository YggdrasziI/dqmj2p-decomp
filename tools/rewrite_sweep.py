#!/usr/bin/env python3
"""Try a fixed set of statement rewrites on near-match functions, one at a time.

tools/permute.py searches at random among small mutations (declaration moves, copies,
swaps). Some forms that close register gaps are larger rewrites that it never makes:
a value given its own local or written over a dead variable of the same type, a nested
call split in two statements (or two merged), a pointer local replaced by the expression
it holds, a conditional assignment written as `if` / `else` or as `?:`. There are few
such rewrites per function, so this script compiles every one of them (a beam search of
`--depth` rounds keeps the `--beam` best of each round) instead of sampling.

Variants are compiled in a temporary directory with the unit's other code unchanged and
scored by objdiff-cli against the delinked target, as in the report (`--scorer words`:
permute.py's count of differing words, faster but often wrong on gains). Nothing
in the repository is written. A rewrite is only checked by the compiled bytes: a variant
that scores better but is not a match may change what the code does, so read it before
keeping it, and keep nothing that tools/report_diff.py and ninja do not confirm. The
output form is a compiler fact, not evidence of the original source.

  python tools/rewrite_sweep.py <unit> <symbol> [--depth N] [--beam N] [--workers N]
  python tools/rewrite_sweep.py --report [--min 97] [--max 100] [--limit 10] [--out DIR]

With --report, functions of the objdiff report (build/jpn/report.json, written by ninja)
whose fuzzy match is in [min, max) are swept, largest remaining gap first. Functions
defined inside a class body are not found and are listed as skipped. With --out, the
best variant of each improved function is written there as a unified diff.
"""
import argparse
import difflib
import json
import os
import re
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import permute  # noqa: E402
from permute import Search, elf_func, insert_ok, is_decl, l_has, locate, split_top  # noqa: E402

LOOP = re.compile(r"^\s*(for|while|do)\b")
ASSIGN = re.compile(r"^(\s*)([A-Za-z_]\w*) = (?!=)(.+);\s*$")
PATH = re.compile(r"^&?[A-Za-z_]\w*(?:(?:->|\.)[A-Za-z_]\w*|\[\w+\])*$")
CALL_START = re.compile(r"[A-Za-z_]\w*(?:\[[^\[\]]*\])?(?:(?:->|\.)[A-Za-z_]\w*(?:\[[^\[\]]*\])?)*\(")


def ind(l):
    return len(l) - len(l.lstrip())


def word(name):
    """A use of a variable: not a member access `.name` / `->name`."""
    return re.compile(r"(?<!\.)(?<!->)\b%s\b" % re.escape(name))


def norm(t):
    return t.replace(" ", "")


def block_end(L, i):
    """Index of the line that closes the block line i is in (len(L) for the body)."""
    d = ind(L[i])
    for j in range(i + 1, len(L)):
        if L[j].strip() and ind(L[j]) < d:
            return j
    return len(L)


def loops_around(L, i):
    """(start, end) line ranges of the loops whose body holds line i."""
    out = []
    for k in range(i):
        if LOOP.match(L[k]) and ind(L[k]) < ind(L[i]):
            e = block_end(L, k + 1) if k + 1 < len(L) else len(L)
            if k < i < e or (L[k].lstrip().startswith("do") and k < i <= e):
                out.append((k, e + 1))
    return out


def params(sig):
    """[(name, type text)] of a definition line."""
    a = sig.index("(")
    depth, b = 0, a
    for b in range(a, len(sig)):
        if sig[b] == "(":
            depth += 1
        elif sig[b] == ")":
            depth -= 1
            if depth == 0:
                break
    out = []
    parts, _ = split_top(sig[a + 1 : b], (",",))
    for _, p in parts:
        p = p.split("=")[0].strip()
        m = re.match(r"^(.*?[\s*&])([A-Za-z_]\w*)$", p)
        if m and p not in ("void", "..."):
            out.append((m.group(2), m.group(1)))
    return out


def variables(L, sig):
    """[(name, type text, declaration line or -1 for a parameter)]."""
    v = [(n, t, -1) for n, t in params(sig)]
    for i, l in enumerate(L):
        m = is_decl(l)
        if m:
            v.append((m.group(3), m.group(2), i))
    return v


def in_scope(L, d, i):
    """The local declared on line d (-1: a parameter) is visible on line i."""
    if d < 0:
        return True
    if d >= i:
        return False
    return all(ind(L[k]) >= ind(L[d]) for k in range(d + 1, i) if L[k].strip())


def used(L, name, a, b):
    w = word(name)
    return any(w.search(L[k]) for k in range(a, min(b, len(L))))


def rename(L, name, new, a, b):
    w = word(name)
    return [w.sub(new, l) if a <= k < b else l for k, l in enumerate(L)]


def dead_after(L, y, x, i):
    """y holds no value needed after line i: it is not used again, or its next use is a
    plain write `y = e;` (e may read x, not y) that every path after line i reaches, after
    which x is not used."""
    w = word(y)
    for k in range(i + 1, len(L)):
        if not w.search(L[k]):
            continue
        m = ASSIGN.match(L[k])
        # Reached by every path from line i: no line in between closes the write's block
        # (an `} else {` of another branch is less indented than a write inside it).
        reached = all(ind(L[j]) >= ind(L[k]) for j in range(i + 1, k) if L[j].strip())
        return (m is not None and m.group(2) == y and not w.search(m.group(3)) and ind(L[k]) <= ind(L[i])
                and reached and not loops_around(L, k) and not used(L, x, k + 1, len(L)))
    return True


def reuse_var(L, V):
    """`T x = e;` -> `y = e;` with x renamed y, for a y of type T holding a dead value."""
    out = []
    for i, l in enumerate(L):
        m = is_decl(l)
        if not m or not m.group(4):
            continue
        x, t, e = m.group(3), m.group(2), m.group(5)
        end = block_end(L, i)
        loops = loops_around(L, i)
        for y, ty, d in V:
            if y == x or norm(ty) != norm(t) or not in_scope(L, d, i) or not dead_after(L, y, x, i):
                continue
            if any(used(L, y, a, b) for a, b in loops) or re.search(r"&\s*%s\b" % re.escape(y), "\n".join(L)):
                continue
            R = rename(L, x, y, i + 1, end)
            R[i] = "%s%s = %s;" % (m.group(1), y, e)
            out.append(("reuse `%s` for `%s` (line %d)" % (y, x, i), R))
    return out


def split_var(L, V):
    """`y = e;` -> `T y_ = e;` with the later uses of y renamed, outside loops."""
    out = []
    types = {}
    for n, t, d in V:
        types.setdefault(n, (t, d))
    for i, l in enumerate(L):
        m = ASSIGN.match(l)
        if not m or m.group(2) not in types or loops_around(L, i):
            continue
        y = m.group(2)
        t, d = types[y]
        if not in_scope(L, d, i) or re.search(r"&\s*%s\b" % re.escape(y), "\n".join(L)):
            continue
        end = len(L) if ind(l) <= BASE else block_end(L, i)
        if used(L, y, end, len(L)):
            continue
        n = y + "_"
        while l_has(L, n):
            n += "_"
        R = rename(L, y, n, i + 1, end)
        R[i] = "%s%s%s = %s;" % (m.group(1), t if t.endswith(("*", "&", " ")) else t + " ", n, m.group(3))
        out.append(("own local for `%s` (line %d)" % (y, i), R))
    return out


def calls(s):
    """[(start, open paren, close paren)] of the call expressions of s."""
    out = []
    for m in CALL_START.finditer(s):
        if m.start() and (s[m.start() - 1].isalnum() or s[m.start() - 1] in "_.>"):
            continue
        a = m.end() - 1
        depth = 0
        for b in range(a, len(s)):
            if s[b] == "(":
                depth += 1
            elif s[b] == ")":
                depth -= 1
                if depth == 0:
                    out.append((m.start(), a, b))
                    break
    return out


def split_nested(L, V):
    """`v = f(g(...));` -> `v = g(...); v = f(v);`, or g's result in a new local."""
    out = []
    for i, l in enumerate(L):
        if not l.rstrip().endswith(";") or re.match(r"^\s*(return|case|default)\b", l):
            continue
        C = calls(l)
        for s, a, b in C:
            # The inner call is the outer call's whole argument list: `f(g(...))`.
            inner = [c for c in C if c[0] > a and l[a + 1 : c[0]].strip() == "" and l[c[2] + 1 : b].strip() == ""]
            if not inner:
                continue
            gs, ge = inner[0][0], inner[0][2] + 1
            g = l[gs:ge]
            m = ASSIGN.match(l)
            if m and gs > l.index("="):
                v = m.group(2)
                rest = l[l.index("=") + 1 : gs] + l[ge:]  # the statement without g and the lhs
                if not word(v).search(rest):
                    R = list(L)
                    R[i : i + 1] = ["%s%s = %s;" % (m.group(1), v, g), l[:gs] + v + l[ge:]]
                    out.append(("split nested call into `%s` (line %d)" % (v, i), R))
            if insert_ok(L, i):
                n = "n%d_" % i
                while l_has(L, n):
                    n += "_"
                R = list(L)
                R[i : i + 1] = ["%s__typeof__(%s) %s = %s;" % (" " * ind(l), g, n, g), l[:gs] + n + l[ge:]]
                out.append(("nested call in a local (line %d)" % i, R))
    for i in range(len(L) - 1):
        m1, m2 = ASSIGN.match(L[i]), ASSIGN.match(L[i + 1])
        if not m1 or not m2 or m1.group(2) != m2.group(2) or m1.group(1) != m2.group(1):
            continue
        v = m1.group(2)
        w = word(v)
        if len(w.findall(m2.group(3))) != 1:
            continue
        e = m1.group(3)
        if not (PATH.match(e) or calls(e) and calls(e)[0][0] == 0 and calls(e)[0][2] == len(e) - 1):
            e = "(" + e + ")"
        R = list(L)
        R[i : i + 2] = ["%s%s = %s;" % (m1.group(1), v, w.sub(lambda _: e, m2.group(3)))]
        out.append(("merge two writes of `%s` into a nested call (line %d)" % (v, i), R))
    return out


def inline_ptr(L, V):
    """`T *p = E;` with E a plain path (`owner`, `a->b`, `&a[i]`): use E instead of p."""
    out = []
    for i, l in enumerate(L):
        m = is_decl(l)
        if not m or not m.group(4) or "*" not in m.group(2):
            continue
        p, e = m.group(3), m.group(5).strip()
        if not PATH.match(e):
            continue
        end = block_end(L, i)
        if any(permute.assigns(L[k], p) for k in range(i + 1, end)) or re.search(r"&\s*%s\b" % re.escape(p), "\n".join(L)):
            continue
        names = re.findall(r"(?<![.>\w])([A-Za-z_]\w*)", e.lstrip("&"))
        if any(permute.assigns(L[k], n) for n in names for k in range(i + 1, end)):
            continue
        w = re.escape(p)
        if e.startswith("&"):
            base = e[1:]
            subs = [(r"(?<!\.)(?<!->)\b%s->" % w, base + "."), (r"(?<!\.)(?<!->)\b%s\b" % w, e)]
        else:
            subs = [(r"(?<!\.)(?<!->)\b%s\b" % w, e)]
        R = list(L)
        for k in range(i + 1, end):
            for a, b in subs:
                R[k] = re.sub(a, lambda _: b, R[k])
        del R[i]
        out.append(("inline pointer `%s` (line %d)" % (p, i), R))
    return out


def ternary(s):
    """(cond, a, b) when s is one top-level `c ? a : b`, else None."""
    depth, q = 0, None
    for k, ch in enumerate(s):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif depth == 0 and ch == "?":
            if q is not None:
                return None
            q = k
        elif depth == 0 and ch == ":" and q is not None and s[k - 1] != ":" and s[k + 1 : k + 2] != ":":
            return s[:q].strip(), s[q + 1 : k].strip(), s[k + 1 :].strip()
    return None


TRUE, FALSE = ("true", "1"), ("false", "0")


def is_boolean(c):
    """c is 0 or 1: a comparison, `&&` / `||` or `!` at its top level (not `->`, `<<`, `>>`)."""
    if c.startswith("!") and not c.startswith("!="):
        return True
    _, ops = split_top(c, ("==", "!=", "<=", ">=", "&&", "||", "<", ">"))
    return bool(ops)


def cond_forms(L, V):
    """Conditional assignments: `if` / `else` <-> `?:`, and `x = c;` for true / false."""
    out = []
    types = {n: t for n, t, _ in V}
    for i in range(len(L) - 4):
        m = re.match(r"^(\s*)if \((.*)\) \{$", L[i])
        if not m:
            continue
        s = m.group(1)
        a = re.match(r"^%s    ([A-Za-z_]\w*) = (.+);$" % s, L[i + 1])
        if not a or L[i + 2] != s + "} else {" or L[i + 4] != s + "}":
            continue
        b = re.match(r"^%s    %s = (.+);$" % (s, re.escape(a.group(1))), L[i + 3])
        if not b:
            continue
        x, c, va, vb = a.group(1), m.group(2), a.group(2), b.group(1)
        rhs = ["%s ? %s : %s" % (c, va, vb)]
        if va in TRUE and vb in FALSE and (norm(types.get(x, "")) == "bool" or is_boolean(c)):
            rhs.append(c)
        if va in FALSE and vb in TRUE and norm(types.get(x, "")) == "bool":
            rhs.append("!(%s)" % c)
        for r in rhs:
            R = list(L)
            R[i : i + 5] = ["%s%s = %s;" % (s, x, r)]
            out.append(("if / else as `%s = %s` (line %d)" % (x, r, i), R))
            d = is_decl(L[i - 1]) if i else None
            if d and d.group(3) == x and not d.group(4) and d.group(1) == s:
                R = list(L)
                R[i - 1 : i + 5] = ["%s%s%s = %s;" % (s, d.group(2), x, r)]
                out.append(("if / else as the initializer of `%s` (line %d)" % (x, i), R))
    for i, l in enumerate(L):
        d = is_decl(l)
        m = ASSIGN.match(l)
        if d and d.group(4):
            s, x, e, t = d.group(1), d.group(3), d.group(5), d.group(2)
        elif m:
            s, x, e, t = m.group(1), m.group(2), m.group(3), None
        else:
            continue
        tr = ternary(e)
        if not tr:
            continue
        c, va, vb = tr
        body = [s + "if (%s) {" % c, s + "    %s = %s;" % (x, va), s + "} else {", s + "    %s = %s;" % (x, vb), s + "}"]
        R = list(L)
        R[i : i + 1] = ([s + t + x + ";"] if t else []) + body
        out.append(("`?:` as if / else (line %d)" % i, R))
    return out


def block_close(L, i):
    """Index of the `}` line closing the block opened on line i (same indentation)."""
    for j in range(i + 1, len(L)):
        if L[j].strip() and ind(L[j]) <= ind(L[i]):
            return j if L[j].strip().startswith("}") else None
    return None


def wrapped(c):
    """c is `(...)` with the first parenthesis closing at its end."""
    if not (c.startswith("(") and c.endswith(")")):
        return False
    depth = 0
    for k, ch in enumerate(c):
        depth += ch == "("
        depth -= ch == ")"
        if depth == 0:
            return k == len(c) - 1
    return False


def negate(c):
    """The negation of a condition, written the simple way when there is one."""
    c = c.strip()
    if c.startswith("!") and not c.startswith("!=") and wrapped(c[1:]):
        return c[2:-1]
    if re.match(r"^![A-Za-z_][\w.\->\[\]]*$", c):
        return c[1:]
    parts, ops = split_top(c, ("==", "!=", "<=", ">=", "&&", "||", "<", ">"))
    if ops in (["=="], ["!="]):
        return "%s %s %s" % (parts[0][1].strip(), "!=" if ops[0] == "==" else "==", parts[1][1].strip())
    return "!(%s)" % c


def branches(L, V):
    """`if (c) {A} else {B}` -> `if (!c) {B} else {A}`; `if (a && b) {A}` <-> nested ifs."""
    out = []
    for i, l in enumerate(L):
        m = re.match(r"^(\s*)if \((.*)\) \{$", l)
        if not m:
            continue
        s, c = m.group(1), m.group(2)
        j = block_close(L, i)
        if j is None:
            continue
        if L[j] == s + "} else {":
            k = block_close(L, j)
            if k is not None and L[k] == s + "}":
                R = L[:i] + [s + "if (%s) {" % negate(c)] + L[j + 1 : k] + [s + "} else {"] + L[i + 1 : j] + L[k:]
                out.append(("if / else inverted (line %d)" % i, R))
            continue
        if L[j] != s + "}":
            continue
        parts, ops = split_top(c, ("&&", "||"))
        if ops and all(o == "&&" for o in ops):
            a, b = parts[0][1].strip(), c[parts[1][0]:].strip()
            inner = ["    " + x if x.strip() else x for x in L[i + 1 : j]]
            R = L[:i] + [s + "if (%s) {" % a, s + "    if (%s) {" % b] + inner + [s + "    }", s + "}"] + L[j + 1 :]
            out.append(("`&&` as nested ifs (line %d)" % i, R))
        n = re.match(r"^%s    if \((.*)\) \{$" % re.escape(s), L[i + 1]) if i + 1 < j else None
        if n and block_close(L, i + 1) == j - 1 and L[j - 1] == s + "    }":
            b = n.group(1)
            if split_top(b, ("||",))[1]:
                b = "(%s)" % b
            a = c if not split_top(c, ("||",))[1] else "(%s)" % c
            inner = [x[4:] if x.startswith(s + "    " + "    ") else x for x in L[i + 2 : j - 1]]
            R = L[:i] + [s + "if (%s && %s) {" % (a, b)] + inner + [s + "}"] + L[j + 1 :]
            out.append(("nested ifs as `&&` (line %d)" % i, R))
    return out


def loop_steps(L, V):
    """`for (...; ...; a, b)` -> `for (...; ...; b, a)`."""
    out = []
    for i, l in enumerate(L):
        m = re.match(r"^(\s*for \(.*;.*; )(.*)(\) \{)$", l)
        if not m:
            continue
        parts, ops = split_top(m.group(2), (",",))
        if len(parts) == 2:
            R = list(L)
            R[i] = m.group(1) + "%s, %s" % (parts[1][1].strip(), parts[0][1].strip()) + m.group(3)
            out.append(("for steps swapped (line %d)" % i, R))
    return out


REWRITES = {
    "branch": branches,
    "steps": loop_steps,
    "reuse": reuse_var,
    "split": split_var,
    "nested": split_nested,
    "pointer": inline_ptr,
    "cond": cond_forms,
}


def candidates(L, sig, kinds):
    """The rewrites of L, taken in turn from each kind so that a cut keeps every kind."""
    V = variables(L, sig)
    lists = [REWRITES[k](L, V) for k in kinds]
    for k in range(max((len(x) for x in lists), default=0)):
        for x in lists:
            if k < len(x) and x[k][1] != L:
                yield x[k]


def nested_name(symbol):
    """(classes, function name) of a mangled member function, or None."""
    m = re.match(r"_ZNK?", symbol)
    if not m:
        return None
    p, names = m.end(), []
    while True:
        q = re.match(r"\d+", symbol[p:])
        if q:
            n = int(q.group(0))
            names.append(symbol[p + q.end() : p + q.end() + n])
            p += q.end() + n
            continue
        c = re.match(r"[CD]\d", symbol[p:])
        if c and names:
            return names, ("~" if c.group(0)[0] == "D" else "") + names[-1]
        return (names[:-1], names[-1]) if len(names) > 1 else None


def find_definition(text, symbol):
    """Match object of the definition line (through its `{` and newline)."""
    rx = locate(symbol)
    m = re.search(rx, text, re.M) if rx else None
    if m:
        return m
    nn = nested_name(symbol)
    if not nn:
        return None
    classes, name = nn
    c = re.search(r"^\s*(?:class|struct) %s\b[^;{]*\{" % re.escape(classes[-1]), text, re.M)
    if not c:
        return None
    end = body_end(text, c.end()) or len(text)
    rx = re.compile(r"^[ \t]+(?:(?:virtual|static|inline)\s+)*[^;\n(]*?(?<![\w~])%s\([^;{]*\)\s*(?:const\s*)?\{\n" % re.escape(name), re.M)
    return rx.search(text, c.end(), end)


def body_end(text, s0):
    """Start of the line holding the `}` that closes the block opened just before s0."""
    depth, k = 1, s0
    while k < len(text):
        ch = text[k]
        if text.startswith("//", k):
            k = text.find("\n", k)
            if k < 0:
                return None
            continue
        if text.startswith("/*", k):
            k = text.find("*/", k) + 2
            if k < 2:
                return None
            continue
        if ch in "\"'":
            j = k + 1
            while j < len(text) and text[j] != ch:
                j += 2 if text[j] == "\\" else 1
            k = j + 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                if text[text.rfind("\n", 0, k) + 1 : k].strip():
                    return None  # the brace shares a line with code: not line-based
                return text.rfind("\n", 0, k) + 1
        k += 1
    return None


BASE = 4  # indentation of the function's top-level statements


def sweep(unit, symbol, o):
    src = unit + ".cpp"
    if not unit.startswith("src/"):
        return "skipped: not a game unit (built with other compiler flags)"
    if not os.path.exists(src):
        return "skipped: no C++ source"
    target = os.path.join("build", "jpn", "delinks", unit.replace("/", os.sep) + ".o")
    tgt, trel = elf_func(target, symbol)
    if tgt is None:
        return "skipped: symbol not in target"
    text = open(src, newline="", encoding="utf-8", errors="surrogateescape").read().replace("\r\n", "\n")
    m = find_definition(text, symbol)
    if not m:
        return "skipped: definition not found"
    s0 = m.end()
    e = body_end(text, s0)
    if e is None:
        return "skipped: unbalanced braces"
    pre, body, post = text[:s0], text[s0:e].split("\n")[:-1], text[e:]
    sig = m.group(0)
    global BASE
    BASE = min((ind(l) for l in body if l.strip()), default=4)
    with tempfile.TemporaryDirectory(prefix="sweep_") as tmp:
        args = (pre, post, os.path.dirname(os.path.abspath(src)), symbol, tgt, trel, tmp, o.workers)
        s = Search(*args, target=os.path.abspath(target)) if o.scorer == "objdiff" else Search(*args)
        start = s.run(0, body)
        if start >= 10**6:
            s.ex.shutdown()
            return "skipped: the current source does not compile"
        beam = [(start, [], body)]
        best = beam[0]
        n = 0
        done = {tuple(body)}
        for _ in range(o.depth):
            per = [[(path + [desc], R) for desc, R in candidates(L, sig, o.kinds)] for _, path, L in beam]
            C, queued = [], set()
            for k in range(max(len(x) for x in per)):  # in turn from each beam entry
                for x in per:
                    if k < len(x) and tuple(x[k][1]) not in done and tuple(x[k][1]) not in queued:
                        queued.add(tuple(x[k][1]))
                        C.append(x[k])
            C = C[: o.max_candidates]
            done.update(tuple(R) for _, R in C)  # only what is compiled
            if not C:
                break
            scored = []
            for k in range(0, len(C), o.workers):
                part = C[k : k + o.workers]
                for (path, R), v in zip(part, s.batch([R for _, R in part])):
                    scored.append((v, path, R))
                    if v == 0:
                        break
                if any(v == 0 for v, _, _ in scored):
                    break
            n += len(scored)
            scored.sort(key=lambda x: x[0])
            if scored and scored[0][0] < best[0]:
                best = scored[0]
            if best[0] == 0:
                break
            beam = [x for x in scored[: o.beam] if x[0] < start]
            if not beam:
                break
        s.ex.shutdown()
    v, path, L = best
    if o.scorer == "objdiff":
        msg = "%.2f %% -> %.2f %%, %d variants" % (100 - start, 100 - v, n)
    else:
        msg = "start %d, best %d of %d words, %d variants" % (start, v, len(tgt) // 4, n)
    if v < start:
        msg += (" MATCH" if v == 0 else "") + "\n      " + "\n      ".join(path)
        if o.out:
            os.makedirs(o.out, exist_ok=True)
            first = pre.count("\n") + 1
            diff = difflib.unified_diff(body, L, "a/" + src, "b/" + src, lineterm="", n=2)
            shift = lambda q: "%s%d" % (q.group(1), int(q.group(2)) + first - 1)  # noqa: E731
            diff = [re.sub(r"([-+])(\d+)", shift, d, count=2) if d.startswith("@@") else d for d in diff]
            with open(os.path.join(o.out, symbol + ".diff"), "w", encoding="utf-8", newline="\n") as f:
                f.write("score %s (start %s): %s\n" % (round(v, 3), round(start, 3), "; ".join(path)) + "\n".join(diff) + "\n")
    return msg


def from_report(o):
    r = json.load(open(os.path.join("build", "jpn", "report.json")))
    out = []
    for u in r["units"]:
        if not u["name"].startswith("src/"):
            continue
        for f in u.get("functions", []):
            p = f.get("fuzzy_match_percent", 0)
            if o.min <= p < o.max:
                out.append((int(f["size"]) * (100 - p), u["name"], f["name"]))
    out.sort(reverse=True)
    return [(u, f) for _, u, f in out[: o.limit]]


def main():
    a = argparse.ArgumentParser()
    a.add_argument("unit", nargs="?")
    a.add_argument("symbol", nargs="?")
    a.add_argument("--report", action="store_true", help="sweep functions of build/jpn/report.json")
    a.add_argument("--min", type=float, default=97.0)
    a.add_argument("--max", type=float, default=100.0)
    a.add_argument("--limit", type=int, default=10)
    a.add_argument("--depth", type=int, default=2)
    a.add_argument("--beam", type=int, default=2)
    a.add_argument("--max-candidates", type=int, default=120, help="per round and function")
    a.add_argument("--workers", type=int, default=2)
    a.add_argument("--scorer", choices=("objdiff", "words"), default="objdiff",
                   help="objdiff: the match percent of the report (default); words: permute.py's score")
    a.add_argument("--kinds", default=",".join(REWRITES), help="comma-separated: " + ", ".join(REWRITES))
    a.add_argument("--out", default=None)
    o = a.parse_args()
    o.kinds = [k for k in o.kinds.split(",") if k]
    bad = [k for k in o.kinds if k not in REWRITES]
    if bad:
        sys.exit("unknown rewrite kinds: " + ", ".join(bad))
    if o.report:
        todo = from_report(o)
    elif o.unit and o.symbol:
        todo = [(o.unit, o.symbol)]
    else:
        a.error("give <unit> <symbol> or --report")
    for unit, symbol in todo:
        t0 = time.time()
        try:
            msg = sweep(unit, symbol, o)
        except Exception as e:  # one function's failure does not end a --report run
            msg = "skipped: %s: %s" % (type(e).__name__, e)
        print("%s %s: %s (%d s)" % (unit, symbol, msg, time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
