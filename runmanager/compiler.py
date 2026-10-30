#####################################################################
#                                                                   #
# /compiler.py                                                      #
#                                                                   #
# Copyright 2013, Monash University                                 #
#                                                                   #
# This file is part of the program runmanager, in the labscript     #
# suite (see http://labscriptsuite.org), and is licensed under the  #
# Simplified BSD License. See the license.txt file in the root of   #
# the project for the full license.                                 #
#                                                                   #
#####################################################################
"""Prepares labscript shots for compilation: sequence details, run files and globals.

Compilation itself takes place in batch_compiler.py, in a subprocess that
start_compiler starts.
"""

import datetime
import errno
import json
import os
import random
import re
import sys
import threading
import traceback
import uuid

import labscript_utils.h5_lock
import h5py
import numpy as np

from labscript_utils.labconfig import LabConfig
from labscript_utils.ls_zprocess import ProcessTree, zmq_push_multipart
from labscript_utils.file_utils import next_available_indexed_filepath
from labscript_utils.lookup_format import format_lookup_string

from runmanager import globals_file
from runmanager.evaluator import evaluate_globals
from runmanager.expander import expand_globals
from runmanager.group_manager import _details_to_sequence_globals, get_all_groups

process_tree = ProcessTree.instance()


def next_sequence_index(shot_basedir, dt, increment=True):
    """Return the next sequence index in ``shot_basedir`` for the date of ``dt``.

    ``shot_basedir`` is ``<experiment_shot_storage>/<script_basename>``. With
    ``increment`` the index is claimed, by incrementing it atomically on disk, and
    must be claimed before it is used. Without it the result is indicative only, as
    other code may use the index at any time."""
    from labscript_utils.ls_zprocess import Lock
    from labscript_utils.shared_drive import path_to_agnostic

    DATE_FORMAT = '%Y-%m-%d'
    # The file where we store the next sequence index on disk:
    sequence_index_file = os.path.join(shot_basedir, '.next_sequence_index')
    # Open with zlock to prevent race conditions with other code:
    with Lock(path_to_agnostic(sequence_index_file), read_only=not increment):
        try:
            with open(sequence_index_file) as f:
                datestr, sequence_index = json.load(f)
                if datestr != dt.strftime(DATE_FORMAT):
                    # New day, start from zero again:
                    sequence_index = 0
        except OSError as exc:
            if exc.errno != errno.ENOENT:
                raise
            # File doesn't exist yet, start from zero
            sequence_index = 0
        if increment:
            os.makedirs(os.path.dirname(sequence_index_file), exist_ok=True)
            with open(sequence_index_file, 'w') as f:
                json.dump([dt.strftime(DATE_FORMAT), sequence_index + 1], f)
        return sequence_index


def _sequence_format(config, option, fallback, default=False):
    """The labconfig's ``[runmanager]`` format ``option``, or ``fallback``.

    With ``default``, as for a default shot, its sequence-index field becomes the
    literal ``default``."""
    template = config.get('runmanager', option, fallback=fallback)
    if default:
        template = re.sub(r'\{sequence_index(?::[^}]*)?\}', 'default', template)
    return template


