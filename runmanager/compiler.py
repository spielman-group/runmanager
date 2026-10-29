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
"""Functions for managing compilation of labscript shots.
Actual compilation takes place in batch_compiler.py.
These functions prepare the sequence and globals.
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
    """Return the next sequence index for sequences in the given base directory (i.e.
    <experiment_shot_storage>/<script_basename>) and the date of the given datetime
    object, and increment the sequence index atomically on disk if increment=True. If
    not setting increment=True, then the result is indicative only and may be used by
    other code at any time. One must increment the sequence index prior to use."""
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
            # Write the new file with the incremented sequence index
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
    """Generate the details for a new sequence: the toplevel attrs sequence_date,
    sequence_index, sequence_id; and the the output directory and filename prefix for
    the shot files, according to labconfig settings. If increment_sequence_index=True,
    then we are claiming the resulting sequence index for use such that it cannot be
    used by anyone else. This should be done if the sequence details are immediately
    about to be used to compile a sequence. Otherwise, set increment_sequence_index to
    False, but in that case the results are indicative only and one should call this
    function again with increment_sequence_index=True before compiling the sequence, as
    otherwise the sequence_index may be used by other code in the meantime.

    With default=True these are the details of the day's default sequence, which every
    default shot made that day shares: dated from the start of the day, it has
    sequence_index -1 and claims no index.

    With subdirectory_in_prefix=True the output directory is the storage folder alone,
    and the subdirectory the folder format makes leads the filename prefix, so that
    make_run_files formats it with each shot's globals and never formats the folder."""
    if config is None:
        config = LabConfig()
    script_basename = os.path.splitext(os.path.basename(script_path))[0]
    shot_storage = config.get('default', 'experiment_shot_storage')
    shot_basedir = os.path.join(shot_storage, script_basename)
    now = datetime.datetime.now()
    if default:
        now = now.replace(hour=0, minute=0, second=0, microsecond=0)
        sequence_index = -1
    else:
        sequence_index = next_sequence_index(shot_basedir, now, increment_sequence_index)
    sequence_timestamp = now.strftime('%Y%m%dT%H%M%S')

    # Toplevel attributes to be saved to the shot files:
    sequence_date = now.strftime('%Y-%m-%d')
    sequence_id = sequence_timestamp + '_' + script_basename

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

    # Dated at the sequence's start, for all its shots; each shot fills in only globals.
    # Compute the output directory based on labconfig settings:
    subdir_format = _sequence_format(
        config,
        'output_folder_format',
        os.path.join('%Y', '%m', '%d', '{sequence_index:05d}'),
        default,
    )

    # Format the output directory according to the current timestamp, sequence index and
    # sequence_timestamp, if present in the format string:
    subdir = format_lookup_string(
        subdir_format, context, dt=now, preserve_unresolved_roots=('globals',)
    )
    shot_output_dir = os.path.join(shot_basedir, subdir)

    # Compute the shot filename prefix according to labconfig settings:
    filename_prefix_format = _sequence_format(
        config, 'filename_prefix_format', '{sequence_timestamp}_{script_basename}', default
    )
    # Format the filename prefix according to the current timestamp, sequence index,
    # sequence_timestamp, and script_basename, if present in the format string:
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
    """Does what it says. sequence_globals and shots are of the datatypes returned by
    get_globals and get_shots, one is a nested dictionary with string values, and the
    other a flat dictionary. sequence_attrs is a dict of the attributes pertaining to
    this sequence to be initially set at the top-level group of the h5 file, as returned
    by new_sequence_details. output_folder and filename_prefix determine the directory
    shot files will be output to, as well as their filenames (this function will
    generate filenames with the shot number and .h5 extension appended to
    filename_prefix). Sensible defaults for these are also returned by
    new_sequence_details(), so preferably these should be used. output_folder is used
    as it is; filename_prefix, which may name subdirectories, is formatted with each
    shot's globals.

    Shuffle will randomise the order that the run files are generated in with respect to
    which element of shots they come from. Shuffled or not, what is yielded comes in run
    number order rather than in the order of shots, and each yielded item describes the
    shot that went into it: with return_infos the info carries that shot's globals
    beside its path and run number, and without them the file at the yielded path holds
    them. So a caller that shuffles needs no separate note of where each element of
    shots ended up.

    This function returns a *generator*. The run files are not actually created until
    you loop over this generator (which gives you the filepaths). This is useful for not
    having to clean up as many unused files in the event of failed compilation of
    labscripts. If you want all the run files to be created at some point, simply
    convert the returned generator to a list. The filenames the run files are given is
    simply the sequence_id with increasing integers appended."""
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
    for runfilename, run_no, shot_globals in numbered:
        if create_files:
            make_single_run_file(
                runfilename, sequence_globals, shot_globals, sequence_attrs, run_no, n_runs
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
            basename + '.h5', '_{index:0%dd}' % width, start=run_no, strip_suffix=False
        )
        if os.path.abspath(path) not in taken:
            return path, run_no
        run_no += 1


