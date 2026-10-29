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
2. A global is evaluated after every global it refers to, as upstream orders them.
3. The globals an expression refers to come from its symbol table. They are the
   names that are global and read, and not assigned, in the expression or in a
   lambda, comprehension or generator in it.
   - A name that a nested scope binds is not a reference.
   - A name that the expression binds with `:=` in its own scope is not a reference
     anywhere in it, nested scopes included. A lambda's body is another scope, but its
     default arguments run in the expression's scope, so a `:=` in a default counts.
     Only real `:=` bindings count: a comprehension's target is not one. So in `z = ([a for a in range(2)], lambda: a)[1]`,
     the lambda's `a` is still a reference to the global `a`. This fixes upstream
     bug 1.
4. A global's reference to itself that occurs only inside a lambda is no edge. Python
   binds the name before the lambda's body runs, so `f = lambda n: 1 if n < 2 else
   n*f(n-1)` works. This fixes upstream bug 3, narrowed by Ian to direct
   self-recursion. Every other reference stays an ordinary dependency, exactly as
   upstream has it:
   - Mutual recursion through lambdas keeps upstream's cycle error.
   - A self-reference at definition time, in the expression itself or in a
     comprehension or generator in it, is still a cycle.
   - `a = (lambda: a)()` calls the lambda while `a` is being defined, so it gives a
     NameError at evaluation, not a cycle error.
5. Every global in a cycle of definition-time references, a self-reference included,
   gets a ValueError. All the globals of a strongly connected component, such as
   overlapping cycles, are found before any leaves the graph. The error names the
   whole component: "Globals refer to each other in a cycle: a, b, c". A
   self-reference keeps upstream's "Global a refers to itself". This fixes upstream
   bug 2.
6. A global that refers to a failed global fails as Python fails it: a NameError, or
   for `globals()[name]` a KeyError. The fork adds no guard of its own.
   - A global refused by an expansion check keeps its value in the namespace, as
     e829c32 has it. So with `a = 3` expanded `outer`, `a` holds the ExpansionError
     and `b = a + 1` gives 4, by design.
7. An expression that does not compile holds its SyntaxError. That includes a syntax
   error, and `yield`.
8. The hierarchy maps each global to every global it refers to, in the expression and
   its nested scopes, on every branch. It leaves out requirement 4's dropped
   self-edge. #163's analysis reads each expression's full references, kept
   separately, and they include it.
9. A global named like a namespace name shadows it, and is evaluated before the
   globals that refer to it. So `x = 2*pi` with `pi = 3` gives 6. The exception: a
   shadowing global that fails is not in the namespace, so the namespace name stays
   visible. With `pi = 1/0`, `pi` holds the ZeroDivisionError and `x = 2*pi` uses the
   namespace's `pi`.
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
12. Each expression is compiled at most once and evaluated at most once. It is also
    walked for references: once through its symbol table, and once through its syntax
    tree for `:=` targets. Both walks are bounded by the expression's size.
    For G globals and E dependency edges, ordering costs O(G + E). Each cycle costs
    one `CycleError` and one search of its strongly connected component, so C cycles
    cost O(C (G + E)) at worst. Besides that, the cost is the expressions' own work.

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
   expression itself binds it with `:=`. So `a = ((a := 2), (lambda: a)())[1]` makes
   an invented self-cycle. The fix finds the expression's real `:=` targets from its
   syntax tree: the `ast.NamedExpr` nodes in the expression's own scope. The walk goes
   into a lambda's default arguments, which run in that scope, and skips only lambda
   bodies. So `a = z + 1` with `z = (lambda x=(a := 2): a)()` gives `a = 3` and
   `z = 2`, as the fork does today, with no invented `a`–`z` cycle. It subtracts them from the references. The symbol table
   cannot be used, since on Python 3.12 and later it also marks comprehension
   targets as assigned in the expression's scope.
2. **Overlapping cycles.** On a `CycleError`, only the reported cycle's members get
   the ValueError and leave the graph. With `a = b`, `b = a + c` and `c = b`, `c` then
   gets a NameError instead. The fix first takes all of the reported cycle's strongly
   connected component: the globals reachable from the cycle that can also reach
   back to it. All of them get one ValueError naming the component, and then they
   leave.
3. **Recursive lambdas.** `f = lambda n: 1 if n < 2 else n*f(n-1)` is refused as a
   self-reference, although Python binds `f` before the body runs. The fix is one
   condition where the dependencies are made. `_global_references` gains an option
   that stops at lambda tables, giving the definition-time references. When a
   global's own name is among its references but not among its definition-time
   references, that self-edge is dropped. The ordering, the cycle errors and the
   guesser are otherwise upstream's.

`TraceDictionary` goes. Upstream removed it too, and nothing outside runmanager uses
it.

## Build

1. Take upstream's `evaluator.py` and `exceptions.py` whole, crediting 2d8129d,
   872a36d and the evaluator half of e829c32.
2. Fork commit: the other-axes check and the zip-length grouping both compare the
   expansion settings themselves, not upstream's `axis_names`, and its import goes.
   `axis_names` treats an `outer` global and a zip group of the same name as one
   axis. That rule is part of e829c32's expansion-setting change, which Ian deferred,
   and the fork's expander keeps them as two axes. A test shows that an outer
   `x = [1, 2]` and a `y = [3]` in zip group `x` stay independent, with no
   unequal-lengths error, as two axes.
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
| e829c32 (expansion settings) | `ExpansionError(message, value)` taken as is. The other-axes check and the zip-length grouping differ, without `axis_names`: step 2 |
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
- A name read before the `:=` in the same expression binds it, as in
  `(x2, (x2 := 1))[1]`, is not a dependency. It fails with a NameError unless `x2` is
  already in the namespace when the expression runs: a namespace name, or a global
  that happens to be evaluated earlier.
- A directly recursive lambda keeps working. Mutually recursive lambdas are a cycle,
  as upstream has them. `a = (lambda: a)()` gives a NameError.
- With `pi = 1/0`, a global using `pi` gets the namespace's `pi`.
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
- A static reference through an unscanned helper changes what is inferred. Take a
  scanned `x`, an unscanned `f = lambda: x*2` and a scanned `y = f()`.
  - Today `y`'s evaluation traces `x` through `f`, so the guesser zips `y` with `x`.
    With static references `y` refers only to `f`, so `y` stays `outer` beside `x`,
    and the scan grows to their product.
  - If `f` uses a pylab name, #163's warning moves from `y` to `f`.

  The guesser's policy is the settled one, unchanged; only its input changes.
- Evaluation is faster for long chains of globals.

## Tests

The tests drive the public surface (`evaluate_globals`, `_evaluate_globals` as the GUI
calls it, the labconfig's `globals_namespace`, and `get_queue_compile_globals`), and
cover only essential behaviour. New ones, in `tests/test_scans.py`:
- **R1, R12, evaluated at most once:** a namespace file supplies `mark(x)`, which
  records each call.
  - A chain of globals defined last first, each `mark(...)` of the next, calls it once
    per global.
  - A global `mark(0)/0` records one call and holds its ZeroDivisionError. The current
    evaluator records one call per pass.
- **R2, R4:**
  - the chain evaluates;
  - a recursive lambda works;
  - two mutually recursive lambdas hold the cycle error;
  - `a = (lambda: a)()` holds a NameError.
- **R3:**
  - the walrus case evaluates to 2;
  - `z = ([a for a in range(2)], lambda: a)[1]` depends on `a`;
  - a lambda parameter named like a global gives no dependency;
  - `a = z + 1` with `z = (lambda x=(a := 2): a)()` gives `a = 3` and `z = 2`.
- **R5:** a self-reference, a two-global cycle, and the overlapping case each give
  every member the ValueError. In the overlapping case its text names `a`, `b` and
  `c`. A global that refers to a cycle member gets a
  NameError.
- **R6 to R9:**
  - a SyntaxError is held;
  - the hierarchy lists both branches of a conditional;
  - `pi = 3` shadows the namespace's `pi`, and `pi = 1/0` does not.
- **R10:**
  - With `raise_exceptions`, the summary names every failed global. A name defined in
    two groups raises its own error first, before a SyntaxError elsewhere.
  - Without it, both definitions hold the duplicate's error and the other globals
    evaluate.
  - A namespace file that raises gives every global without an earlier error the
    load's exception, and keeps a SyntaxError.
- **Step 2:**
  - An outer `x` and a zip group named `x` stay two axes.
  - Its counterpart: an outer `x = [1, 2]` and `y = np.array(x)*2` in zip group `x`
    make `y` an ExpansionError, depending on another axis. Upstream's check, which
    takes them as one axis, would fail this test.
- **#163 from the references:** a pylab name only in a branch not taken still warns.
- **The frozen and JIT path:** a JIT global's expression changed after freezing
  compiles to the new value, while the frozen globals keep theirs. This goes through
  `get_queue_compile_globals` with the frozen overrides.

Existing tests already cover the rest:
- the expansion checks' raise paths, and R6's kept value: `ZipGroupTests`;
- frozen values that round-trip: `FrozenGlobalsTests`;
- the queue's compile of frozen rows: `test_frozen_globals`, `test_queueing`;
- #163's warning on a taken branch: `NamespaceTests`;
- the remote getters: `test_remote_api`.

Not a suite test, the benchmark: a chain of 1000 globals, defined last first.
- The namespace is loaded once beforehand. Each run gets a fresh copy of it from a
  patched `_namespace`, so loading is not timed and no run sees another's globals.
- One warm-up call, then the median of five calls.
- The current evaluator and the new one run on the same machine in the same session,
  and the result is reported as their ratio, not asserted. Timings taken here only
  compare.

## Size

`evaluator.py` and `exceptions.py` become upstream's. The fork's diff from upstream is
step 2 and the three fixes, about 30 lines in all. Two lines go from `__init__.py`.
The tests above add about 150 lines.
