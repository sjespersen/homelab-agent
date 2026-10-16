"""Coding tasks for coding_bench.py: a prompt, hidden tests the answer must pass, and a
reference solution (only used by `coding_bench.py --self-check` to prove the tests are right).

The tasks are written for this bench, not taken from public sets, so models can't have
memorized them. Each asks for one Python function or class; the tests are plain asserts.
"""

TASKS = []


def task(id, prompt, tests, reference):
    TASKS.append({"id": id, "prompt": prompt.strip(), "tests": tests.strip(), "reference": reference.strip()})


task("merge_intervals", """
Write a Python function `merge(intervals)` that takes a list of half-open intervals `[start, end)`
as (int, int) tuples, in any order, and returns the merged list sorted by start, as tuples.
Intervals that overlap or touch (one ends where the next starts) merge into one. Empty intervals
(start == end) are dropped, unless they lie inside or at the edge of another interval, in which
case they vanish into it. Raise ValueError if any interval has start > end.
""", """
assert merge([]) == []
assert merge([(1, 3), (2, 5)]) == [(1, 5)]
assert merge([(5, 7), (1, 3), (3, 4)]) == [(1, 4), (5, 7)]
assert merge([(1, 1)]) == []
assert merge([(1, 1), (2, 3)]) == [(2, 3)]
assert merge([(0, 10), (2, 2), (10, 10)]) == [(0, 10)]
assert merge([(-5, -1), (-1, 0), (4, 6), (5, 5)]) == [(-5, 0), (4, 6)]
assert merge([(1, 2), (1, 2), (1, 2)]) == [(1, 2)]
try:
    merge([(3, 1)]); raise AssertionError("no ValueError")
except ValueError:
    pass
""", """
def merge(intervals):
    for s, e in intervals:
        if s > e:
            raise ValueError
    out = []
    for s, e in sorted(i for i in intervals if i[0] < i[1]):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out
""")

task("calc", """
Write a Python function `calc(expr)` that evaluates an arithmetic expression string with integers,
`+ - * /`, parentheses, unary minus (also repeated, like `--3`) and arbitrary spaces, and returns
an int. `*` and `/` bind tighter than `+` and `-`; all four are left-associative. `/` is integer
division that truncates toward zero (so -7/2 == -3). Unary minus binds tighter than `*` and `/`.
Don't use eval, exec or compile. Raise ZeroDivisionError on division by zero and ValueError on
malformed input (e.g. "", "1 +", "(1", "1 2", "2*/3").
""", """
assert calc("1 + 2 * 3") == 7
assert calc("(1 + 2) * 3") == 9
assert calc("10 - 4 - 3") == 3
assert calc("100 / 10 / 5") == 2
assert calc("-7 / 2") == -3
assert calc("7 / -2") == -3
assert calc("--3") == 3
assert calc("-(2 + 3) * 2") == -10
assert calc("  42 ") == 42
assert calc("2*-3") == -6
assert calc("((((5))))") == 5
assert calc("1 - -1") == 2
assert calc("123456789 * 1000") == 123456789000
for bad in ["", "1 +", "(1", "1 2", "2*/3", "()", "1)", "+"]:
    try:
        calc(bad); raise AssertionError("no ValueError for " + repr(bad))
    except ValueError:
        pass
try:
    calc("1 / (2 - 2)"); raise AssertionError("no ZeroDivisionError")
except ZeroDivisionError:
    pass
""", """
def calc(expr):
    import re
    toks = re.findall(r"\\d+|[-+*/()]|\\S", expr)
    pos = 0
    def peek():
        return toks[pos] if pos < len(toks) else None
    def take():
        nonlocal pos
        pos += 1
        return toks[pos - 1]
    def atom():
        t = peek()
        if t == "-":
            take(); return -atom()
        if t == "(":
            take(); v = add()
            if peek() != ")":
                raise ValueError
            take(); return v
        if t is not None and t.isdigit():
            return int(take())
        raise ValueError
    def mul():
        v = atom()
        while peek() in ("*", "/"):
            op = take(); r = atom()
            if op == "*":
                v *= r
            else:
                if r == 0:
                    raise ZeroDivisionError
                q = abs(v) // abs(r)
                v = q if (v >= 0) == (r >= 0) else -q
        return v
    def add():
        v = mul()
        while peek() in ("+", "-"):
            op = take(); r = mul()
            v = v + r if op == "+" else v - r
        return v
    v = add()
    if pos != len(toks):
        raise ValueError
    return v
""")

task("semver", """
Write a Python function `compare(a, b)` that compares two Semantic Versioning 2.0.0 strings and
returns -1, 0 or 1. Follow the spec's precedence rules: compare major, minor, patch numerically;
a version with a pre-release (after `-`) is lower than the same version without one; pre-release
identifiers separated by dots compare one by one: numeric ones numerically, alphanumeric ones
lexically in ASCII order, numeric ones are lower than alphanumeric ones, and a shorter list is
lower if all earlier identifiers are equal. Build metadata (after `+`) is ignored. Raise
ValueError for invalid versions (missing parts, leading zeros in numbers like "01", empty
identifiers, non-numeric major/minor/patch).
""", """
assert compare("1.0.0", "1.0.0") == 0
assert compare("1.0.0", "2.0.0") == -1
assert compare("2.1.0", "2.0.9") == 1
assert compare("1.10.0", "1.9.0") == 1
assert compare("1.0.0-alpha", "1.0.0") == -1
assert compare("1.0.0+build.5", "1.0.0+other") == 0
chain = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta", "1.0.0-beta.2",
         "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0"]
for x, y in zip(chain, chain[1:]):
    assert compare(x, y) == -1, (x, y)
    assert compare(y, x) == 1, (y, x)
assert compare("1.0.0-1", "1.0.0-a") == -1
assert compare("1.0.0-Z", "1.0.0-a") == -1
assert compare("1.0.0-alpha+x", "1.0.0-alpha") == 0
for bad in ["1.0", "1.0.0.0", "01.0.0", "1.0.0-", "1.0.0-a..b", "a.b.c", "1.0.0-01", ""]:
    try:
        compare(bad, "1.0.0"); raise AssertionError("no ValueError for " + repr(bad))
    except ValueError:
        pass
""", """
def compare(a, b):
    import re
    def parse(v):
        v = v.split("+", 1)[0]
        core, dash, pre = v.partition("-")
        parts = core.split(".")
        if len(parts) != 3 or not all(re.fullmatch(r"0|[1-9]\\d*", p) for p in parts):
            raise ValueError
        ids = pre.split(".") if dash else []
        for i in ids:
            if not re.fullmatch(r"[0-9A-Za-z-]+", i) or (i.isdigit() and len(i) > 1 and i[0] == "0"):
                raise ValueError
        return [int(p) for p in parts], ids
    (ca, pa), (cb, pb) = parse(a), parse(b)
    if ca != cb:
        return -1 if ca < cb else 1
    if not pa or not pb:
        return (not pa) - (not pb) if pa or pb else 0
    for x, y in zip(pa, pb):
        if x == y:
            continue
        if x.isdigit() and y.isdigit():
            return -1 if int(x) < int(y) else 1
        if x.isdigit() != y.isdigit():
            return -1 if x.isdigit() else 1
        return -1 if x < y else 1
    return (len(pa) > len(pb)) - (len(pa) < len(pb))
""")