def make_single_run_file(
    filename, sequenceglobals, runglobals, sequence_attrs, run_no, n_runs, shot_id=None
):
    """Does what it says. runglobals is a dict of this run's globals, the format being
    the same as that of one element of the list returned by expand_globals.
    sequence_globals is a nested dictionary of the type returned by get_globals.
    sequence_attrs is a dict of attributes pertaining to this sequence, as returned by
    new_sequence_details, and is identical for every run of one sequence.

    run_no is this run's number, which is unique within its sequence. n_runs is how far
    that sequence reaches as of this file: one more than the highest run number it has
    once this one is written. For a sequence compiled in one go every file carries the
    same value, which exceeds the number of runs generated if files already in the
    folder took some of their numbers. For a sequence that is added
    to afterwards it is not: the shots written earlier keep the smaller number they were
    written with, since they may already have run and are not rewritten. So n_runs read
    off one shot is not the size of its sequence, and the shots of one sequence need not
    agree on it.

    shot_id, if given, is the identifier of the queue row this shot was written for,
    so that a result coming back can be matched to the shot that was submitted. A file
    written without one carries no such attribute at all, which is how a shot that
    nobody submitted is told apart from one that was."""
    directory = os.path.dirname(filename)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with h5py.File(filename, 'w') as f:
        f.attrs.update(sequence_attrs)
        f.attrs['run number'] = run_no
        f.attrs['n_runs'] = n_runs
        if shot_id is not None:
            f.attrs['shot_id'] = shot_id
        f.create_group('globals')
        if sequenceglobals is not None:
            for groupname, groupvars in sequenceglobals.items():
                group = f['globals'].create_group(groupname)
                unitsgroup = group.create_group('units')
                expansiongroup = group.create_group('expansion')
                for name, (value, units, expansion) in groupvars.items():
                    group.attrs[name] = value
                    unitsgroup.attrs[name] = units
                    expansiongroup.attrs[name] = expansion
        for name, value in runglobals.items():
            if value is None:
                # Store it as a null object reference:
                value = h5py.Reference()
            try:
                f['globals'].attrs[name] = value
            except Exception as e: # noqa: BLE001 all global exceptions are value errors
                message = (f'Global {name} cannot be saved as an hdf5 attribute. ' +
                           'Globals can only have relatively simple datatypes, with no nested structures. ' +
                           'Original error was:\n' +
                           f'{e.__class__.__name__}: {e!s}')
                raise ValueError(message)


