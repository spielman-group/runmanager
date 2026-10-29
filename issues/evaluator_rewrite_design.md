# Evaluating each global once, in dependency order

A design for porting upstream 2d8129d and 872a36d onto the fork's evaluator. It is
revised after Codex's review and Ian's decisions. Nothing is built.

## Today

`evaluator._evaluate_globals` evaluates every unevaluated expression in each pass,
and repeats the passes until the number of errors stops falling. Any error might be
a forward reference.
- An expression may be evaluated many times.
- A typo and a forward reference look the same until the passes give up.
- A global that refers to itself is reported as a NameError.

`TraceDictionary` records the names an expression looked up while it evaluated. The
dependency hierarchy therefore holds only the names on the path taken: for
`x if flag else y`, one branch. Four things read that hierarchy:
- the expansion guesser (`guess_expansion_modes`);
- the zipped-global check (b283c3a);
- `expander.scanned_globals`, behind the group tabs' scan icons and "Scanned over"
  tooltips;
- the pylab-name check (#163). This one reads the traced names rather than the
  hierarchy.

## Requirements

1. Each global's expression is compiled once and evaluated at most once per call.
2. A global is evaluated after the globals it refers to at definition time. Where no
   cycle results, it is also evaluated after the globals that its lambdas refer to.
3. The globals an expression refers to come from its symbol table. They are the
   names that are global and read, and not assigned, in the expression or in a
   lambda, comprehension or generator in it.
   - A name that a nested scope binds is not a reference.
   - A name that the expression itself assigns with `:=` is not a reference anywhere
     in it, nested scopes included. This fixes upstream bug 1.
4. A reference that occurs only inside a lambda is late-bound: Python binds the name
   when the lambda runs. Such a reference orders the globals where it can, as
   requirement 2 says, but never makes a cycle error on its own:
   - a lambda's reference to its own global is no edge;
   - mutual recursion through lambdas is allowed.

   This fixes upstream bug 3. A comprehension or generator runs at definition time,
   since runmanager turns an iterator into a tuple at once, so its references are
   not late-bound.
5. Every global in a cycle of definition-time references, a self-reference included,
   gets a ValueError naming a cycle through it. Every member of overlapping cycles is
   found before any global leaves the graph. This fixes upstream bug 2.
6. A global that refers to a failed global fails as Python fails it: a NameError, or
   for `globals()[name]` a KeyError. The fork adds no guard of its own.
   - A global refused by an expansion check keeps its value in the namespace, as
     e829c32 has it. So with `a = 3` expanded `outer`, `a` holds the ExpansionError
     and `b = a + 1` gives 4, by design.
7. An expression that does not compile holds its SyntaxError. That includes a syntax
   error, and `yield`.
8. The hierarchy maps each global to every global it refers to, definition-time and
   late-bound, on every branch.
9. A global named like a namespace name shadows it, and is evaluated before the
   globals that refer to it. So `x = 2*pi` with `pi = 3` gives 6.
10. The error contract, in order:
    1. A name defined in more than one active group: with `raise_exceptions`, its
       ValueError is raised at once. Otherwise each definition holds it, and the name
       is not evaluated.
    2. A SyntaxError, and a cycle's ValueError, are recorded for each global.
    3. If the namespace cannot be loaded: with `raise_exceptions`, the load's
       exception is raised at once, and the earlier SyntaxErrors and cycle errors go
       unreported. Otherwise each global without an earlier error holds the load's
       exception, and the earlier errors stay.
    4. Each global is evaluated, and its error, if any, is recorded.
    5. With `raise_exceptions` and any error so far, one Exception lists every failed
       global, as `name: type: message`.
    6. The expansion checks then run separately.
       - A zipped global that depends on another axis: with `raise_exceptions`, the
         first such global's ExpansionError is raised. Otherwise each holds one.
       - A zip group of unequal lengths: with `raise_exceptions`, a ValueError is
         raised. Otherwise each member holds an ExpansionError with its value.
11. The following are unchanged:
    - the return shapes: `evaluate_globals` returns 3 items and `_evaluate_globals` 4;
    - iterators turned into tuples;
    - #163's warnings, which now read requirement 3's references;
    - the frozen and JIT globals of the queue's compile path.
12. The work is linear in the number of globals: no expression is evaluated twice.

## Architecture

Upstream's `runmanager/evaluator.py` and `runmanager/exceptions.py` are taken whole
from `upstream/master`. They already hold every upstream evaluator commit the fork
has ported one at a time:
- `_global_references(table)` walks a `symtable` (872a36d).
- `_evaluate_globals` compiles each expression once and orders the globals with
  `graphlib.TopologicalSorter` (2d8129d).
- #163's namespace and deprecation checks read the references (45baf1f).
- `ExpansionError` carries the global's value (e829c32).

The fork's changes then follow as commits of their own.

The three upstream bugs, each offered upstream on `UpstreamBugFixes` once built:
1. **Walrus.** `_global_references` counts a name read in a nested scope even when the
   expression itself assigns it with `:=`. So `a = ((a := 2), (lambda: a)())[1]`
   makes an invented self-cycle. The fix subtracts the expression's own assigned
   names from its nested scopes' references.
2. **Overlapping cycles.** On a `CycleError`, only the reported cycle's members get
   the ValueError and leave the graph. With `a = b`, `b = a + c` and `c = b`, `c` then
   gets a NameError instead. The fix first takes all of the reported cycle's strongly
   connected component: the globals reachable from the cycle that can also reach
   back to it. All of them get the ValueError, and then they leave.
3. **Recursive lambdas.** `f = lambda n: 1 if n < 2 else n*f(n-1)` is refused as a
   self-reference, although Python binds `f` before the body runs. The fix records
   late-bound references, those occurring only inside a lambda's table, as edges of
   their own:
   - Cycle errors come from definition-time edges alone.
   - The ordering uses both, after dropping the late-bound edges that lie inside a
     cycle of the combined graph, a self-edge included.

`TraceDictionary` goes. Upstream removed it too, and nothing outside runmanager uses
it.

## Build

1. Take upstream's `evaluator.py` and `exceptions.py` whole, crediting 2d8129d,
   872a36d and the evaluator half of e829c32.
2. Fork commit: the zipped-global check compares the expansion settings themselves,
   not upstream's `axis_names`. `axis_names` treats an `outer` global and a zip group
   of the same name as one axis. That rule is part of e829c32's expansion-setting
   change, which Ian deferred, and the fork's expander keeps them as two axes.
3. Fix upstream bug 1, the walrus, with its test.
4. Fix upstream bug 2, overlapping cycles, with its test.
5. Fix upstream bug 3, recursive lambdas, with its test.
6. Fork commit: `TraceDictionary` leaves `__init__.py` and its `__all__`.
7. `usage.rst` states the behaviour changes below.

The fork's other evaluator changes need no line of their own:
- The guesser reads the hierarchy as it is.
- The frozen and JIT globals do not touch the evaluator.

After the build, `git diff upstream/master CodeHealth -- runmanager/evaluator.py` holds
step 2 and steps 3 to 5. Steps 3 to 5 leave the diff once upstream takes them.
`exceptions.py` does not differ.

| Upstream commit | In the build |
| --- | --- |
| 7729dc8, e06872f, 4c20a38 (split, docstrings, `iterator_to_tuple` moved) | Taken as is |
| ea20be6, 9d24a9a, a467455, 2754f37, 9a3bd5e, 65bc426 (style and lint) | Taken as is |
| 3483d09, b5c4b25, 28fc9ae, b283c3a, c110ea0 (already ported) | Taken as is |
| 2d8129d (once each, in order) | Taken, then upstream bugs 2 and 3 are fixed in its ordering: steps 4 and 5 |
| 872a36d (symbol-table references) | Taken, then upstream bug 1 is fixed: step 3 |
| e829c32 (expansion settings) | `ExpansionError(message, value)` taken as is. The other-axes check differs, without `axis_names`: step 2 |
| 45baf1f (#163) | Taken as is. It replaces the fork's check of the traced names |

## What changes for users

These are stated in `usage.rst` too.
- A self-reference or a cycle shows as a ValueError naming the cycle, not a NameError.
- A dependency only in a branch that is never taken still counts. So
  `a = 1 if True else b` with `b = a + 1` is a cycle, and both are refused.
- `x = 2*pi` defined before `pi = 3` now gives 6, not 2π: the global shadows pylab's
  `pi` for every global.
- Dynamic names, such as `globals()['a']` and `eval('a')`, give no dependency. They
  work only if that global happens to be evaluated earlier, and fail otherwise.
- A name read before the `:=` in the same expression assigns it, as in
  `(x2, (x2 := 1))[1]`, is not a dependency. It fails with a NameError even when a
  global `x2` exists.
- Recursive and mutually recursive lambdas keep working.
- Every branch now counts, wherever the hierarchy is read:
  - **The guesser** still runs only when every global evaluates, and only on scanned
    globals (681ea89). Within that, a scanned global that refers to a scanned list
    only in a branch not taken is now zipped with it.
  - **The zipped-global check** refuses a zipped global that refers to another axis
    only in such a branch.
  - **`scanned_globals`** counts a global as derived from another on its axis through
    any branch. Such a global leaves its tab's "Scanned over" tooltip, and a tab whose
    only scanned global it was loses its scan icon.
  - **#163's warning** covers a pylab name in a branch not taken.
- Evaluation is faster for long chains of globals.

## Tests

The tests drive the public surface (`evaluate_globals`, `_evaluate_globals` as the GUI
calls it, and the labconfig's `globals_namespace`), and cover only essential
behaviour. New ones, in `tests/test_scans.py`:
- **R1, R12:** a namespace file supplies `mark(x)`, which records each call. A chain
  of globals defined last first, each `mark(...)` of the next, calls it once per
  global.
- **R2, R4:** the chain evaluates. Also:
  - a recursive lambda works;
  - two mutually recursive lambdas work;
  - `f = lambda: c * 2`, `c = 3` and `d = f()` gives `d == 6` with `f` defined first.
- **R3:** the walrus case evaluates to 2, with no self-cycle. A comprehension or
  lambda parameter named like a global gives no dependency.
- **R5:** a self-reference, a two-global cycle, and the overlapping case each give
  every member the ValueError. A global that refers to a cycle member gets a
  NameError.
- **R6, R7, R8, R9:** a SyntaxError is held; the hierarchy lists both branches of a
  conditional; `pi` is shadowed.
- **R10:** a namespace file that raises gives every global without an earlier error
  the load's exception, and keeps a SyntaxError.

Existing tests already cover the rest:
- R10's raise paths and expansion checks, and R6's kept value: `ZipGroupTests`;
- R11's frozen and JIT path: `test_frozen_globals`, `test_queueing`,
  `FrozenGlobalsTests`;
- #163's warnings: `NamespaceTests`;
- the remote getters: `test_remote_api`.

Not a suite test, the benchmark: a chain of 1000 globals, defined last first.
- The namespace is loaded once beforehand, and `_namespace` is patched to return it,
  so loading the namespace is not timed.
- One warm-up call, then the median of five calls.
- The current evaluator and the new one run on the same machine in the same session,
  and the result is reported as their ratio, not asserted. Timings taken here only
  compare.

## Size

`evaluator.py` and `exceptions.py` become upstream's. The fork's diff from upstream is
step 2 and the three fixes, about 40 lines in all. Two lines go from `__init__.py`.
The tests above add about 100 lines.