task("justify", """
Write a Python function `justify(text, width)` that fully justifies text: split `text` on any
whitespace into words, fill lines greedily (as many words as fit, separated by at least one
space), and return the list of lines. Every line except the last is exactly `width` characters:
spread the extra spaces between words as evenly as possible, giving the leftmost gaps one more
space when they don't divide evenly. A line with a single word is padded with spaces on the
right. The last line is left-justified (single spaces) and padded with spaces to `width`. A word
longer than `width` goes on its own line, unpadded and unsplit. Empty text gives [].
""", """
assert justify("", 10) == []
assert justify("   ", 10) == []
assert justify("This is an example of text justification.", 16) == [
    "This    is    an", "example  of text", "justification.  "]
assert justify("What must be acknowledgment shall be", 16) == [
    "What   must   be", "acknowledgment  ", "shall be        "]
assert justify("a b c d e", 3) == ["a b", "c d", "e  "]
assert justify("supercalifragilistic is long", 10) == [
    "supercalifragilistic", "is long   "]
assert justify("supercalifragilistic is long ok", 10) == [
    "supercalifragilistic", "is long ok"]
assert justify("ab supercalifragilistic cd ef", 6) == [
    "ab    ", "supercalifragilistic", "cd ef "]
assert justify("one", 5) == ["one  "]
assert justify("aa bb cc dd", 7) == ["aa   bb", "cc dd  "]
assert justify("x  y\\n z\\tw", 5) == ["x y z", "w    "]
assert justify("x  y\\n zz\\tw", 5) == ["x   y", "zz w "]
assert justify("a bb ccc dddd", 9) == ["a  bb ccc", "dddd     "]
""", """
def justify(text, width):
    words = text.split()
    lines, cur = [], []
    for w in words:
        if cur and len(" ".join(cur + [w])) > width:
            lines.append(cur); cur = []
        cur.append(w)
    if cur:
        lines.append(cur)
    out = []
    for i, line in enumerate(lines):
        if i == len(lines) - 1 or len(line) == 1:
            s = " ".join(line)
            out.append(s + " " * max(0, width - len(s)))
            continue
        gaps = len(line) - 1
        spaces = width - sum(map(len, line))
        q, r = divmod(spaces, gaps)
        s = ""
        for j, w in enumerate(line[:-1]):
            s += w + " " * (q + (1 if j < r else 0))
        out.append(s + line[-1])
    return out
""")

task("toposort", """
Write a Python function `order(tasks, deps)` where `tasks` is a list of unique task names (strings)
and `deps` is a list of (before, after) pairs meaning `before` must come before `after`. Return a
list of all tasks in an order that respects every dependency; when several tasks could come next,
pick the one that comes first alphabetically. Duplicate pairs are allowed. Raise ValueError if
the dependencies contain a cycle (including a task depending on itself) or name a task that isn't
in `tasks`.
""", """
assert order([], []) == []
assert order(["b", "a", "c"], []) == ["a", "b", "c"]
assert order(["a", "b", "c"], [("c", "a")]) == ["b", "c", "a"]
assert order(["x", "y", "z", "w"], [("z", "y"), ("w", "y"), ("y", "x"), ("z", "y")]) == ["w", "z", "y", "x"]
assert order(["d", "c", "b", "a"], [("d", "a"), ("c", "a"), ("b", "a")]) == ["b", "c", "d", "a"]
assert order(["m", "n"], [("n", "m")]) == ["n", "m"]
for tasks, deps in [(["a", "b"], [("a", "b"), ("b", "a")]), (["a"], [("a", "a")]),
                    (["a", "b", "c"], [("a", "b"), ("b", "c"), ("c", "a")]), (["a"], [("a", "q")])]:
    try:
        order(tasks, deps); raise AssertionError("no ValueError for " + repr(deps))
    except ValueError:
        pass
big = [f"t{i:03d}" for i in range(300)]
chain = [(big[i + 1], big[i]) for i in range(299)]
assert order(big, chain) == big[::-1]
""", """
def order(tasks, deps):
    import heapq
    names = set(tasks)
    indeg = {t: 0 for t in tasks}
    out = {t: set() for t in tasks}
    for a, b in set(deps):
        if a not in names or b not in names or a == b:
            raise ValueError
        if b not in out[a]:
            out[a].add(b); indeg[b] += 1
    heap = [t for t in tasks if indeg[t] == 0]
    heapq.heapify(heap)
    res = []
    while heap:
        t = heapq.heappop(heap); res.append(t)
        for n in out[t]:
            indeg[n] -= 1
            if indeg[n] == 0:
                heapq.heappush(heap, n)
    if len(res) != len(tasks):
        raise ValueError
    return res
""")

task("csv_line", """
Write a Python function `parse_line(line, sep=",")` that splits one CSV line into a list of field
strings, without using the csv module. Fields may be quoted with double quotes; inside a quoted
field, the separator is literal and `""` stands for one `"`. Unquoted fields are taken as they are
(spaces kept; a `"` in the middle of an unquoted field is literal). An empty line is one empty
field. Raise ValueError for a quoted field that isn't closed, or for characters between a closing
quote and the next separator.
""", '''
assert parse_line("") == [""]
assert parse_line("a,b,c") == ["a", "b", "c"]
assert parse_line("a,,c,") == ["a", "", "c", ""]
assert parse_line('"a,b",c') == ["a,b", "c"]
assert parse_line('"say ""hi""",x') == ['say "hi"', "x"]
assert parse_line('""') == [""]
assert parse_line('"",""') == ["", ""]
assert parse_line(" a , b ") == [" a ", " b "]
assert parse_line('ab"c,d') == ['ab"c', "d"]
assert parse_line("a;b,c;d", sep=";") == ["a", "b,c", "d"]
assert parse_line('"x;y";z', sep=";") == ["x;y", "z"]
for bad in ['"abc', '"a"b,c', '"a""', 'x,"y']:
    try:
        parse_line(bad); raise AssertionError("no ValueError for " + repr(bad))
    except ValueError:
        pass
''', """
def parse_line(line, sep=","):
    fields, i, n = [], 0, len(line)
    while True:
        if i < n and line[i] == '"':
            i += 1; buf = []
            while True:
                if i >= n:
                    raise ValueError
                if line[i] == '"':
                    if i + 1 < n and line[i + 1] == '"':
                        buf.append('"'); i += 2; continue
                    i += 1; break
                buf.append(line[i]); i += 1
            if i < n and line[i] != sep:
                raise ValueError
            fields.append("".join(buf))
        else:
            j = line.find(sep, i)
            j = n if j < 0 else j
            fields.append(line[i:j]); i = j
        if i >= n:
            return fields
        i += 1
""")

task("islands", """
Write a Python function `count_islands(rows, cols, cells)` for a grid that starts as all water.
`cells` is a list of (row, col) positions that become land one after another. After each one,
record how many islands there are (an island is a group of land cells connected up, down, left
or right). Return the list of counts. Adding a cell that is already land changes nothing (its
count is repeated); a position outside the grid raises ValueError. It must handle a 1000x1000 grid
with 200,000 additions in a few seconds.
""", """
assert count_islands(3, 3, []) == []
assert count_islands(3, 3, [(0, 0), (0, 1), (1, 2), (2, 1), (1, 1)]) == [1, 1, 2, 3, 1]
assert count_islands(1, 1, [(0, 0), (0, 0)]) == [1, 1]
assert count_islands(2, 2, [(0, 0), (1, 1), (0, 1)]) == [1, 2, 1]
assert count_islands(1, 5, [(0, 0), (0, 2), (0, 4), (0, 1), (0, 3)]) == [1, 2, 3, 2, 1]
for bad in [(3, 0), (0, -1)]:
    try:
        count_islands(3, 3, [bad]); raise AssertionError("no ValueError")
    except ValueError:
        pass
import random, time
rng = random.Random(7)
cells = [(rng.randrange(1000), rng.randrange(1000)) for _ in range(200000)]
t = time.time()
res = count_islands(1000, 1000, cells)
assert time.time() - t < 8, "too slow"
assert len(res) == 200000 and res[0] == 1
""", """
def count_islands(rows, cols, cells):
    parent = {}
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    out, count = [], 0
    for r, c in cells:
        if not (0 <= r < rows and 0 <= c < cols):
            raise ValueError
        if (r, c) not in parent:
            parent[(r, c)] = (r, c); count += 1
            for nb in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)):
                if nb in parent:
                    a, b = find((r, c)), find(nb)
                    if a != b:
                        parent[a] = b; count -= 1
        out.append(count)
    return out
""")

