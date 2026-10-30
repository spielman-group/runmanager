# runmanager deep pass

The code-health campaign's record for runmanager (`issues/code_health_campaign.md`
in the meta repo), on `CodeHealth`: the decisions that hold, and what was left as it
is on purpose. It is deleted when the pass is merged. Each step's full record, with
its candidates and evidence, is in history at `cd9f171`.

## Ranges

Each step's commits on `CodeHealth`, as `git log --first-parent A..B`. The whole
campaign is `57d3d21..` this file's commit. `7ede9b5` merges seven of Development's
lab commits, which are not the campaign's; `--first-parent` leaves them out.

| Step | Range | Also |
| --- | --- | --- |
| 1, contract | `57d3d21..72baf75` | |
| 2.1, behaviour no one asked for | `72baf75..1e80564` | reviewed as `7ede9b5..86de3e4` |
| 2.2, duplication | `1e80564..e73b137`, but for `c411689` | reviewed as `0b324ce..3f569c1`, `bb8b2cd..3c42c11`, `c411689..bee15b7` |
| 2.3, root causes | `e73b137..8b10b9f` | `c411689`, its list |
| 2.4, dead code | `8b10b9f..c36421f` | |
| 2.5, documents | `c36421f..6dd3dfd` | corrections `2034163`, `fff4f32` |
| Upstream stage | `6dd3dfd..1e5fae1` | `519e2ed`, the tests' error windows |
| Upstream revisit | `fff4f32..fc29258` | |
| Compatibility audit | `fc29258..bdad81a` | |
| Evaluator | design `bdad81a..6b08931`, build `6b08931..ac7eacc` | `d9f3d64`, the build's record |
| 2.6, style | `d9f3d64..c6e4385` | |
| Whole-campaign review | `454a2fb..90f4eb7` | |
| Records | `90f4eb7..9fbc677` | the frozen-globals design and its removal |
| Upstream port | `9fbc677..` this file's commit | upstream `d5842a2..057e6f8` |

## Decisions that hold

- **Kept though small or unused** (Ian): `skip_missing` on `set_scans` and
  `set_expansions`, one signature for the three setters; `submit_shots` with no
  entries answers `[]`; `drop_overtaken_default_shots`, which drops a default shot
  that real work got in front of.
- **An empty expansion on a scanned global** is saved as `outer`
  (`globals_file._normalise_record`), so `set_expansions({'x': ''})` on one reads
  back as `outer`. Ian left the behaviour, and the docstrings say so.
- **Guards that stay with their cause:**
  - `queue_exchange` answers an exception rather than raising it, since blacs takes
    any raise for an unreachable runmanager and resends for ever.
  - `QueueController.shot_finished` ignores a repeated outcome: BLACS resends an
    outcome whose reply was lost, and the exchange carries no token to tell a resend
    from a second failure.
  - The reclaim of a row still marked running: BLACS asks again only when it has
    lost that row.
  - The locks (`globals_file._file_lock`, `compiler_lock`, `_default_shot_lock`,
    `QueueController._lock`), each over real concurrent use.
  - A bare path in a saved queue restores as a compiled eager row, since
    Production saves its queue as bare paths.
  - The remote setters' type checks, the refusal of a remote set of an HDF5 globals
    file, and the refusal of a remote group activation that leaves two active groups
    of one name (Ian).
- **Names:** `queue_exchange` stays out of `runmanager.remote`. The client's `path`
  stays; `globals_file.py` says `globals_file`, `group_name` and `global_name`.
  `run_no` stays inside runmanager, beside `run_number` in the receipt and
  `submit_shots`.
- **Removed upstream names stay out** (Ian): `runmanager.new_globals_file`,
  `get_grouplist`, `get_globalslist`, `copy_group`, `delete_group`, `delete_global`,
  `get_value`, `set_value`, `get_units`, `set_units`, `get_expansion`,
  `set_expansion`, `add_expansion_groups`, `dict_diff`, and `remote.set_globals`.
  `runmanager.globals_file` and `set_values` do their jobs.
- **Public though unused:** `compile_multishot_async`, upstream's compile API;
  `functions.drop_times`, which a globals file may name; every
  `RunmanagerServer.handle_*`, reached by its command name from the client.
- **Docs:** `usage.rst` holds what `LAZY_COMPILE.md` held.
- **Frozen globals:** Ian stopped the redesign of how globals are resolved, since it
  gives no clear benefit in the lab today. The faults it was to fix, Codex's
  whole-campaign items 1 to 3 among them, are in `KNOWN_ISSUES.md`. The design is in
  history at `ee684f4`.

## Upstream

`master` is upstream's `057e6f8`. CodeHealth takes upstream's fixes in upstream's
form, each commit naming its upstream commit: the zip checks, the small bugs, the
compiler child, the parse races, the module split, #163 with 0a0ceb7 and 8ae14c0,
interrupting the compile (d053c40), and the style, lint, theme and release commits on
the lines the fork shares. The evaluator is upstream's, 2d8129d and 872a36d, with the
fork's four fixes (73650a9, 193856a, 9884cbd, ac7eacc), which go upstream as one
pull request from `UpstreamEvaluator`. The evaluator's design and build record are in
history at a9078eb.