def make_run_file_from_globals_files(labscript_file, globals_files, output_path, config=None):
    """Creates a run file output_path, using all the globals from globals_files. Uses
    labscript_file to determine the sequence_attrs only"""
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
    if len(shots) != 1:
        scanning_globals = [name for name, expansion in expansions.items() if expansion]
        raise ValueError(
            'Cannot compile to a single run file: The following globals are a '
            'sequence: ' + ', '.join(scanning_globals)
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
    """Compiles labscript_file with run_file.
    
    This function is designed to be called in a thread. 
    The stdout and stderr from the compilation will be shovelled into
    stream_port via zmq push as it spews forth, and when compilation is complete,
    done_callback will be called with a boolean argument indicating success. Note that
    the zmq communication will be encrypted, or not, according to security settings in
    labconfig. If you want to receive the data on a zmq socket, do so using a PULL
    socket created from a :class:`labscript_utils.ls_zprocess.Context`, or using a
    :class:`labscript_utils.ls_zprocess.ZMQServer`. These subclasses will also be configured
    with the appropriate security settings and will be able to receive the messages.

    Args:
        labscript_file (str): Path to labscript file to be compiled
        run_file (str): Path to h5 file where compilation output is stored.
            This file must already exist with proper globals initialization.
            See :func:`runmanager.globals_file.new_globals_file` for details.
        stream_port (zmq.socket, optional): ZMQ socket to push stdout and stderr.
            If None, defaults to calling process stdout/stderr. Default is None.
        done_callback (function, optional): Callback function run when compilation finishes.
            Takes a single boolean argument marking compilation success or failure.
            If None, callback is skipped. Default is None.
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
    """Compiles labscript_file with multiple run_files (ie globals).
    
    This function is designed to be called in a thread.
    The stdout and stderr from the compilation will be shovelled into
    stream_port via zmq push as it spews forth, and when each compilation is complete,
    done_callback will be called with a boolean argument indicating success. Compilation
    will stop after the first failure.  If you want to receive the data on a zmq socket,
    do so using a PULL socket created from a :class:`labscript_utils.ls_zprocess.Context`, or
    using a :class:`labscript_utils.ls_zprocess.ZMQServer`. These subclasses will also be
    configured with the appropriate security settings and will be able to receive the
    messages.
    
    Args:
        labscript_file (str): Path to labscript file to be compiled
        run_files (list of str): Paths to h5 file where compilation output is stored.
            These files must already exist with proper globals initialization.
        stream_port (zmq.socket, optional): ZMQ socket to push stdout and stderr.
            If None, defaults to calling process stdout/stderr. Default is None.
        done_callback (function, optional): Callback function run when compilation finishes.
            Takes a single boolean argument marking compilation success or failure.
            If None, callback is skipped. Default is None.
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
    """Compiles labscript_file with multiple globals files into a directory.
    
    Instead, stderr and stdout will be put to
    stream_port via zmq push in the multipart message format `['stdout','hello, world\\n']`
    etc. When compilation is finished, the function done_callback will be called a
    boolean argument indicating success or failure.  If you want to receive the data on
    a zmq socket, do so using a PULL socket created from a
    :external:class:`labscript_utils.ls_zprocess.Context`, or using a
    :external:class:`labscript_utils.ls_zprocess.ZMQServer`. These subclasses will also be configured with
    the appropriate security settings and will be able to receive the messages.
    
    Args:
        labscript_file (str): Path to labscript file to be compiled
        globals_files (list of str): Paths to h5 file where globals values to be used are stored.
            See :func:`make_run_file_from_globals_files` for details.
        output_path (str): Path of the shot file to write and compile into.
        stream_port (zmq.socket): ZMQ socket to push stdout and stderr.
        done_callback (function): Callback function run when compilation finishes.
            Takes a single boolean argument marking compilation success or failure.
    """
    try:
        make_run_file_from_globals_files(labscript_file, globals_files, output_path)
        thread = threading.Thread(
            target=compile_labscript_async, args=[labscript_file, output_path, stream_port, done_callback])
        thread.daemon = True
        thread.start()
    except Exception:
        error = traceback.format_exc()
        zmq_push_multipart(stream_port, data=[b'stderr', error.encode('utf-8')])
        t = threading.Thread(target=done_callback, args=(False,))
        t.daemon = True
        t.start()