task("ttl_cache", """
Write a Python class `TTLCache(capacity, ttl, clock)` : an LRU cache whose entries also expire.
`clock` is a function returning the current time in seconds (tests pass a fake one). Methods:
`get(key, default=None)` returns the value, or `default` if the key is missing or expired, and
marks it as most recently used; `put(key, value)` stores it with a fresh expiry time `ttl` seconds
from now and marks it most recently used; `__len__` counts entries that haven't expired. An entry
expires when clock() >= stored time + ttl. When a put would go over `capacity`, first drop expired
entries, then if still full drop the least recently used one. capacity < 1 or ttl <= 0 raises
ValueError.
""", """
now = [0.0]
clock = lambda: now[0]
c = TTLCache(2, 10, clock)
c.put("a", 1); c.put("b", 2)
assert c.get("a") == 1
c.put("c", 3)
assert c.get("b") is None and c.get("a") == 1 and c.get("c") == 3
now[0] = 9.99
assert c.get("a") == 1
now[0] = 10.0
assert c.get("a", "gone") == "gone" and len(c) == 0
c.put("x", 1); now[0] = 15; c.put("y", 2); now[0] = 20
assert len(c) == 1
c.put("z", 3)
assert c.get("y") == 2 and c.get("z") == 3 and c.get("x") is None
now[0] = 25
c.put("y", 22)
assert c.get("y") == 22 and len(c) == 2
now[0] = 31
assert c.get("y") == 22 and c.get("z") is None
d = TTLCache(1, 5, clock); d.put(1, "one"); d.put(2, "two")
assert d.get(1) is None and d.get(2) == "two"
for args in [(0, 5), (1, 0)]:
    try:
        TTLCache(*args, clock); raise AssertionError("no ValueError")
    except ValueError:
        pass
""", """
class TTLCache:
    def __init__(self, capacity, ttl, clock):
        if capacity < 1 or ttl <= 0:
            raise ValueError
        from collections import OrderedDict
        self.cap, self.ttl, self.clock, self.d = capacity, ttl, clock, OrderedDict()
    def _purge(self):
        now = self.clock()
        for k in [k for k, (v, t) in self.d.items() if now >= t]:
            del self.d[k]
    def get(self, key, default=None):
        if key in self.d:
            v, t = self.d[key]
            if self.clock() < t:
                self.d.move_to_end(key); return v
            del self.d[key]
        return default
    def put(self, key, value):
        if key in self.d:
            del self.d[key]
        if len(self.d) >= self.cap:
            self._purge()
        if len(self.d) >= self.cap:
            self.d.popitem(last=False)
        self.d[key] = (value, self.clock() + self.ttl)
    def __len__(self):
        self._purge(); return len(self.d)
""")

task("fix_rotated_search", """
This function should return True if `target` is in `nums`, a sorted list of ints (duplicates
allowed) that may have been rotated at some pivot, like [4,5,6,6,7,0,1,2,4,4]. It has bugs. Return
a corrected `search(nums, target)` that runs in O(log n) when there are no duplicates.

```python
def search(nums, target):
    lo, hi = 0, len(nums)
    while lo < hi:
        mid = (lo + hi) // 2
        if nums[mid] == target:
            return True
        if nums[lo] < nums[mid]:
            if nums[lo] <= target < nums[mid]:
                hi = mid
            else:
                lo = mid
        else:
            if nums[mid] < target <= nums[hi]:
                lo = mid + 1
            else:
                hi = mid
    return False
```
""", """
import random
assert search([], 1) is False
assert search([1], 1) is True and search([1], 0) is False
assert search([4, 5, 6, 6, 7, 0, 1, 2, 4, 4], 0) is True
assert search([4, 5, 6, 6, 7, 0, 1, 2, 4, 4], 3) is False
assert search([1, 0, 1, 1, 1], 0) is True
assert search([1, 1, 1, 0, 1], 0) is True
assert search([3, 1], 1) is True and search([3, 1], 3) is True
rng = random.Random(3)
for _ in range(3000):
    base = sorted(rng.randrange(8) for _ in range(rng.randrange(1, 12)))
    k = rng.randrange(len(base))
    nums = base[k:] + base[:k]
    t = rng.randrange(-1, 9)
    assert search(nums, t) == (t in nums), (nums, t)
""", """
def search(nums, target):
    lo, hi = 0, len(nums) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if nums[mid] == target:
            return True
        if nums[lo] == nums[mid] == nums[hi]:
            lo += 1; hi -= 1
        elif nums[lo] <= nums[mid]:
            if nums[lo] <= target < nums[mid]:
                hi = mid - 1
            else:
                lo = mid + 1
        else:
            if nums[mid] < target <= nums[hi]:
                lo = mid + 1
            else:
                hi = mid - 1
    return False
""")

task("rate_limiter", """
Write a Python class `SlidingWindowLimiter(limit, window)`: a per-client rate limiter. Method
`allow(client, now)` takes a client id and a timestamp in seconds (calls come in non-decreasing
time order) and returns True if the request is allowed: at most `limit` allowed requests per
client in any window of `window` seconds, where a request at time t counts against requests in
(t - window, t]. Rejected requests don't count. Memory must not grow without bound for clients
that stop sending: `size()` returns the number of timestamps still stored across all clients, and
after a call at time `now`, no stored timestamp may be older than now - window (for any client).
""", """
r = SlidingWindowLimiter(2, 10)
assert r.allow("a", 0) and r.allow("a", 1)
assert not r.allow("a", 5)
assert r.allow("b", 5)
assert not r.allow("a", 9.99)
assert r.allow("a", 10)
assert not r.allow("a", 10.5)
assert r.allow("a", 11)
r2 = SlidingWindowLimiter(1, 1)
for t in range(1000):
    assert r2.allow(f"c{t}", t)
r2.allow("z", 2000)
assert r2.size() <= 1, r2.size()
r3 = SlidingWindowLimiter(3, 5)
assert [r3.allow("x", t) for t in [0, 0, 0, 0, 4.9, 5, 5, 5, 5]] == [
    True, True, True, False, False, True, True, True, False]
""", """
class SlidingWindowLimiter:
    def __init__(self, limit, window):
        from collections import deque
        self.limit, self.window, self.q, self.all = limit, window, {}, deque()
    def allow(self, client, now):
        from collections import deque
        while self.all and self.all[0][0] <= now - self.window:
            t, c = self.all.popleft()
            self.q[c].popleft()
            if not self.q[c]:
                del self.q[c]
        q = self.q.get(client)
        if q is not None and len(q) >= self.limit:
            return False
        self.q.setdefault(client, deque()).append(now)
        self.all.append((now, client))
        return True
    def size(self):
        return len(self.all)
""")

