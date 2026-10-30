# Frozen globals as values

A queued shot keeps the values its globals had at Engage, and its file is written
from them, whether it is compiled eagerly or lazily.

## Why

Engage evaluates the globals and expands them into shots. `get_frozen_globals`
queues each shot with its globals as text: a scanned global as `value_expression`
of its value, any other global that is not JIT as its expression. The compile
(`prepare_queue_shot`, then `get_queue_compile_globals`) reads the globals files
again, puts that text in place of their expressions, and evaluates every global
again. Eager and lazy compiles both go this way, and three faults follow:
- **Values that do not come back.** An empty multidimensional array loses its
  shape, a structured dtype is refused, and nan and inf need pylab's names.
- **Globals evaluated per shot.** A global that is not scanned is evaluated again
  for each shot, with the scanned globals' single values; Engage, as upstream does,
  evaluates it once for the sequence. With an outer `x = np.array([1, 2])`,
  `y = 2*x` is `[2, 4]` at Engage but 2 and 4 in the files. `n = len(x)` engages,
  and then every shot fails to compile.
- **The wrong record.** The file's `/globals` record holds the frozen text, with no
  expansion, in place of the user's expressions: `x` is recorded as `np.int64(1)`,
  not `np.array([1, 2])` with `outer`. lyse's `Run.get_globals_raw` and
  `Run.get_globals_expansion` read that record, and so does runmanager when it opens
  a shot file as a globals file. labscript, runviewer, labscript-agents and
  labscript-optimization read only the values, through
  `labscript_utils.shot_utils.get_shot_globals`.

## Requirements

1. A queued shot keeps as values the globals Engage gave it: every scanned global,
   and every other global that is not JIT and does not depend on a JIT global,
   directly or through other globals. These are the values `expand_globals` gives the
   shot, so a global that is not scanned keeps one value for the whole sequence, as
   in upstream's files.
2. The compile writes those values to the file unchanged. It evaluates only the
   other globals, each with the kept values in its namespace: the JIT globals, from
   their expressions in the globals file at compile time, and the globals that depend
   on them, from their expressions at Engage.
3. The file's `/globals` group records the expressions, units and expansions of the
   globals as they were at Engage, with each JIT global's expression as compiled.
   Eager and lazy shots from one Engage record the same.
4. The saved queue keeps a row's values only until its file is written. For a row
   not yet compiled, they are saved in `runmanager.toml` as one pickle, in base64
   text.
5. A restored row not yet compiled whose values do not load is refused: it leaves
   the queue, the output box names its shot file, and the other rows restore.
6. `submit_shots` passes the values it is given to the evaluation as evaluated
   objects, not as text.
7. `set_values`, `set_scans` and `submit_shots` write a value into a globals file as
   `value_expression`'s text. A value whose text does not evaluate back to it is
   refused, naming the global, and nothing is written. The caller can pass
   `raw=True` with an expression of its own.

## The proposal, point by point

1. **Confirmed for scanned globals, corrected for the rest.** A global that is not
   scanned is kept as its value too (requirement 1), since evaluating its expression
   again per shot is the second fault. Only JIT globals and the globals that depend
   on them are evaluated at compile, so a global derived from a JIT global still
   follows it, as it does today.
2. **Confirmed.** pickle, not dill: every value a shot keeps must become an HDF5
   attribute, and every such value pickles: str, bytes, numbers, numpy scalars and
   arrays, structured ones included. `runmanager.toml` is the user's own file, which
   runmanager already trusts as it does the globals namespace file it runs.
3. **Confirmed, for both modes.** Today neither mode records the user's expressions
   (the third fault). The record's readers are lyse's `get_globals_raw` and
   `get_globals_expansion`, which upstream documents, and runmanager reading a shot
   file's globals. A search of every repository's CodeHealth, tests included, found
   no other reader.
4. **Keep `value_expression`.** Upstream's `repr` under `np.printoptions` writes
   `array(...)`, a pylab name that upstream's own #163 deprecates and that fails with
   `pylab_namespace = False`. `value_expression` needs only `np`. Its gaps become
   refusals (requirement 7). The check evaluates the text with only `np` in the
   namespace, and compares type, dtype, shape and values, with nan equal to nan.
5. **No code for the old form.**
   - Development's saved rows hold `frozen_globals` as `repr` text under
     `np.printoptions`. CodeHealth's hold it as `value_expression` text. Production's
     rows are bare paths, which are already compiled.
   - A compiled row needs no values, so it restores as it is.
   - An uncompiled row from either branch has no values that load, so requirement 5
     refuses it.

## Architecture

- **`evaluator.py`:** `_evaluate_globals` takes `frozen`, a dict from name to value.
  Those names go into the namespace as given and are not evaluated. In runmanager,
  "values" means the Value column's text, so the keyword is named after the
  record's `frozen_globals`. The evaluator's `_namespace()` builds the only
  namespace, so no other mechanism can do this. This adds a few
  lines to upstream's file, and `evaluate_globals` is unchanged.
- **`expander.py`:**
  - `get_shots_with_frozen_globals(globals_details, expansion_order=None,
    frozen=None)` returns, from one evaluation, each shot's globals with the values
    it keeps (requirement 1), and the sequence globals at Engage.
  - `get_frozen_globals` returns values.
  - `value_expression` gains the round-trip check.
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
    `prepare_queue_shot` passes both on. `sequence_globals` has `get_globals`' shape,
    `{group: {name: (expression, units, expansion)}}`, as the name means everywhere
    in runmanager and upstream.
  - `submit_shots` passes its entry as `frozen`, and writes text only into the
    record and the file.
- **`queueing.py`:**
  - `get_save_data` writes an uncompiled row's `frozen_globals` as a base64 pickle,
    and leaves them out for a compiled row. lyse keeps base64 text in its app config
    the same way, for its routine layout. `labscript_utils.properties`' JSON and
    base64 is for HDF5 attributes, and keeps neither dtype nor shape.
  - `restore_save_data` loads them. It refuses a row whose values fail to load, and
    reports it as it reports a setting it does not know.
- **Docs:** `usage.rst` says that a queued shot keeps its values.

Upstream's, unchanged: the shot file's layout, with upstream's `/globals` record
restored; `evaluate_globals`; `expand_globals`; `make_single_run_file`.

## Tests

One per behaviour, through the public surface. Each must fail when its behaviour
breaks.
1. An outer scan over an empty `(0, 3)` array, a structured array and `[nan, inf]`,
   with `pylab_namespace = False`: each shot's file, eager and lazy, holds the value
   from Engage, with the same type, dtype, shape and values.
2. Those shots' uncompiled rows go through `get_save_data`, the TOML writer and
   reader of `runmanager.toml`, and `restore_save_data`, and are then compiled: the
   files hold the same values.
3. A saved uncompiled row whose values do not load is refused, naming its shot file,
   and the row after it restores.
4. Eager and lazy shots from one Engage, with the globals file edited between Engage
   and the lazy compile: both files' `/globals` record the expressions and expansions
   at Engage, and both hold the same values.
5. With an outer `x`, `y = 2*x` and `n = len(x)` compile with the values Engage gave
   them.
6. Kept: the JIT test, which also checks that a global derived from the JIT one
   follows it.
7. `set_values` of a value whose text does not come back is refused, naming the
   global, and writes nothing.

`FrozenGlobalsTests` in `test_scans.py` tests `value_expression` through
`get_frozen_globals`. It moves to `set_values`. `test_frozen_globals.py`'s
text round trip is replaced by test 1.