def new_sequence_details(
    script_path,
    config=None,
    increment_sequence_index=True,
    default=False,
    subdirectory_in_prefix=False,
):
    """Return a new sequence's ``(sequence_attrs, output_dir, filename_prefix)``.

    ``sequence_attrs`` holds the top-level attributes for the shot files:
    script_basename, sequence_date, sequence_index and sequence_id. The output
    directory and filename prefix follow the labconfig settings. With
    ``increment_sequence_index`` the sequence index is claimed, so that nobody else can
    use it: do this when the details are about to be used to compile a sequence.
    Otherwise the results are indicative only, and the function should be called again
    with it set before compiling, as other code may use the index in the meantime.

    With ``default=True`` these are the details of the day's default sequence, which
    every default shot made that day shares: dated from the start of the day, it has
    sequence_index -1 and claims no index.

    With ``subdirectory_in_prefix=True`` the output directory is the script's storage
    folder alone, and the subdirectory the folder format makes leads the filename
    prefix, so that make_run_files formats it with each shot's globals and never
    formats the folder."""
    if config is None:
        config = LabConfig()
    script_basename = os.path.splitext(os.path.basename(script_path))[0]
    shot_storage = config.get('default', 'experiment_shot_storage')
    shot_basedir = os.path.join(shot_storage, script_basename)
    now = datetime.datetime.now()
    if default:
        now = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # With the UTC offset in force at that time, which a default sequence's midnight
    # may not share with now:
    now = now.astimezone()
    if default:
        sequence_index = -1
    else:
        sequence_index = next_sequence_index(shot_basedir, now, increment_sequence_index)
    sequence_timestamp = now.strftime('%Y%m%dT%H%M%S')

    # Toplevel attributes to be saved to the shot files:
    sequence_date = now.strftime('%Y-%m-%d')
    # The time with its UTC offset, so that a time in the hour that repeats as daylight
    # saving time ends is unambiguous:
    sequence_id = now.strftime('%Y%m%dT%H%M%S%z') + '_' + script_basename

    sequence_attrs = {
        'script_basename': script_basename,
        'sequence_date': sequence_date,
        'sequence_index': sequence_index,
        'sequence_id': sequence_id,
    }
    context = dict(
        sequence_attrs,
        sequence_timestamp=sequence_timestamp,
        globals={},
    )

    # Dated at the sequence's start, for all its shots; each shot fills in only globals:
    subdir_format = _sequence_format(
        config,
        'output_folder_format',
        os.path.join('%Y', '%m', '%d', '{sequence_index:05d}'),
        default,
    )

    subdir = format_lookup_string(
        subdir_format, context, dt=now, preserve_unresolved_roots=('globals',)
    )
    shot_output_dir = os.path.join(shot_basedir, subdir)

    filename_prefix_format = _sequence_format(
        config, 'filename_prefix_format', '{sequence_timestamp}_{script_basename}', default
    )
    filename_prefix = format_lookup_string(
        filename_prefix_format, context, dt=now, preserve_unresolved_roots=('globals',)
    )

    if subdirectory_in_prefix:
        return sequence_attrs, shot_basedir, os.path.join(subdir, filename_prefix)
    return sequence_attrs, shot_output_dir, filename_prefix


def make_run_files(
    output_folder,
    sequence_globals,
    shots,
    sequence_attrs,
    filename_prefix,
    shuffle=False,
    return_infos=False,
    create_files=True,
):
    """Create the run files of ``shots``, and return a generator of what they are.

    ``sequence_globals`` is a nested dictionary of the type returned by get_globals and
    ``shots`` a list of the flat dictionaries that expand_globals returns.
    ``sequence_attrs``, as returned by new_sequence_details, is a dict of the attributes
    of this sequence to set on the file's top-level group. That function also returns
    sensible ``output_folder`` and ``filename_prefix``, which determine where the files
    go: ``output_folder`` is used as it is, and ``filename_prefix``, which may name
    subdirectories, is formatted with each shot's globals, with the run number and
    ``.h5`` appended.

    ``shuffle`` randomises the order that the run files are generated in with respect
    to which element of ``shots`` they come from. Shuffled or not, what is yielded
    comes in run number order rather than in the order of shots, and each yielded item
    describes the shot that went into it: with ``return_infos`` the info carries that
    shot's globals beside its path and run number, and without it the file at the
    yielded path holds them. So a caller that shuffles needs no separate note of where
    each element of ``shots`` ended up.

    The files are not created until you loop over the generator, which gives their
    paths. This leaves fewer unused files to clean up if compiling a labscript fails;
    convert it to a list to create them all. With ``create_files=False`` none is
    written. Raises ValueError if ``shots`` is empty."""
    if not shots:
        raise ValueError('No shots to compile: a global that is expanded over has no values.')
    return _make_run_files(
        output_folder,
        sequence_globals,
        shots,
        sequence_attrs,
        filename_prefix,
        shuffle,
        return_infos,
        create_files,
    )


