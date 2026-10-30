# Globals evaluated per shot, and kept as values

In the expression of a global that is not scanned, a scanned global has its value in
the shot, not its whole list. A queued shot keeps the values its Engage gave it, and
its file is written from them, whether it is compiled eagerly or lazily.

## Why

A user scans a global to find its setting, then sets the one value found as its
default. The expressions of the other globals are written for that one value, so in
them the scanned global means its value in the shot. numpy broadcasts most
operations, which hides the difference until an expression does not broadcast.

Today, Engage evaluates every global once for the sequence, with each scanned global
holding its whole list. The compile (`prepare_queue_shot`, then
`get_queue_compile_globals`) reads the globals files again and evaluates every global
again for each shot, with each scanned global's value frozen as text. Eager and lazy
compiles both take this path. Three faults follow:
- **Engage and the compile disagree.** With an outer `x = np.array([1, 2])`,
  `y = 2*x` is `[2, 4]` at Engage but 2 and 4 in the files. `n = len(x)` is 2 at
  Engage, and then every shot fails to compile.
- **Values that do not come back.** An empty multidimensional array loses its shape,
  a structured dtype is refused, and nan and inf need pylab's names.
- **The wrong record.** The file's `/globals` record holds the frozen text, with no
  expansion, in place of the user's expressions: `x` is recorded as `np.int64(1)`,
  not `np.array([1, 2])` with `outer`. lyse's `Run.get_globals_raw` and
  `Run.get_globals_expansion` read that record, and so does runmanager when it opens
  a shot file as a globals file. labscript, runviewer, labscript-agents and
  labscript-optimization read only the values, through
  `labscript_utils.shot_utils.get_shot_globals`.

## Terms

- A **scanned** global has an expansion: in runmanager, its Scan? box is ticked;
  upstream, its expansion setting, automatic or chosen, is not empty. A JIT global is
  never scanned. This is wider than `expander.scanned_globals`, which returns only
  the globals that lead an axis, leaving out one zipped with the global it is derived
  from.
- A **per-shot** global is not scanned, and depends on a scanned global, directly or
  through other globals.

## Requirements

1. Engage evaluates every global that is not per-shot once for the sequence, as now.
   In a scanned global's expression, a scanned global it refers to has its whole
   list, so a scanned global derived from another is zipped with it as now. The
   scanned globals are expanded into shots as now: the shot count, the axes, their
   order and shuffling do not change.
2. Engage evaluates each per-shot global once per shot, after the expansion, with
   each scanned global holding its value in that shot. A global that is not per-shot
   keeps its one value, so a random one is drawn once for the sequence, as now.
3. A scanned global that depends on a per-shot global is an error naming both, since
   its values make an axis before any shot exists. This replaces the error for a zip
   group global that depends on another axis through globals that are not scanned. A
   zip group global that refers directly to a global on another axis is still an
   error.
4. Engage refuses a sequence in which a per-shot global fails in any shot. Its
   message names each failing global, its error, and the scanned values of the
   first shot it fails in. The globals tabs evaluate each per-shot global in the
   first shot only, with each scanned global at its first value: its cell shows that
   value, or its error in red, and its tooltip says which shot the value is for.
5. runmanager's expansion guesser changes only the expansions of scanned globals, as
   now, so it never puts a per-shot global on an axis. Upstream, a global that depends
   on a scanned global gets no automatic expansion. Evaluated per shot, it follows its
   scanned globals, which is what putting it on their axis did for an expression that
   broadcasts. A derived list whose expression needs the whole list, such as
   `[a**2 for a in amplitude]`, needs a chosen expansion, as in runmanager it needs
   Scan?. A global derived from globals on several axes is per-shot, and no longer an
   error.
6. A queued shot keeps as values every global its Engage gave it, except the JIT
   globals and the globals that depend on a JIT global through globals that are not
   scanned.
7. The compile writes the kept values to the file unchanged. It evaluates only the
   other globals, with the kept values in the namespace: the JIT globals, from their
   expressions in the globals file at compile time, and the globals that depend on
   them, from their expressions at Engage. A file therefore holds its Engage's values,
   and only JIT globals and their dependants are new.
8. The file's `/globals` group records the expressions, units and expansions of the
   globals as they were at Engage, with each JIT global's expression as compiled.
   Eager and lazy shots from one Engage record the same.
9. The saved queue keeps a row's values only until its file is written. For a row
   not yet compiled, they are saved in `runmanager.toml` as one pickle, in base64
   text. A restored row not yet compiled whose values do not load is refused: it
   leaves the queue, the output box names its shot file, and the other rows restore.