- **Port of `d5842a2..057e6f8`:** each commit picked with `-x`, in order. Where the
  fork differs, the pick says how in its message, and a fork commit follows:
  - 77dc943 keeps `requires-python` at 3.13 and no `__version__.py`, since the version
    comes from labscript-utils; e4a4180 drops the 3.11 and 3.12 classifiers.
  - 0adb3a8 lists the fork's PyQt6 inventory; fa4069c adds `runmanager.client` and
    `runmanager.globals_file`, the fork's public modules, to the API reference.
  - 4513df4 is merged into the fork's usage page, and deletes upstream's 2013 manual;
    4c174cc corrects the fork's own text the same way, and says the automatic zip
    group is named after the alphabetically first global, not by dictionary hashes.
  - 4cdda6b's docstring was already right, on `client.py`; e08a576 says globals file,
    not HDF file, in the groups tab's tooltips.
  - f03d7b3, a0f4b7a, 188cd85, 1cf5844, d1e73df and 2ca0300 are ported onto the
    fork's tree of globals, queue and `exec_qapplication`. ae0dbfc drops a deleted
    view from f03d7b3's click-off filter, which otherwise raises on every click once
    a view's wrapper outlives it; upstream has the same fault.
- **Already in:** a844749, 32bead0, 3a7b271, 1035d06, 27d61e3 and 8e7d7f7, upstream's
  style commits whose lines the fork has changed or removed.
- **Deferred by Ian:** the custom and automatic expansion settings (a658642 and its
  follow-ups, 7528a82), the value history (9c5d2bc) and the Bool toggle (076fbf8).
- **Not possible:** 9ff38d5, since the fork writes no HDF5 globals file; b42e389,
  since Empty queue does its job; dc0d18f and 357376e, and d0aab69's images,
  upstream's screenshots; and the style hunks on lines the fork has changed.
- **Offered upstream:** fef75a0 on `UpstreamBugFixes`, on `057e6f8`, which starts
  runviewer with zprocess's `start_daemon`, the one fork cleanup that stands in the
  way of upstream's form, with three small fixes; upstream's 4cdda6b made a fourth.

## Compatibility

Kept, as upstream's own form has them: #163's deprecated pylab names, with their
FutureWarning; `get_shot_globals`' FutureWarning; `functions.drop_times`;
`__init__.py`'s re-exports and `__all__`; `runmanager.remote` over `client.py`; the
client sending `preserve_comments` only when it is False.

Kept by Ian, each keeping a user's saved file working: legacy HDF5 globals files,
read-only and converted to TOML on the first edit; a saved queue of bare paths; saved
queues and configurations from before the campaign's fields, filled with defaults;
and `_normalise_record`'s format defaults for older or hand-edited TOML files.
Frozen `array(...)` text in rows saved before 1006d75 has no code of its own: it
compiles while `pylab_namespace` is True.

## Left as it is

- `evaluator.py` and `exceptions.py` keep upstream's text, so the evaluator differs
  from upstream only by the fork's fixes. `functions.py`, `globals_diff.py`,
  `remote.py` and `client.py` (upstream's `remote.py` renamed) are upstream's files
  and get no banner.
- `analysis_submission.py` is upstream BLACS's module, moved, under its banner.
- `__main__.py`'s `qapplication` is bound only when the module runs as `__main__`;
  only an import without a run, which no test clicks, reaches the two click
  handlers that read it unbound.
- `copy_group` sets `source_globals_file = dest_globals_file` only when they are
  already equal, a line that does nothing.

## Deferred

- New screenshots for `usage.rst`, taken from the running app after the coupled
  merge (Ian, P12).
- Single-use helpers, left by the style pass, which changed forms only: `__main__.py`'s
  `setup_*` methods, `can_use_alternate_submission_mode`, `reindex_run_file_infos`,
  `swap_value_items` and `test_compile`; `queueing.py`'s `_sent_to_blacs_row`,
  `track_unqueued` and `drop_overtaken_default_shots`; `differ.py`'s `_shortened`;
  `widgets.py`'s `mix_colors`; `globals_file.py`'s `new_globals_file` and
  `get_field`.
- Test gaps: no queueing test sees a lyse outcome arrive before its row retires, the
  remote tour never reaches a failed compile, and no test sees the ported window
  behaviour: the click-off clearing, the add rows sorted last and the output tab's
  error icon.

## Lab checks

On Development, on Windows: a shot sent to runviewer with View shot(s) ticked starts
runviewer through `start_daemon` with `CREATE_NEW_PROCESS_GROUP` and
`CREATE_NO_WINDOW`. It should open its window with no console.