def _make_run_files(
    output_folder,
    sequence_globals,
    shots,
    sequence_attrs,
    filename_prefix,
    shuffle,
    return_infos,
    create_files,
):
    indexed_shots = list(enumerate(shots))
    ndigits = int(np.ceil(np.log10(len(indexed_shots))))
    if shuffle:
        random.shuffle(indexed_shots)
    # Numbered before any file is made, since a number skipped for a file
    # already in the folder takes the sequence beyond len(shots).
    numbered = []
    run_no = 0
    for _, shot_globals in indexed_shots:
        runfilename, run_no = next_run_file(
            output_folder, filename_prefix, shot_globals, run_no, ndigits
        )
        numbered.append((runfilename, run_no, shot_globals))
        run_no += 1
    n_runs = run_no
    # The sequence globals are the same in every file, so are written once:
    template = _sequence_globals_image(sequence_globals) if create_files else None
    for runfilename, run_no, shot_globals in numbered:
        if create_files:
            _write_run_file(
                runfilename, template, shot_globals, sequence_attrs, run_no, n_runs
            )
        if return_infos:
            yield {
                'path': runfilename,
                'shot_globals': shot_globals,
                'sequence_attrs': dict(sequence_attrs),
                'run_no': run_no,
                'n_runs': n_runs,
            }
        else:
            yield runfilename


def next_run_file(output_folder, filename_prefix, shot_globals, run_no=None, width=1, taken=()):
    """The file of a sequence's next run, and its run number.

    The file is ``filename_prefix``, formatted with the shot's globals, in
    ``output_folder``, numbered with the first run number from ``run_no`` whose
    file does not exist and is not in ``taken``, zero-padded to ``width``
    digits. With no ``run_no``, numbering starts after the highest run number in
    the file's folder."""
    basename = os.path.join(
        output_folder, format_lookup_string(filename_prefix, {'globals': shot_globals})
    )
    if run_no is None:
        # One listing of the folder, not a stat per number:
        folder = os.path.dirname(basename)
        names = os.listdir(folder) if os.path.isdir(folder) else []
        ends = [os.path.splitext(name)[0].rpartition('_')[2] for name in names]
        run_no = 1 + max((int(end) for end in ends if end.isdigit()), default=-1)
    while True:
        path, run_no = next_available_indexed_filepath(
            basename + '.h5', f'_{{index:0{width}d}}', start=run_no, strip_suffix=False
        )
        if os.path.abspath(path) not in taken:
            return path, run_no
        run_no += 1


def make_single_run_file(
    filename, sequenceglobals, runglobals, sequence_attrs, run_no, n_runs, shot_id=None
):
    """Write the run file ``filename`` of one shot.

    ``runglobals`` is a dict of this run's globals, the format being that of one
    element of the list returned by expand_globals. ``sequenceglobals`` is a nested
    dictionary of the type returned by get_globals. ``sequence_attrs``, as returned by
    new_sequence_details, is identical for every run of one sequence.

    ``run_no`` is this run's number, which is unique within its sequence. ``n_runs`` is
    how far that sequence reaches as of this file: one more than the highest run number
    it has once this one is written. For a sequence compiled in one go every file
    carries the same value, which exceeds the number of runs generated if files already
    in the folder took some of their numbers. For a sequence that is added to afterwards
    it does not, as the shots written earlier keep the smaller number they were written
    with, since they may already have run. So ``n_runs`` read off one shot is not the
    size of its sequence, and the shots of one sequence need not agree on it.

    ``shot_id``, if given, is the identifier of the queue row this shot was written for,
    so that a result coming back can be matched to the shot that was submitted. A file
    written without one has no such attribute, which is how a shot that nobody
    submitted is told apart from one that was."""
    _write_run_file(
        filename,
        _sequence_globals_image(sequenceglobals),
        runglobals,
        sequence_attrs,
        run_no,
        n_runs,
        shot_id,
    )


def _in_memory_file(image=None):
    """Return a writable HDF5 file that is held in memory, optionally initialised from
    a file image."""
    fapl = h5py.h5p.create(h5py.h5p.FILE_ACCESS)
    fapl.set_fapl_core(backing_store=False)
    # The name must differ from that of any open file. A file opened from an identifier
    # is not locked with zlock.
    name = f'run_file_{uuid.uuid4()}'.encode()
    if image is None:
        return h5py.File(h5py.h5f.create(name, fapl=fapl))
    fapl.set_file_image(image)
    return h5py.File(h5py.h5f.open(name, h5py.h5f.ACC_RDWR, fapl=fapl))


