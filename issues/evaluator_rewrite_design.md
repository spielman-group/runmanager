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

In `runmanager/evaluator.py`, as upstream has it:
- `_global_references(table)` walks a `symtable` (872a36d).
- `_evaluate_globals` flattens the groups as now, then compiles each expression
  once. It keeps the code object and the expression's references, and takes the
  dependencies as the references that are globals.
- `graphlib.TopologicalSorter` orders the globals. On a `CycleError`, the cycle's
  globals get the ValueError and leave the graph, and sorting goes on.
- The expressions are evaluated in that order, in one namespace dict. A global whose
  dependency failed gets the NameError instead of being evaluated.
- The zip-length and other-axes checks run after, on the static hierarchy.

`TraceDictionary` goes. Upstream removed it too, and nothing outside runmanager
uses it, but it is a public name of `runmanager` (in `__all__`). It leaves
`__all__`, as upstream's has.

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

- The guesser, `ExpansionError`, the frozen globals and the queue's compile path.
- JIT globals are still evaluated at compile, with the frozen globals as overrides,
  through the same function.

## Size

About 120 lines change in `evaluator.py`; upstream's form of the function is taken
whole. Two lines go from `__init__.py`. A test file covers requirements 2 to 8.
