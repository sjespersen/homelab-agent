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