task("edit_script", """
Write a Python function `diff(a, b)` that takes two lists and returns a shortest edit script that
turns `a` into `b`, as a list of operations: ("keep", x), ("del", x) and ("add", x). Applying the
script in order (keep: copy the next item of `a`; del: skip the next item of `a`; add: output x)
must give exactly `b`, and the number of del + add operations must be the minimum possible. It
must handle lists of 2000 items each in a few seconds.
""", """
def apply(a, script):
    out, i = [], 0
    for op, x in script:
        if op == "keep":
            assert a[i] == x, ("keep mismatch", i); out.append(x); i += 1
        elif op == "del":
            assert a[i] == x, ("del mismatch", i); i += 1
        elif op == "add":
            out.append(x)
        else:
            raise AssertionError(op)
    assert i == len(a), "not all of a consumed"
    return out
def lcs(a, b):
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b):
            cur.append(prev[j] + 1 if x == y else max(prev[j + 1], cur[j]))
        prev = cur
    return prev[-1]
import random, time
rng = random.Random(11)
cases = [([], []), ([], [1, 2]), ([1, 2], []), (list("abcabba"), list("cbabac")),
         (list("kitten"), list("sitting")), ([1, 2, 3], [1, 2, 3])]
for _ in range(200):
    cases.append(([rng.randrange(4) for _ in range(rng.randrange(15))],
                  [rng.randrange(4) for _ in range(rng.randrange(15))]))
for a, b in cases:
    s = diff(a, b)
    assert apply(a, s) == b, (a, b)
    edits = sum(op != "keep" for op, _ in s)
    assert edits == len(a) + len(b) - 2 * lcs(a, b), (a, b, edits)
a = [rng.randrange(50) for _ in range(2000)]
b = [x if rng.random() < 0.9 else rng.randrange(50) for x in a]
t = time.time()
s = diff(a, b)
assert time.time() - t < 10, "too slow"
assert apply(a, s) == b
""", """
def diff(a, b):
    n, m = len(a), len(b)
    L = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            L[i][j] = L[i + 1][j + 1] + 1 if a[i] == b[j] else max(L[i + 1][j], L[i][j + 1])
    i = j = 0; out = []
    while i < n and j < m:
        if a[i] == b[j]:
            out.append(("keep", a[i])); i += 1; j += 1
        elif L[i + 1][j] >= L[i][j + 1]:
            out.append(("del", a[i])); i += 1
        else:
            out.append(("add", b[j])); j += 1
    out += [("del", x) for x in a[i:]] + [("add", x) for x in b[j:]]
    return out
""")

task("grid_path", """
Write a Python function `cheapest(grid, portals)` where `grid` is a list of equal-length strings:
'#' is a wall, digits '1'-'9' are cells that cost that much to enter, 'S' is the start and 'E' the
end (both cost 0 to enter). You move up, down, left or right. `portals` is a list of
((r1, c1), (r2, c2), cost) tuples: from either cell you can jump to the other for `cost` (portal
ends are never walls). Return the minimum total cost from S to E, or -1 if E can't be reached.
There is exactly one S and one E.
""", """
assert cheapest(["SE"], []) == 0
assert cheapest(["S1E"], []) == 1
assert cheapest(["S9E", "111"], []) == 3
assert cheapest(["S2E", "111"], []) == 2
assert cheapest(["S#E"], []) == -1
assert cheapest(["S#E"], [((0, 0), (0, 2), 7)]) == 7
assert cheapest(["S11", "###", "11E"], [((0, 2), (2, 0), 3)]) == 2 + 3 + 1
assert cheapest(["S99999E"], [((0, 1), (0, 5), 1)]) == 10
assert cheapest(["E1S"], [((0, 2), (0, 0), 5)]) == 1
big = ["S" + "1" * 199] + ["1" * 200 for _ in range(198)] + ["1" * 199 + "E"]
assert cheapest(big, []) == 398 - 1
""", """
def cheapest(grid, portals):
    import heapq
    R, C = len(grid), len(grid[0])
    for r in range(R):
        for c in range(C):
            if grid[r][c] == "S": s = (r, c)
            if grid[r][c] == "E": e = (r, c)
    jumps = {}
    for a, b, cost in portals:
        jumps.setdefault(tuple(a), []).append((tuple(b), cost))
        jumps.setdefault(tuple(b), []).append((tuple(a), cost))
    def enter(p):
        ch = grid[p[0]][p[1]]
        return 0 if ch in "SE" else int(ch)
    dist = {s: 0}; pq = [(0, s)]
    while pq:
        d, p = heapq.heappop(pq)
        if p == e:
            return d
        if d > dist[p]:
            continue
        r, c = p
        nxt = [((r + dr, c + dc), None) for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1))]
        nxt += jumps.get(p, [])
        for q, cost in nxt:
            if not (0 <= q[0] < R and 0 <= q[1] < C) or grid[q[0]][q[1]] == "#":
                continue
            nd = d + (enter(q) if cost is None else cost)
            if nd < dist.get(q, float("inf")):
                dist[q] = nd; heapq.heappush(pq, (nd, q))
    return -1
""")


# --- The hard set: longer programs with several parts (./coding_bench.py run --set hard). ---

NO_IMPORT = '''
_src = open(__file__).read().split("# --- tests ---")[0]
for _mod in {mods}:
    assert not re.search(r"^\\s*(import|from)\\s+" + _mod + r"\\b", _src, re.M), "uses " + _mod
'''


def hard(id, prompt, tests, reference, forbid=()):
    pre = ("import re\n" + NO_IMPORT.format(mods=repr(list(forbid)))) if forbid else ""
    task(id, prompt, pre + tests.strip(), reference)
    TASKS[-1]["set"] = "hard"


