# Evaluating each global once, in dependency order

A design for porting upstream 2d8129d and 872a36d onto the fork's evaluator, for
Ian's review. Nothing is built.

## Today

`evaluator._evaluate_globals` evaluates every unevaluated expression in each pass,
and repeats the passes until the number of errors stops falling. Any error might be
a forward reference.
- An expression may be evaluated many times.
- A typo and a forward reference look the same until the passes give up.
- A global that refers to itself is reported as a NameError.

`TraceDictionary` records the names an expression looked up while it evaluated, so
the dependency hierarchy holds only the names on the path taken: for
`x if flag else y`, one branch. Three things read that hierarchy:
- the expansion guesser (`guess_expansion_modes`);
- the zipped-global check (b283c3a);
- the pylab-name check (#163).

## Requirements

1. Each global's expression is compiled once and evaluated at most once per call.
2. Globals are evaluated in an order in which each comes after the globals it refers
   to.
3. The globals an expression refers to come from its symbol table. They are the
   names that are global and read, and not assigned, in the expression or in any
   lambda, comprehension or generator in it. A name that a nested scope binds is
   not a reference.
4. A cycle of references, a self-reference included, gives every global in it a
   ValueError naming the cycle.
5. A global that refers to a global holding an error gets a NameError of its own.
   No pass-by-pass guessing is involved.
6. An expression that does not compile, such as a syntax error or `yield`, holds
   its SyntaxError.
7. The hierarchy maps each global to every global it refers to, on every branch.
8. A global with the name of a namespace name, such as `pi`, is evaluated before
   the globals that refer to it, and they get the global.
9. The following are unchanged:
   - the return shapes: `evaluate_globals` returns 3 items and `_evaluate_globals` 4;
   - an error held in place of a value;
   - with `raise_exceptions`, one exception listing every failed global;
   - iterators turned into tuples;
   - a failing namespace load;
   - the ExpansionError value kept in the namespace;
   - the zip-length and other-axes checks;
   - the #163 warnings.
10. The pylab-name check reads requirement 3's references, as upstream's does.
11. A chain of 1000 globals, defined last first, evaluates in well under a second.
    Upstream measures 0.05 s, against 11.7 s for the passes.

## Architecture

Upstream's `runmanager/evaluator.py` is taken whole, from `upstream/master`, together
with its `runmanager/exceptions.py`. It already holds every upstream evaluator commit
the fork has ported one at a time, and the fork's changes then follow as commits of
their own:
- `_global_references(table)` walks a `symtable` (872a36d).
- `_evaluate_globals` compiles each expression once, keeping its code object and
  references. It orders the globals with `graphlib.TopologicalSorter`. On a
  `CycleError`, the cycle's globals get the ValueError and leave the graph, and
  sorting goes on. Then it evaluates them in that order, in one namespace dict
  (2d8129d).
- #163's namespace and deprecation checks read the symbol-table references (45baf1f).
- `ExpansionError` carries the global's value (e829c32's exception, which upstream's
  evaluator constructs).

`TraceDictionary` goes. Upstream removed it too, and nothing outside runmanager uses
it. It leaves `runmanager/__init__.py` and its `__all__`, a separate commit.

## Build

1. Replace `evaluator.py` and `exceptions.py` with upstream's, crediting 2d8129d,
   872a36d and the evaluator half of e829c32.
2. One fork commit: the zipped-global check compares the expansion settings
   themselves, not upstream's `axis_names`. The reason: `axis_names` treats an `outer`
   global and a zip group of the same name as one axis, which is part of e829c32's
   expansion-setting change that Ian deferred, and the fork's expander keeps them as
   two axes.
3. One fork commit: `TraceDictionary` leaves `__init__.py`.

The fork's other evaluator changes need no line of their own:
- The guesser reads the hierarchy as it is, now static.
- The frozen globals and JIT globals do not touch the evaluator.
- #163's checks are upstream's.

After the build, `git diff upstream/master CodeHealth -- runmanager/evaluator.py` holds
only step 2, and `runmanager/exceptions.py` does not differ.

| Upstream commit | In the build |
| --- | --- |
| 7729dc8, e06872f, 4c20a38 (split, docstrings, `iterator_to_tuple` moved) | Taken as is |
| ea20be6, 9d24a9a, a467455, 2754f37, 9a3bd5e, 65bc426 (style and lint) | Taken as is |
| 3483d09, b5c4b25, 28fc9ae, b283c3a, c110ea0 (already ported) | Taken as is |
| 2d8129d (once each, in order) | Taken as is |
| 872a36d (symbol-table references) | Taken as is |
| e829c32 (expansion settings) | `ExpansionError(message, value)` taken as is. `axis_names`, in the other-axes check, differs: step 2 |
| 45baf1f (#163) | Taken as is. It replaces the fork's check of the traced names |

## What changes for users

- Self-references and cycles show as an error naming the cycle, not as NameError.
- The guesser and the zipped-global check see every branch, so:
  - a global that refers to a scanned global only in a branch not taken is now
    zipped with it automatically;
  - a zipped global that refers to another axis only in such a branch is now
    refused.

  Upstream has behaved this way since 2d8129d.
- A pylab name in a branch not taken now gets the #163 warning.
- Evaluation is faster for long chains of globals.

## Not changing

- The guesser, the frozen globals and the queue's compile path.
- JIT globals are still evaluated at compile, with the frozen globals as overrides,
  through the same function.

## Size

`evaluator.py` and `exceptions.py` become upstream's, and the fork's diff from upstream
in them is one change of a few lines. Two lines go from `__init__.py`. A test file
covers requirements 2 to 8.