10. `submit_shots` passes the values it is given to the evaluation as evaluated
    objects, not as text. It makes one shot, so it has no per-shot globals.
11. `set_values`, `set_scans` and `submit_shots` write a value into a globals file as
    `value_expression`'s text. A value whose text does not evaluate back to it is
    refused, naming the global, and nothing is written. The caller can pass
    `raw=True` with an expression of its own.

## Cost

Engage evaluates each per-shot global once for each of its N shots, beside the N shot
dicts it already makes; the globals that are not per-shot are evaluated once. The
globals tabs evaluate a per-shot global once per parse, in the first shot, since a
parse runs after every edit. The compile evaluates only JIT globals and their
dependants, where it now evaluates every global for each shot.

## Choices made

1. **Kept values, not text.** pickle, not dill: every value a shot keeps must become
   an HDF5 attribute, and every such value pickles: str, bytes, numbers, numpy scalars
   and arrays, structured ones included. `runmanager.toml` is the user's own file,
   which runmanager already trusts as it does the globals namespace file it runs.
2. **The user's expressions in `/globals`, for both modes.** The record's readers are
   lyse's `get_globals_raw` and `get_globals_expansion`, which upstream documents,
   and runmanager reading a shot file's globals. A search of every repository's
   CodeHealth, tests included, found no other reader.
3. **`value_expression` for writing values into globals files.** Upstream's `repr`
   under `np.printoptions` writes `array(...)`, a pylab name that upstream's own #163
   deprecates and that fails with `pylab_namespace = False`. `value_expression`
   needs only `np`. Its gaps become refusals (requirement 11). The check evaluates the
   text with only `np` in the namespace, and compares type, dtype, shape and values,
   with nan equal to nan.
4. **No code for the old saved form.**
   - Development's saved rows hold `frozen_globals` as `repr` text under
     `np.printoptions`. CodeHealth's hold it as `value_expression` text. Production's
     rows are bare paths, which are already compiled.
   - A compiled row needs no values, so it restores as it is.
   - An uncompiled row from either branch has no values that load, so requirement 9
     refuses it.

## Upstream

Requirements 1 to 5 are an upstream bug fix, in one pull request of its own against
upstream's `evaluator.py`, `expander.py`, `group_manager.automatic_expansions` and
`usage.rst`. Requirements 6 to 11 are the fork's queue and lazy compile, which
upstream does not have.
- Upstream documents the model this replaces: "Evaluation of globals" in `usage.rst`
  says each global is evaluated once for the whole sequence, and rule 2 of the
  automatic expansions zips a derived list with its source. The pull request rewrites
  both, and gives the reason in **Why**. A quantity that depends on several axes,
  which upstream's documentation tells the user to compute in the experiment script,
  becomes a per-shot global.
- It is written against upstream's code, not ported from the fork's: upstream's
  zip group check uses `expander.axis_names`, and `automatic_expansions` is upstream's
  alone, since runmanager's `guess_expansion_modes` already skips globals that are not
  scanned.
- The four-fix evaluator pull request changes how `_evaluate_globals` finds each
  global's references. This one moves that code into a function of its own, adds the
  per-shot pass and changes the zip group check. Each applies without the other,
  though both touch `_evaluate_globals`, so the second to merge is rebased on the
  first.

## Architecture

- **`evaluator.py`:**
  - The code of `_evaluate_globals` that compiles each expression, finds its
    references and orders the globals becomes one function, which `_evaluate_globals`
    and `expand_globals` both call.
  - Its evaluation loop (eval, `iterator_to_tuple`, catching the error) becomes one
    function too, used for the globals evaluated once, for the first shot, and for
    each shot in `expand_globals`.
  - The per-shot globals are those not scanned whose `_reachable` set in the
    hierarchy holds a scanned global. `_reachable` also replaces the zip group check's
    `expanded_dependencies` and `scanned_globals`' own `dependencies`.
  - `_evaluate_globals(sequence_globals, raise_exceptions=True, frozen=None)` leaves
    the per-shot globals out of the evaluation in order. It then evaluates them in the
    first shot, whose scanned values are the first of each scanned global's values,
    so that its results have a value or an error for every global. It does this
    itself, since `expander` imports `evaluator` and not the other way round. With no
    shots, they hold an error saying that the scan has none.
  - It gives a scanned global that depends on a per-shot global the error of
    requirement 3.
  - `frozen`, a dict from name to value, is the fork's: those names go into the
    namespace as given and are not evaluated. `evaluate_globals` does not take it.