hard("regex_engine", """
Write a Python function `fullmatch(pattern, text)` that returns True if the whole `text` matches
`pattern`, implementing the regex engine yourself (don't import re or any regex library).
Support: literal characters; `.` (any character); `*`, `+`, `?` (greedy or not doesn't matter,
only whether it matches); alternation `|` with the lowest precedence; grouping `( )` (an empty
group `()` matches the empty string); character classes `[abc]`, ranges `[a-z0-9]`, negated
`[^...]` (a `]` right after `[` or `[^` is literal, a `-` first or last is literal); and `\\` to
escape any special character, inside or outside a class. It must not take exponential time:
patterns like `(a*)*b` against 40 `a`s must answer in well under a second. Raise ValueError for
invalid patterns (unbalanced parentheses, unclosed class, a quantifier with nothing before it,
a trailing backslash).
""", """
import random, time
cases = [("abc", "abc", True), ("abc", "abd", False), ("a.c", "axc", True), ("a*", "", True),
         ("a+", "", False), ("colou?r", "color", True), ("colou?r", "colour", True),
         ("cat|dog", "dog", True), ("cat|dog", "cats", False), ("(ab)+", "ababab", True),
         ("(ab)+", "aba", False), ("[a-c]+x", "abcax", True), ("[^0-9]*", "abc", True),
         ("[^0-9]*", "ab1", False), ("[]a]+", "]a]", True), ("[a-]+", "a-a", True),
         ("\\\\.\\\\*", ".*", True), ("\\\\.", "x", False), ("[\\\\]]", "]", True), ("()", "", True),
         ("a(|b)c", "ac", True), ("a(|b)c", "abc", True), ("(a|b)*abb", "babaabb", True),
         ("x*y*z*", "xxzz", True), ("((a))", "a", True), ("a|", "", True), ("|a", "a", True)]
for p, t, want in cases:
    assert fullmatch(p, t) == want, (p, t, want)
t0 = time.time()
assert fullmatch("(a*)*b", "a" * 40) is False
assert fullmatch("(a|aa)*c", "a" * 40) is False
assert fullmatch("(x+x+)+y", "x" * 40) is False
assert time.time() - t0 < 2, "exponential"
for bad in ["(ab", "ab)", "[ab", "*a", "a|*", "\\\\", "(*)", "[a-"]:
    try:
        fullmatch(bad, "a"); raise AssertionError("no ValueError for " + repr(bad))
    except ValueError:
        pass
rng = random.Random(5)
def gen(d=0):
    k = rng.random()
    if d > 3 or k < 0.35:
        return rng.choice(["a", "b", ".", "[ab]", "[^a]", "c"])
    if k < 0.55:
        return gen(d + 1) + gen(d + 1)
    if k < 0.7:
        return "(" + gen(d + 1) + "|" + gen(d + 1) + ")"
    return "(" + gen(d + 1) + ")" + rng.choice("*+?")
for _ in range(1500):
    p = gen()
    s = "".join(rng.choice("abc") for _ in range(rng.randrange(7)))
    assert fullmatch(p, s) == bool(re.fullmatch(p, s)), (p, s)
""", """
def fullmatch(pattern, text):
    # parse into an AST, compile to an NFA (Thompson), simulate with state sets
    pos = 0
    n = len(pattern)
    def peek():
        return pattern[pos] if pos < n else None
    def parse_alt():
        nonlocal pos
        branches = [parse_seq()]
        while peek() == "|":
            pos += 1
            branches.append(parse_seq())
        return ("alt", branches) if len(branches) > 1 else branches[0]
    def parse_seq():
        nonlocal pos
        items = []
        while peek() is not None and peek() not in "|)":
            atom = parse_atom()
            while peek() is not None and peek() in "*+?":
                if atom[0] == "rep_marker":
                    raise ValueError
                atom = (peek(), atom); pos += 1
                if peek() is not None and peek() in "*+?":
                    raise ValueError
            items.append(atom)
        return ("seq", items)
    def parse_atom():
        nonlocal pos
        c = peek()
        if c in "*+?":
            raise ValueError
        if c == "(":
            pos += 1
            inner = parse_alt()
            if peek() != ")":
                raise ValueError
            pos += 1
            return inner
        if c == "[":
            pos += 1
            neg = False
            if peek() == "^":
                neg = True; pos += 1
            items = []
            first = True
            while True:
                ch = peek()
                if ch is None:
                    raise ValueError
                if ch == "]" and not first:
                    pos += 1; break
                first = False
                if ch == "\\\\":
                    pos += 1
                    if peek() is None:
                        raise ValueError
                    ch = peek()
                pos += 1
                if peek() == "-" and pos + 1 < n and pattern[pos + 1] != "]":
                    pos += 1
                    hi = peek()
                    if hi == "\\\\":
                        pos += 1
                        if peek() is None:
                            raise ValueError
                        hi = peek()
                    pos += 1
                    items.append((ch, hi))
                else:
                    items.append((ch, ch))
            return ("class", neg, items)
        if c == "\\\\":
            pos += 1
            if peek() is None:
                raise ValueError
            ch = peek(); pos += 1
            return ("char", ch)
        pos += 1
        if c == ".":
            return ("any",)
        return ("char", c)
    ast = parse_alt()
    if pos != n:
        raise ValueError
    # NFA: states are ints; trans[s] = list of (pred or None for epsilon, target)
    trans = []
    def new():
        trans.append([]); return len(trans) - 1
    def build(node):
        kind = node[0]
        if kind in ("char", "any", "class"):
            s, e = new(), new()
            if kind == "char":
                pred = (lambda ch, c=node[1]: ch == c)
            elif kind == "any":
                pred = (lambda ch: True)
            else:
                neg, items = node[1], node[2]
                pred = (lambda ch, neg=neg, items=items: any(lo <= ch <= hi for lo, hi in items) != neg)
            trans[s].append((pred, e)); return s, e
        if kind == "seq":
            s = new(); cur = s
            for it in node[1]:
                a, b = build(it); trans[cur].append((None, a)); cur = b
            return s, cur
        if kind == "alt":
            s, e = new(), new()
            for br in node[1]:
                a, b = build(br); trans[s].append((None, a)); trans[b].append((None, e))
            return s, e
        a, b = build(node[1]); s, e = new(), new()
        trans[s].append((None, a)); trans[b].append((None, e))
        if kind in "*?":
            trans[s].append((None, e))
        if kind in "*+":
            trans[b].append((None, a))
        return s, e
    start, end = build(ast)
    def closure(states):
        stack, seen = list(states), set(states)
        while stack:
            s = stack.pop()
            for pred, t in trans[s]:
                if pred is None and t not in seen:
                    seen.add(t); stack.append(t)
        return seen
    cur = closure({start})
    for ch in text:
        cur = closure({t for s in cur for pred, t in trans[s] if pred is not None and pred(ch)})
        if not cur:
            return False
    return end in cur
""", forbid=("re", "regex"))

hard("json_parser", """
Write a Python function `parse(text)` that parses a JSON document (RFC 8259) and returns the same
Python value json.loads would, without importing json or any parser library: objects -> dict
(a repeated key keeps the last value), arrays -> list, strings (with all escapes, including
\\uXXXX and surrogate pairs; a lone surrogate is kept as is, like json.loads), numbers -> int when
there is no fraction or exponent, else float, true/false/null -> True/False/None. Whitespace is
space, tab, newline and carriage return only. Raise ValueError for anything json.loads rejects:
trailing commas, leading zeros (01), a leading +, single quotes, unquoted keys, NaN/Infinity,
raw control characters (below U+0020) inside strings, bad escapes, trailing text after the
value, an empty document. It must handle 500 levels of nesting and a 2 MB document in a few
seconds.
""", """
import json as _json, random, time
docs = ['0', '-0', '12', '-3.5e+2', '1E3', '0.25', '"a\\\\"b\\\\\\\\c\\\\/d\\\\b\\\\f\\\\n\\\\r\\\\t"',
        '"\\\\u00e9\\\\u20ac\\\\ud83d\\\\ude00"', '"\\\\ud800x"', '[]', '{}', ' [1, [2, [3]] ] ',
        '{"a": 1, "a": 2}', '{"k": [true, false, null, {"x": ""}]}', '"\\u00e9 \\U0001f600"']
for d in docs:
    got, want = parse(d), _json.loads(d)
    assert got == want and type(got) == type(want), (d, got, want)
assert type(parse("1.0")) is float and type(parse("10")) is int
bad = ['', ' ', '[1,]', '{"a":1,}', '01', '+1', "'a'", '{a:1}', 'NaN', 'Infinity', '-',
       '1.', '.5', '1e', '"abc', '"a\\x01b"', '"\\\\x41"', '"\\\\u12"', '[1] x', 'tru', 'nul',
       '[1 2]', '{"a" 1}', '{"a":}', '\\u00a0 1', '[', ']', '"\\\\ud83d\\\\u"']
for d in bad:
    try:
        parse(d); raise AssertionError("no ValueError for " + repr(d))
    except ValueError:
        pass
deep = "[" * 500 + "]" * 500
assert parse(deep) == _json.loads(deep)
rng = random.Random(9)
def gen(d=0):
    k = rng.random()
    if d > 4 or k < 0.4:
        return rng.choice([rng.randrange(-10**12, 10**12), rng.random() * 10**rng.randrange(-5, 5),
                           True, False, None, "".join(rng.choice("ab\\"\\\\\\n\\u00e9\\U0001f600 /\\t") for _ in range(rng.randrange(6)))])
    if k < 0.7:
        return [gen(d + 1) for _ in range(rng.randrange(5))]
    return {"".join(rng.choice("xyz\\"") for _ in range(rng.randrange(4))): gen(d + 1) for _ in range(rng.randrange(5))}
for _ in range(400):
    v = gen()
    s = _json.dumps(v, ensure_ascii=rng.random() < 0.5, indent=rng.choice([None, 2]))
    assert parse(s) == _json.loads(s), s
big = _json.dumps([gen() for _ in range(6000)])
assert len(big) > 300000
big = big * 1 if len(big) > 2_000_000 else "[" + ",".join([big] * (2_000_000 // len(big) + 1)) + "]"
t0 = time.time()
assert parse(big) == _json.loads(big)
assert time.time() - t0 < 15, "too slow"
""", """
def parse(text):
    WS = " \\t\\n\\r"
    n = len(text)
    def ws(i):
        while i < n and text[i] in WS:
            i += 1
        return i
    def value(i, depth):
        if i >= n:
            raise ValueError("end")
        c = text[i]
        if c == "{":
            obj = {}; i = ws(i + 1)
            if i < n and text[i] == "}":
                return obj, i + 1
            while True:
                if i >= n or text[i] != '"':
                    raise ValueError("key")
                k, i = string(i); i = ws(i)
                if i >= n or text[i] != ":":
                    raise ValueError(":")
                v, i = value(ws(i + 1), depth + 1); obj[k] = v; i = ws(i)
                if i < n and text[i] == ",":
                    i = ws(i + 1); continue
                if i < n and text[i] == "}":
                    return obj, i + 1
                raise ValueError("obj")
        if c == "[":
            arr = []; i = ws(i + 1)
            if i < n and text[i] == "]":
                return arr, i + 1
            while True:
                v, i = value(i, depth + 1); arr.append(v); i = ws(i)
                if i < n and text[i] == ",":
                    i = ws(i + 1); continue
                if i < n and text[i] == "]":
                    return arr, i + 1
                raise ValueError("arr")
        if c == '"':
            return string(i)
        for lit, val in (("true", True), ("false", False), ("null", None)):
            if text.startswith(lit, i):
                return val, i + len(lit)
        return number(i)
    def number(i):
        j = i
        if j < n and text[j] == "-":
            j += 1
        if j < n and text[j] == "0":
            j += 1
        elif j < n and "1" <= text[j] <= "9":
            while j < n and text[j].isdigit() and text[j] in "0123456789":
                j += 1
        else:
            raise ValueError("num")
        isf = False
        if j < n and text[j] == ".":
            j += 1; isf = True
            if not (j < n and text[j] in "0123456789"):
                raise ValueError("frac")
            while j < n and text[j] in "0123456789":
                j += 1
        if j < n and text[j] in "eE":
            j += 1; isf = True
            if j < n and text[j] in "+-":
                j += 1
            if not (j < n and text[j] in "0123456789"):
                raise ValueError("exp")
            while j < n and text[j] in "0123456789":
                j += 1
        s = text[i:j]
        return (float(s) if isf else int(s)), j
    ESC = {'"': '"', "\\\\": "\\\\", "/": "/", "b": "\\b", "f": "\\f", "n": "\\n", "r": "\\r", "t": "\\t"}
    def hex4(i):
        h = text[i:i + 4]
        if len(h) != 4 or any(ch not in "0123456789abcdefABCDEF" for ch in h):
            raise ValueError("u")
        return int(h, 16)
    def string(i):
        i += 1; out = []
        while True:
            j = i
            while j < n and text[j] != '"' and text[j] != "\\\\" and text[j] >= " ":
                j += 1
            out.append(text[i:j]); i = j
            if i >= n:
                raise ValueError("unterminated")
            c = text[i]
            if c == '"':
                return "".join(out), i + 1
            if c < " ":
                raise ValueError("control")
            i += 1
            if i >= n:
                raise ValueError("esc")
            e = text[i]
            if e in ESC:
                out.append(ESC[e]); i += 1
            elif e == "u":
                cp = hex4(i + 1); i += 5
                if 0xD800 <= cp < 0xDC00 and text.startswith("\\\\u", i):
                    lo = hex4(i + 2)
                    if 0xDC00 <= lo < 0xE000:
                        cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00); i += 6
                out.append(chr(cp))
            else:
                raise ValueError("bad escape")
    v, i = value(ws(0), 0)
    if ws(i) != n:
        raise ValueError("trailing")
    return v
""", forbid=("json", "simplejson", "ast"))