def _sequence_globals_image(sequence_globals):
    """Return the image of an HDF5 file with the globals group of a run file, holding
    the sequence globals but no shot globals."""
    with _in_memory_file() as f:
        globals_group = f.create_group('globals')
        if sequence_globals is not None:
            for groupname, groupvars in sequence_globals.items():
                group = globals_group.create_group(groupname)
                unitsgroup = group.create_group('units')
                expansiongroup = group.create_group('expansion')
                for name, (value, units, expansion) in groupvars.items():
                    group.attrs[name] = value
                    unitsgroup.attrs[name] = units
                    expansiongroup.attrs[name] = expansion
        f.flush()
        return f.id.get_file_image()


def _write_run_file(filename, template, runglobals, sequence_attrs, run_no, n_runs, shot_id=None):
    directory = os.path.dirname(filename)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with _in_memory_file(template) as f:
        f.attrs.update(sequence_attrs)
        f.attrs['run number'] = run_no
        f.attrs['n_runs'] = n_runs
        if shot_id is not None:
            f.attrs['shot_id'] = shot_id
        globals_group = f['globals']
        for name, value in runglobals.items():
            if value is None:
                # Store it as a null object reference:
                value = h5py.Reference()
            try:
                globals_group.attrs[name] = value
            except Exception as e:  # noqa: BLE001 all global exceptions are value errors
                message = (f'Global {name} cannot be saved as an hdf5 attribute. ' +
                           'Globals can only have relatively simple datatypes, with no nested structures. ' +
                           'Original error was:\n' +
                           f'{e.__class__.__name__}: {e!s}')
                raise ValueError(message)
        f.flush()
        image = f.id.get_file_image()
    with open(filename, 'wb') as f:
        f.write(image)


def make_run_file_from_globals_files(labscript_file, globals_files, output_path, config=None):
    """Create the run file ``output_path`` from all the globals of ``globals_files``.

    ``labscript_file`` determines the sequence_attrs only, and a new sequence index is
    claimed for them."""
    groups = get_all_groups(globals_files)
    sequence_globals, shot_globals = get_queue_compile_globals(groups)
    sequence_attrs, _, _ = new_sequence_details(
        labscript_file, config=config, increment_sequence_index=True
    )
    make_single_run_file(output_path, sequence_globals, shot_globals, sequence_attrs, 0, 1)


def get_queue_compile_globals(groups, frozen_globals=None, default_globals=False):
    """The globals a queued shot's file is written from.

    Returns the sequence globals and the one shot's globals. ``frozen_globals``
    replaces the expressions of the globals it names. With ``default_globals``
    the rest are taken at their defaults even where a scan is set, as a default
    shot's are.
    """
    globals_details = globals_file.get_globals_details(groups)
    sequence_globals = _details_to_sequence_globals(
        globals_details, defaults_only=default_globals, overrides=frozen_globals
    )
    evaled_globals, _, expansions = evaluate_globals(
        sequence_globals, raise_exceptions=True
    )
    shots = expand_globals(sequence_globals, evaled_globals)
    if not shots:
        raise ValueError('No shots to compile: a global that is expanded over has no values.')
    if len(shots) != 1:
        scanning_globals = [name for name, expansion in expansions.items() if expansion]
        raise ValueError(
            'Cannot compile to a single run file, as these globals are a sequence: '
            + ', '.join(scanning_globals)
        )
    return sequence_globals, shots[0]


def start_compiler(stream_port=None):
    """Start a compiler subprocess, its output sent to ``stream_port``.

    Returns its ``(to_child, from_child, child)``, as
    :meth:`labscript_utils.ls_zprocess.ProcessTree.subprocess` does.
    """
    compiler_path = os.path.join(os.path.dirname(__file__), 'batch_compiler.py')
    return process_tree.subprocess(compiler_path, output_redirection_port=stream_port)


def compile_with(to_child, from_child, labscript_file, run_file):
    """Compile ``labscript_file`` into ``run_file`` in a compiler subprocess.

    Returns ``(success, error)``, ``error`` being the traceback if it failed.
    """
    to_child.put(['compile', [labscript_file, run_file]])
    signal, success, error = from_child.get()
    assert signal == 'done'
    return success, error