- **`expander.py`:**
  - `expand_globals` evaluates the per-shot globals for each shot it makes. It finds
    them, and their order, with the evaluator's function for references, so its
    signature is unchanged and upstream's scripts get per-shot values. It loads the
    namespace once, not once per shot. It raises one error naming each per-shot
    global that fails, its error, and the first shot it fails in (requirement 4).
  - `get_shots_with_frozen_globals(globals_details, expansion_order=None,
    frozen=None)` returns each shot with the values it keeps (requirement 6), and the
    sequence globals at Engage. `get_frozen_globals` returns values.
  - `value_expression` gains the round-trip check.
- **`group_manager.py`** (upstream only): `automatic_expansions` gives a global that
  depends on a scanned global no automatic expansion (requirement 5).
- **`compiler.py`:** `get_queue_compile_globals(groups, frozen=None,
  sequence_globals=None, default_globals=False)`.
  - With `frozen`, it takes the JIT expressions from the files of `groups`, and
    evaluates the globals that `frozen` does not hold, with `frozen` passed on. It
    returns the `/globals` record and the shot.
  - Without `frozen`, it works as now, for a default shot and for
    `make_run_file_from_globals_files`.
- **`__main__.py`:**
  - Engage, `test_compile` and `submit_shots` put `frozen_globals` (values) and
    `sequence_globals` (the expressions at Engage) on each record, and
    `prepare_queue_shot` passes both on. `sequence_globals` has `get_globals`'
    shape, `{group: {name: (expression, units, expansion)}}`.
  - The tab's tooltip for a per-shot global says which shot its value is for.
- **`queueing.py`:**
  - `get_save_data` writes an uncompiled row's `frozen_globals` as a base64 pickle,
    and leaves them out for a compiled row. lyse keeps base64 text in its app config
    the same way, for its routine layout. `labscript_utils.properties`' JSON and
    base64 is for HDF5 attributes, and keeps neither dtype nor shape.
  - `restore_save_data` loads them. It refuses a row whose values fail to load, and
    reports it as it reports a setting it does not know.
- **Docs:** `usage.rst`'s "Evaluation of globals" states the per-shot model, and
  `evaluate_globals`' docstring says which globals are evaluated per shot. `usage.rst`
  also says that a queued shot keeps its values.

Unchanged: the shot file's layout, with upstream's `/globals` record restored;
`evaluate_globals`' signature and return; `make_single_run_file`; the shot count and
the queue view, whose rows are shot files as now. A shot name template that names a
per-shot global gets its value in that shot.

## Tests

One per behaviour, through the public surface. Each must fail when its behaviour
breaks.
1. With an outer `x = np.array([1, 2])`: `y = 2*x` is 2 and 4 in the shots that
   Engage makes and in their files, eager and lazy; `n = len(x)` refuses Engage,
   naming `n`.
2. With an outer `x = [0, 1, 2]`, `r = 1/(x - 1)` refuses Engage, naming `r` and the
   shot with `x = 1`.
3. A scanned global that refers to a per-shot global is an error naming both.
4. With outer `a` and `b`, `p = a*b` is each shot's product.
5. A scanned global derived from another is zipped with it and has its whole list in
   its expression, as now.
6. An outer scan over an empty `(0, 3)` array, a structured array and `[nan, inf]`,
   with `pylab_namespace = False`: each shot's file, eager and lazy, holds the value
   from Engage, with the same type, dtype, shape and values.
7. Those shots' uncompiled rows go through `get_save_data`, the TOML writer and
   reader of `runmanager.toml`, and `restore_save_data`, and are then compiled: the
   files hold the same values.
8. A saved uncompiled row whose values do not load is refused, naming its shot file,
   and the row after it restores.
9. Eager and lazy shots from one Engage, with the globals file edited between Engage
   and the lazy compile: both files' `/globals` record the expressions and expansions
   at Engage, and both hold the same values.
10. Kept: the JIT test, which also checks that a global derived from the JIT one
    follows it.
11. `set_values` of a value whose text does not come back is refused, naming the
    global, and writes nothing.

`FrozenGlobalsTests` in `test_scans.py` tests `value_expression` through
`get_frozen_globals`. It moves to `set_values`. `test_frozen_globals.py`'s text round
trip is replaced by test 6. Upstream's pull request carries tests 1 to 5 as tests of
`evaluate_globals` and `expand_globals`, with a test of requirement 5's automatic
expansions.
