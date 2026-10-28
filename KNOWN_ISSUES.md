# Known issues

- `__main__.py`, `RunManager.on_close_event`: closing while a default shot is being prepared leaves its file in the shot folder, since the preparation hands the shot over after the close has deleted any ready one. Deferred: waiting for it would hold the close on a compile, and deadlock on the preparation's `inmain` read of the active groups.