def compile_labscript_async(labscript_file, run_file,
                            stream_port=None, done_callback=None):
    """Compile ``labscript_file`` into ``run_file``, in a compiler subprocess.

    This function is designed to be called in a thread. The compile's stdout and stderr
    are pushed to ``stream_port`` over zmq, encrypted or not according to the security
    settings in labconfig. To receive them, use a PULL socket created from a
    :class:`labscript_utils.ls_zprocess.Context`, or a
    :class:`labscript_utils.ls_zprocess.ZMQServer`, which have those settings. When the
    compile is complete ``done_callback`` is called with whether it succeeded.

    Args:
        labscript_file (str): Path to labscript file to be compiled.
        run_file (str): Path to h5 file where compilation output is stored. This file
            must already exist with the globals written to it, as
            :func:`make_single_run_file` does.
        stream_port (int, optional): Port to push stdout and stderr to. If None, they
            go where the calling process's stdout and stderr do. Default is None.
        done_callback (function, optional): Called with a boolean marking compilation
            success or failure. If None, no callback is made. Default is None.
    """
    to_child, from_child, child = start_compiler(stream_port)
    try:
        success, _ = compile_with(to_child, from_child, labscript_file, run_file)
    finally:
        to_child.put(['quit', None])
        child.communicate()
    if done_callback is not None:
        done_callback(success)


def compile_multishot_async(labscript_file, run_files,
                            stream_port=None, done_callback=None):
    """Compile ``labscript_file`` into each of ``run_files`` in turn, in one compiler.

    This function is designed to be called in a thread. The compile's stdout and stderr
    are pushed to ``stream_port`` as for :func:`compile_labscript_async`.
    ``done_callback`` is called when each compile is complete, and compilation stops
    after the first failure. An exception is written to the stream, or to stderr if
    there is no ``stream_port``, and raised.

    Args:
        labscript_file (str): Path to labscript file to be compiled.
        run_files (list of str): Paths to h5 files where compilation output is stored.
            These files must already exist with the globals written to them.
        stream_port (int, optional): Port to push stdout and stderr to. If None, they
            go where the calling process's stdout and stderr do. Default is None.
        done_callback (function, optional): Called after each compile with a boolean
            marking its success or failure. If None, no callback is made. Default is
            None.
    """
    to_child, from_child, child = start_compiler(stream_port)
    try:
        for run_file in run_files:
            success, _ = compile_with(to_child, from_child, labscript_file, run_file)
            if done_callback is not None:
                done_callback(success)
            if not success:
                break
    except Exception:
        error = traceback.format_exc()
        if stream_port is None:
            sys.stderr.write(error)
        else:
            zmq_push_multipart(stream_port, data=[b'stderr', error.encode('utf-8')])
        raise
    finally:
        to_child.put(['quit', None])
        child.communicate()


def compile_labscript_with_globals_files_async(labscript_file, globals_files, output_path,
                                               stream_port, done_callback):
    """Compile ``labscript_file`` into ``output_path``, with the globals of the files.

    The shot file is made first, as :func:`make_run_file_from_globals_files` does, and
    then compiled in a daemon thread, so this returns without waiting for the compile.
    stdout and stderr are pushed to ``stream_port`` via zmq in the multipart message
    format `['stdout','hello, world\\n']` etc. When the compile is finished
    ``done_callback`` is called, from the thread, with whether it succeeded. If the
    shot file cannot be made, the traceback is pushed to ``stream_port`` and
    ``done_callback(False)`` is called.

    Args:
        labscript_file (str): Path to labscript file to be compiled.
        globals_files (list of str): Paths to h5 files where globals values to be used
            are stored. See :func:`make_run_file_from_globals_files` for details.
        output_path (str): Path of the shot file to write and compile into.
        stream_port (int): Port to push stdout and stderr to.
        done_callback (function): Called with a boolean marking compilation success or
            failure.
    """
    try:
        make_run_file_from_globals_files(labscript_file, globals_files, output_path)
        thread = threading.Thread(
            target=compile_labscript_async,
            args=[labscript_file, output_path, stream_port, done_callback],
            daemon=True,
        )
        thread.start()
    except Exception:
        error = traceback.format_exc()
        zmq_push_multipart(stream_port, data=[b'stderr', error.encode('utf-8')])
        t = threading.Thread(target=done_callback, args=(False,), daemon=True)
        t.start()