hard("mini_lisp", """
Write a Python function `run(src)` that interprets a small Lisp and returns the value of the
last top-level expression. Syntax: integers (possibly negative), symbols, `#t` and `#f`, lists
in parentheses, `;` comments to the end of the line. Special forms: `(define name expr)`,
`(define (f arg ...) body ...)`, `(lambda (arg ...) body ...)`, `(if c a b)` (only #f is
false), `(let ((name expr) ...) body ...)`, `(begin expr ...)`, `(set! name expr)` (changes the
nearest existing binding; NameError if there is none). Built-ins: `+ - *` (any number of
arguments; `(- x)` negates), `quotient` (truncates toward zero), `remainder` (sign of the
dividend), `= < > <= >=` (two arguments), `list`, `cons`, `car`, `cdr`, `null?`, `not`.
Closures capture their defining environment (lexical scope). Calls in tail position must not
grow the Python stack: a loop of 100000 tail calls must work. Results: integers as int, #t/#f
as True/False, lists as Python lists, a procedure as any object. Errors: unbound symbol ->
NameError; calling with the wrong number of arguments or calling a non-procedure ->
TypeError; syntax errors (unbalanced parentheses, empty program) -> SyntaxError.
""", """
assert run("(+ 1 2 3)") == 6
assert run("(- 5)") == -5 and run("(- 10 1 2)") == 7 and run("(*)") == 1
assert run("(quotient -7 2)") == -3 and run("(remainder -7 2)") == -1
assert run("(define x 4) (* x x)") == 16
assert run("(if 0 1 2)") == 1 and run("(if #f 1 2)") == 2
assert run("(define (fact n) (if (= n 0) 1 (* n (fact (- n 1))))) (fact 20)") == 2432902008176640000
assert run("(let ((a 1) (b 2)) (+ a b))") == 3
assert run("(define (adder n) (lambda (x) (+ x n))) ((adder 10) 5)") == 15
assert run('''
(define (make-counter)
  (let ((n 0))
    (lambda () (set! n (+ n 1)) n)))   ; closure with state
(define c1 (make-counter))
(define c2 (make-counter))
(c1) (c1) (c2)
(list (c1) (c2))''') == [3, 2]
assert run("(define x 1) (define (f) x) (let ((x 2)) (f))") == 1, "lexical scope"
assert run("(cons 1 (list 2 3))") == [1, 2, 3]
assert run("(car (cdr (list 1 2 3)))") == 2
assert run("(null? (cdr (list 1)))") is True and run("(not 0)") is False
assert run('''
(define (loop i acc) (if (= i 0) acc (loop (- i 1) (+ acc 1))))
(loop 100000 0)''') == 100000
assert run('''
(define (even? n) (if (= n 0) #t (odd? (- n 1))))
(define (odd? n) (if (= n 0) #f (even? (- n 1))))
(even? 100001)''') is False
assert run('''
(define (count-down n) (begin (if (= n 0) 0 (count-down (- n 1)))))
(count-down 100000)''') == 0
assert run('''
(define (map f xs) (if (null? xs) (list) (cons (f (car xs)) (map f (cdr xs)))))
(map (lambda (x) (* x x)) (list 1 2 3 4))''') == [1, 4, 9, 16]
for src, err in [("(undefined 1)", NameError), ("(set! nope 1)", NameError), ("((lambda (x) x))", TypeError),
                 ("(1 2)", TypeError), ("(+ 1", SyntaxError), ("(+ 1))", SyntaxError), ("", SyntaxError),
                 ("(define (f a b) a) (f 1)", TypeError)]:
    try:
        run(src); raise AssertionError("no " + err.__name__ + " for " + repr(src))
    except err:
        pass
""", """
def run(src):
    import re
    toks = re.findall(r"\\(|\\)|[^\\s()]+", re.sub(r";[^\\n]*", "", src))
    pos = 0
    def read():
        nonlocal pos
        if pos >= len(toks):
            raise SyntaxError("eof")
        t = toks[pos]; pos += 1
        if t == "(":
            out = []
            while True:
                if pos >= len(toks):
                    raise SyntaxError("unbalanced")
                if toks[pos] == ")":
                    pos += 1; return out
                out.append(read())
        if t == ")":
            raise SyntaxError("unexpected )")
        if t == "#t":
            return True
        if t == "#f":
            return False
        try:
            return int(t)
        except ValueError:
            return ("sym", t)
    prog = []
    while pos < len(toks):
        prog.append(read())
    if not prog:
        raise SyntaxError("empty")
    class Env:
        def __init__(self, vars, outer):
            self.vars, self.outer = vars, outer
        def find(self, name):
            e = self
            while e is not None:
                if name in e.vars:
                    return e
                e = e.outer
            raise NameError(name)
    class Proc:
        def __init__(self, params, body, env):
            self.params, self.body, self.env = params, body, env
    def sub(*a):
        return -a[0] if len(a) == 1 else a[0] - sum(a[1:])
    def prod(*a):
        r = 1
        for x in a:
            r *= x
        return r
    def quot(a, b):
        q = abs(a) // abs(b)
        return q if (a >= 0) == (b >= 0) else -q
    def rem(a, b):
        return a - b * quot(a, b)
    def two(f):
        def g(*a):
            if len(a) != 2:
                raise TypeError("arity")
            return f(*a)
        return g
    def one(f):
        def g(*a):
            if len(a) != 1:
                raise TypeError("arity")
            return f(*a)
        return g
    glob = Env({"+": lambda *a: sum(a), "-": sub, "*": prod, "quotient": two(quot), "remainder": two(rem),
                "=": two(lambda a, b: a == b), "<": two(lambda a, b: a < b), ">": two(lambda a, b: a > b),
                "<=": two(lambda a, b: a <= b), ">=": two(lambda a, b: a >= b), "list": lambda *a: list(a),
                "cons": two(lambda a, b: [a] + b), "car": one(lambda a: a[0]), "cdr": one(lambda a: a[1:]),
                "null?": one(lambda a: a == []), "not": one(lambda a: a is False)}, None)
    def name(x):
        if not (isinstance(x, tuple) and x[0] == "sym"):
            raise SyntaxError("expected a name")
        return x[1]
    def ev(x, env):
        while True:
            if isinstance(x, tuple):
                return env.find(x[1]).vars[x[1]]
            if not isinstance(x, list):
                return x
            if not x:
                raise SyntaxError("empty call")
            head = x[0]
            op = head[1] if isinstance(head, tuple) else None
            if op == "define" and (op not in env.vars or True) and not _shadowed(env, "define"):
                if isinstance(x[1], list):
                    env.vars[name(x[1][0])] = Proc([name(p) for p in x[1][1:]], x[2:], env)
                else:
                    env.vars[name(x[1])] = ev(x[2], env)
                return None
            if op == "lambda" and not _shadowed(env, op):
                return Proc([name(p) for p in x[1]], x[2:], env)
            if op == "if" and not _shadowed(env, op):
                x = x[2] if ev(x[1], env) is not False else x[3]; continue
            if op == "set!" and not _shadowed(env, op):
                e = env.find(name(x[1])); e.vars[name(x[1])] = ev(x[2], env); return None
            if op == "begin" and not _shadowed(env, op):
                for y in x[1:-1]:
                    ev(y, env)
                x = x[-1]; continue
            if op == "let" and not _shadowed(env, op):
                env = Env({name(b[0]): ev(b[1], env) for b in x[1]}, env)
                for y in x[2:-1]:
                    ev(y, env)
                x = x[-1]; continue
            f = ev(head, env)
            args = [ev(a, env) for a in x[1:]]
            if isinstance(f, Proc):
                if len(args) != len(f.params):
                    raise TypeError("arity")
                env = Env(dict(zip(f.params, args)), f.env)
                for y in f.body[:-1]:
                    ev(y, env)
                x = f.body[-1]; continue
            if callable(f):
                return f(*args)
            raise TypeError("not a procedure")
    def _shadowed(env, op):
        return False
    result = None
    for form in prog:
        result = ev(form, glob)
    return result
""")

