# Evaluator build, for review

The build of `issues/evaluator_rewrite_design.md` on CodeHealth, range
`6b08931..ac7eacc`.

| Commit | Upstream commit or fork change | What it does |
| --- | --- | --- |
| 0022619 | 2d8129d, picked | Evaluates each global once, in dependency order. `TraceDictionary` leaves `__init__.py` here, with upstream's hunk, so it needs no fork commit. |
| 8b522cc | c110ea0, picked | Converts every iterator global to a tuple. |
| 85335ac | 872a36d, picked | Finds the references from the symbol table. |
| 43e9a52 | e829c32, picked: `evaluator.py` and `exceptions.py` only | `ExpansionError(message, value)`; the expansion checks on `axis_names`. The expansion-setting change in `__main__.py`, `expander.py` and `group_manager.py` is deferred by Ian. |
| 9c23cca | 45baf1f, picked: `evaluator.py` only | #163 in the evaluator, reading the references. The other files already hold the fork's port (796789d). |
| 14d7f60 | Fork change | The other-axes check and the zip-length grouping compare the expansion settings, not `axis_names`. An outer global and a zip group of its name stay two axes. Two tests. |
| 06a9c44 | Fork tests | Tests the upstream evaluator's behaviour: evaluated once, in order; every branch in the hierarchy; shadowing; the error order; a failing namespace file; #163 on a branch not taken; the JIT path. Five fail on the previous evaluator. |
| 73650a9 | Fork fix, for upstream | A name the expression binds with `:=` is not a reference. The walk, with a stack, visits lambda defaults and skips lambda bodies, which a forward-reference case checks. |
| 193856a | Fork fix, for upstream | Every global of overlapping cycles gets the cycle's error, naming the whole strongly connected component. |
| 9884cbd | Fork fix, for upstream | A lambda may call itself by its global's name. Mutual recursion, and a reference read when the global is defined, stay cycles. |
| 4908c26 | Fork docs | `usage.rst` states which names count as references, and says `symtable`, not `ast`. |
| 4eac79a | Fork tests | A global defined in two groups: each copy holds a ValueError, not one shared object. |
| 276f0b6 | Fork docs | `usage.rst`: a failed global reaches only the globals that read it, and one that cannot be scanned keeps its value. |
| 5171522 | Design | R12 states the second symbol-table walk; the test list matches the tests. |
| ac7eacc | Fork docs, for upstream | `evaluate_globals`' docstring: which errors reach other globals, and which is raised with `raise_exceptions`. |

Each picked commit's conflicts are resolved in the same way. `evaluator.py` is upstream's
file at that commit under the fork's module banner, and the commit message says so. The
commits from 0022619 to 9c23cca do not import, because `differ.py` and `expander.py` need
45baf1f's `_evaluate_globals` and then e829c32 imports `axis_names`. Every commit from
14d7f60 on imports and passes the suite.

## Diff against upstream

- At each pick, `git diff <upstream commit> <pick> -- runmanager/evaluator.py` is three
  lines: the banner's file name, and the trailing blank line.
- `git diff upstream/master ac7eacc -- runmanager/evaluator.py` has these hunks:
  - the banner;
  - step 2 (14d7f60): the `axis_names` import, the other-axes check and the zip-length
    grouping;
  - the fixes: `_walrus_targets`, `_reachable`, the `lambdas` option of
    `_global_references`, the references line, the self-edge condition, and the cycle
    handler;
  - `evaluate_globals`' docstring (ac7eacc).
- `git diff upstream/master ac7eacc -- runmanager/exceptions.py` is the fork's banner and
  module docstring only. The design said this file would not differ; the fork keeps its
  banners on every module.

## Results

- Suite at ac7eacc: 185 passed, 55 subtests. Every fork commit from 14d7f60 on passes.
- Benchmark: a chain of 1000 globals defined last first, with a fresh namespace copy per
  run, one warm-up, and the median of five. The ratio of the previous evaluator to the new
  one is about 110.

## Found during the build

The design does not cover a global whose expression binds another global's name with
`:=`, as in `y = (x := 1)` beside a global `x`. It overwrites `x` in the shared namespace
for the globals evaluated after it, and both evaluators behave this way. It goes in
`KNOWN_ISSUES.md` with the records.

Upstream's code fails on two expressions no reasonable global needs, which 6b08931
evaluated or held. Over a thousand nested lambdas exceed the recursion limit in
`_global_references`, and an expression too large for `compile`, such as 100,000
additions, raises a RecursionError. Both escape `evaluate_globals` even with
`raise_exceptions=False`, since only a SyntaxError is held.