hard("cron_next", """
Write a Python function `next_run(expr, after)` for 5-field cron expressions
("minute hour day-of-month month day-of-week") that returns the first datetime strictly after
`after` (a naive datetime.datetime) at which the expression fires, with seconds and
microseconds 0. Each field is `*` or a comma-separated list of items: `n`, `a-b`, `*/s` or
`a-b/s` (step s from a), also `n/s` meaning n-max/s. Ranges: minute 0-59, hour 0-23,
day-of-month 1-31, month 1-12 or JAN-DEC, day-of-week 0-7 or SUN-SAT (0 and 7 are both Sunday),
names in any case. Like Vixie cron: if both day-of-month and day-of-week are restricted (not
`*`), a day matches when either one matches; if one is `*`, only the other counts. Raise
ValueError for invalid expressions (wrong field count, out-of-range values, a > b, step 0,
unknown names) and if no time ever matches (like "0 0 30 2 *"). Must be fast: "0 0 29 2 *"
(leap days) included, 300 calls in a few seconds.
""", """
import datetime as dt, random, time
D = dt.datetime
assert next_run("* * * * *", D(2026, 1, 1, 12, 0, 30)) == D(2026, 1, 1, 12, 1)
assert next_run("0 * * * *", D(2026, 1, 1, 12, 0)) == D(2026, 1, 1, 13, 0)
assert next_run("*/15 9-17 * * MON-FRI", D(2026, 10, 16, 17, 50)) == D(2026, 10, 19, 9, 0)
assert next_run("30 4 1,15 * 5", D(2026, 10, 2, 0, 0)) == D(2026, 10, 2, 4, 30)
assert next_run("30 4 1,15 * 5", D(2026, 10, 2, 5, 0)) == D(2026, 10, 9, 4, 30)
assert next_run("0 0 29 2 *", D(2026, 3, 1)) == D(2028, 2, 29)
assert next_run("0 0 31 * *", D(2026, 4, 1)) == D(2026, 5, 31)
assert next_run("0 12 * jan,Jul sun", D(2026, 1, 5)) == D(2026, 1, 11, 12, 0)
assert next_run("0 0 * * 7", D(2026, 10, 16)) == D(2026, 10, 18)
assert next_run("5/20 * * * *", D(2026, 1, 1, 0, 26)) == D(2026, 1, 1, 0, 45)
assert next_run("0 0 1-7 * 1", D(2026, 10, 2)) == D(2026, 10, 3), "dom OR dow"
assert next_run("59 23 31 12 *", D(2026, 12, 31, 23, 59)) == D(2027, 12, 31, 23, 59)
for bad in ["* * * *", "60 * * * *", "* 24 * * *", "* * 0 * *", "* * * 13 *", "* * * * 8",
            "5-1 * * * *", "*/0 * * * *", "* * * FOO *", "0 0 30 2 *", "0 0 31 4,6 *", "a * * * *", "1,,2 * * * *"]:
    try:
        next_run(bad, D(2026, 1, 1)); raise AssertionError("no ValueError for " + repr(bad))
    except ValueError:
        pass
def ref(expr, after):
    mins, hours, doms, mons, dows = expr.split()
    def items(f, lo, hi):
        out = set()
        for part in f.split(","):
            rng_, _, step = part.partition("/")
            a, b = (lo, hi) if rng_ == "*" else (int(rng_.split("-")[0]), int(rng_.split("-")[-1]) if "-" in rng_ else (hi if step else int(rng_)))
            out |= set(range(a, b + 1, int(step) if step else 1))
        return out
    M, H, DM, MO, DW = items(mins, 0, 59), items(hours, 0, 23), items(doms, 1, 31), items(mons, 1, 12), {d % 7 for d in items(dows, 0, 7)}
    t = after.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
    day = t.date()
    for _ in range(366 * 9):
        dom_ok, dow_ok = day.day in DM, (day.isoweekday() % 7) in DW
        ok = (dom_ok or dow_ok) if (doms != "*" and dows != "*") else (dom_ok and dow_ok)
        if day.month in MO and ok:
            for h in sorted(H):
                for m in sorted(M):
                    c = D(day.year, day.month, day.day, h, m)
                    if c >= t:
                        return c
        day += dt.timedelta(days=1)
        t = max(t, D(day.year, day.month, day.day))
rng = random.Random(4)
def field(lo, hi):
    k = rng.random()
    if k < 0.3: return "*"
    if k < 0.5: return f"*/{rng.randrange(1, 8)}"
    a = rng.randrange(lo, hi + 1); b = rng.randrange(a, hi + 1)
    if k < 0.7: return f"{a}-{b}" + (f"/{rng.randrange(1, 4)}" if rng.random() < 0.4 else "")
    return ",".join(str(rng.randrange(lo, hi + 1)) for _ in range(rng.randrange(1, 4)))
t0 = time.time()
for _ in range(300):
    e = " ".join([field(0, 59), field(0, 23), field(1, 28), field(1, 12), field(0, 6)])
    a = D(2026, 1, 1) + dt.timedelta(minutes=rng.randrange(0, 525600 * 3))
    assert next_run(e, a) == ref(e, a), (e, a)
assert time.time() - t0 < 10, "too slow"
""", """
def next_run(expr, after):
    import datetime as dt, calendar
    MON = {m: i for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), 1)}
    DOW = {d: i for i, d in enumerate("SUN MON TUE WED THU FRI SAT".split())}
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError("fields")
    def num(s, names):
        if names and s.upper() in names:
            return names[s.upper()]
        if not s.isdigit():
            raise ValueError(s)
        return int(s)
    def parse(f, lo, hi, names=None):
        out = set()
        for item in f.split(","):
            if not item:
                raise ValueError("empty")
            r, slash, step = item.partition("/")
            if slash:
                if not step.isdigit() or int(step) == 0:
                    raise ValueError("step")
                step = int(step)
            else:
                step = 1
            if r == "*":
                a, b = lo, hi
            elif "-" in r:
                x, _, y = r.partition("-")
                a, b = num(x, names), num(y, names)
            else:
                a = num(r, names); b = hi if slash else a
            if not (lo <= a <= hi and lo <= b <= hi) or a > b:
                raise ValueError("range")
            out |= set(range(a, b + 1, step))
        return out
    M, H = parse(parts[0], 0, 59), parse(parts[1], 0, 23)
    DM, MO = parse(parts[2], 1, 31), parse(parts[3], 1, 12, MON)
    DW = {d % 7 for d in parse(parts[4], 0, 7, DOW)}
    dom_star, dow_star = parts[2] == "*", parts[4] == "*"
    if dow_star or not dom_star:
        # dom must be possible in some month (if dow is *, only dom counts)
        if dow_star and not any(d <= (29 if m == 2 else calendar.monthrange(2001, m)[1]) for m in MO for d in DM):
            raise ValueError("never")
    t = after.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
    day = t.date()
    for _ in range(366 * 9):
        if day.month in MO:
            dom_ok, dow_ok = day.day in DM, (day.isoweekday() % 7) in DW
            ok = (dom_ok or dow_ok) if not dom_star and not dow_star else (dom_ok and dow_ok)
            if ok:
                for h in sorted(H):
                    for m in sorted(M):
                        c = dt.datetime(day.year, day.month, day.day, h, m)
                        if c >= t:
                            return c
        day += dt.timedelta(days=1)
    raise ValueError("never")
""")

hard("txn_store", """
Write a Python class `Store` : an in-memory key-value store with nested transactions.
Methods: `set(key, value)`, `get(key)` (None if missing), `delete(key)` (no error if missing),
`count(value)` (how many keys currently have exactly this value), `begin()` (opens a
transaction, nested inside any open one), `rollback()` (undoes everything since the innermost
open begin() and closes it; RuntimeError if none is open), `commit()` (makes all changes of all
open transactions permanent and closes them all; RuntimeError if none is open), and
`snapshot()` (a dict of all current keys and values). Every method must be fast regardless of
store size or how many transactions are open: count in O(1), rollback in time proportional to
the changes it undoes, commit in O(1) or proportional to the changes. 200,000 operations with up
to 1,000 nested transactions must take a few seconds.
""", """
s = Store()
s.set("a", 10); s.set("b", 10)
assert s.count(10) == 2 and s.count(20) == 0
s.begin(); s.set("a", 20); assert s.get("a") == 20 and s.count(10) == 1
s.begin(); s.delete("b"); assert s.get("b") is None and s.count(10) == 0
s.rollback(); assert s.get("b") == 10 and s.count(10) == 1
s.rollback(); assert s.get("a") == 10 and s.count(10) == 2
for f in (s.rollback, s.commit):
    try:
        f(); raise AssertionError("no RuntimeError")
    except RuntimeError:
        pass
s.begin(); s.set("c", 1); s.begin(); s.set("c", 2); s.set("d", 2); s.commit()
assert s.snapshot() == {"a": 10, "b": 10, "c": 2, "d": 2} and s.count(2) == 2
try:
    s.rollback(); raise AssertionError("commit should close all")
except RuntimeError:
    pass
s.begin(); s.set("x", 1); s.set("x", 2); s.delete("x"); s.set("x", 3); s.rollback()
assert s.get("x") is None and "x" not in s.snapshot()
s.begin(); s.delete("zzz"); s.rollback(); assert s.snapshot() == {"a": 10, "b": 10, "c": 2, "d": 2}
s.set("n", None); assert s.count(None) == 1 and s.snapshot()["n"] is None
s.begin(); s.delete("n"); s.rollback(); assert s.snapshot()["n"] is None and s.count(None) == 1
import random, time
rng = random.Random(1)
t0 = time.time()
big = Store(); model = {}; stack = []
for i in range(200000):
    k = rng.random()
    key = rng.randrange(5000)
    if k < 0.5:
        v = rng.randrange(50); big.set(key, v)
        if stack: stack[-1].append((key, model.get(key, KeyError)))
        model[key] = v
    elif k < 0.7:
        big.delete(key)
        if stack: stack[-1].append((key, model.get(key, KeyError)))
        model.pop(key, None)
    elif k < 0.85 and len(stack) < 1000:
        big.begin(); stack.append([])
    elif k < 0.95 and stack:
        big.rollback()
        for key2, old in reversed(stack.pop()):
            if old is KeyError: model.pop(key2, None)
            else: model[key2] = old
    elif stack and k >= 0.995:
        big.commit(); stack = []
    if i % 20000 == 0:
        v = rng.randrange(50)
        assert big.count(v) == sum(1 for x in model.values() if x == v)
assert big.snapshot() == model
assert time.time() - t0 < 10, "too slow"
# A big store: count, begin and rollback must not touch every key.
huge = Store()
for i in range(200000):
    huge.set(i, i % 7)
t0 = time.time()
for i in range(100000):
    assert huge.count(i % 7) > 0
for i in range(5000):
    huge.begin(); huge.set(i, -1); huge.rollback()
assert huge.count(-1) == 0 and huge.get(3) == 3
assert time.time() - t0 < 3, "count, begin or rollback is O(store size)"
""", """
class Store:
    _MISSING = object()
    def __init__(self):
        self.data, self.counts, self.log = {}, {}, []
    def _raw_set(self, key, value):
        old = self.data.get(key, self._MISSING)
        if old is not self._MISSING:
            self.counts[self._k(old)] -= 1
        if value is self._MISSING:
            self.data.pop(key, None)
        else:
            self.data[key] = value
            self.counts[self._k(value)] = self.counts.get(self._k(value), 0) + 1
        return old
    @staticmethod
    def _k(v):
        try:
            hash(v); return (0, v)
        except TypeError:
            return (1, id(v))
    def set(self, key, value):
        old = self._raw_set(key, value)
        if self.log:
            self.log[-1].append((key, old))
    def get(self, key):
        return self.data.get(key)
    def delete(self, key):
        if key in self.data:
            self.set(key, self._MISSING)
    def count(self, value):
        return self.counts.get(self._k(value), 0)
    def begin(self):
        self.log.append([])
    def rollback(self):
        if not self.log:
            raise RuntimeError("no transaction")
        for key, old in reversed(self.log.pop()):
            self._raw_set(key, old)
    def commit(self):
        if not self.log:
            raise RuntimeError("no transaction")
        self.log = []
    def snapshot(self):
        return dict(self.data)
""")
