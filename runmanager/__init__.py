#####################################################################
#                                                                   #
# /__init__.py                                                      #
#                                                                   #
# Copyright 2013, Monash University                                 #
#                                                                   #
# This file is part of the program runmanager, in the labscript     #
# suite (see http://labscriptsuite.org), and is licensed under the  #
# Simplified BSD License. See the license.txt file in the root of   #
# the project for the full license.                                 #
#                                                                   #
#####################################################################

import itertools
import os
import sys
import random
import time
import subprocess
import threading
import traceback
import datetime
import errno
import json
import re
import tokenize
import io
import warnings

import labscript_utils.h5_lock
import h5py
import numpy as np

from labscript_utils.dict_diff import dict_diff
from labscript_utils.versions import package_version
from labscript_utils.ls_zprocess import ProcessTree, zmq_push_multipart
from labscript_utils.labconfig import LabConfig
from labscript_utils.file_utils import next_available_indexed_filepath
from labscript_utils.lookup_format import format_lookup_string
import labscript_utils.shot_utils
process_tree = ProcessTree.instance()

__version__ = package_version(__package__)
from . import globals_file


def is_valid_python_identifier(name):
    return name.isidentifier()


def is_valid_hdf5_group_name(name):
    """Ensure that a string is a valid name for an hdf5 group.

    The names of hdf5 groups may only contain ASCII characters. Furthermore, the
    characters "/" and "." are not allowed.

    Args:
        name (str): The potential name for an hdf5 group.

    Returns:
        bool: Whether or not `name` is a valid name for an hdf5 group. This will
            be `True` if it is a valid name or `False` otherwise.
    """
    return name.isascii() and '.' not in name and '/' not in name


class ExpansionError(Exception):

    """An exception class so that error handling code can tell when a
    parsing exception was caused by a mismatch with the expansion mode"""
    pass


class TraceDictionary(dict):

    def __init__(self, *args, **kwargs):
        self.trace_data = None
        dict.__init__(self, *args, **kwargs)

    def start_trace(self):
        self.trace_data = []

    def __getitem__(self, key):
        if self.trace_data is not None:
            if key not in self.trace_data:
                self.trace_data.append(key)
        return dict.__getitem__(self, key)

    def stop_trace(self):
        trace_data = self.trace_data
        self.trace_data = None
        return trace_data


def new_group(filename, groupname):
    if not is_valid_hdf5_group_name(groupname):
        raise ValueError(
            'Invalid group name. Group names must contain only ASCII '
            'characters and cannot include "/" or ".".'
        )
    globals_file.new_group(filename, groupname)


def rename_group(filename, oldgroupname, newgroupname):
    if oldgroupname == newgroupname:
        # No rename!
        return
    if not is_valid_hdf5_group_name(newgroupname):
        raise ValueError(
            'Invalid group name. Group names must contain only ASCII '
            'characters and cannot include "/" or ".".'
        )
    globals_file.rename_group(filename, oldgroupname, newgroupname)


def new_global(filename, groupname, globalname):
    new_globals(filename, groupname, {globalname: {}})


def new_globals(filename, groupname, records):
    """Add globals to a group, ``{name: record}``, all of them or none.

    Each record holds the fields of a globals file's record that are not to
    be blank."""
    for globalname in records:
        if not is_valid_python_identifier(globalname):
            raise ValueError('%s is not a valid Python variable name'%globalname)
    globals_file.new_globals(filename, groupname, records)


def rename_global(filename, groupname, oldglobalname, newglobalname):
    if oldglobalname == newglobalname:
        # No rename!
        return
    if not is_valid_python_identifier(newglobalname):
        raise ValueError('%s is not a valid Python variable name'%newglobalname)
    globals_file.rename_global(filename, groupname, oldglobalname, newglobalname)


def guess_expansion_type(value):
    if isinstance(value, np.ndarray) or isinstance(value, list):
        return u'outer'
    else:
        return u''


def iterator_to_tuple(iterator, max_length=1000000):
    # We want to prevent infinite length tuples, but we cannot know
    # whether they are infinite or not in advance. So we'll convert to
    # a tuple only if the length is less than max_length:
    temp_list = []
    for i, element in enumerate(iterator):
        temp_list.append(element)
        if i == max_length:
            raise ValueError('This iterator is very long, possibly infinite. ' +
                             'Runmanager cannot create an infinite number of shots. ' +
                             'If you really want an iterator longer than %d, ' % max_length +
                             'please modify runmanager.iterator_to_tuple and increase max_length.')
    return tuple(temp_list)


def get_all_groups(h5_files):
    """returns a dictionary of group_name: h5_path pairs from a list of h5_files."""
    if isinstance(h5_files, bytes) or isinstance(h5_files, str):
        h5_files = [h5_files]
    groups = {}
    for path in h5_files:
        for group_name in globals_file.get_group_names(path):
            if group_name in groups:
                raise ValueError('Error: group %s is defined in both %s and %s. ' % (group_name, groups[group_name], path) +
                                 'Only uniquely named groups can be used together '
                                 'to make a run file.')
            groups[group_name] = path
    return groups


def get_globals(groups):
    """Return the effective expression, units, and expansion for active globals."""
    return _details_to_sequence_globals(globals_file.get_globals_details(groups))


def _details_to_sequence_globals(globals_details, defaults_only=False, overrides=None):
    overrides = {} if overrides is None else overrides
    sequence_globals = {}
    for group_name, globals_data in globals_details.items():
        sequence_globals[group_name] = {}
        for global_name, record in globals_data.items():
            if global_name in overrides:
                expression = overrides[global_name]
                expansion = ''
            elif defaults_only or not record['scan_enabled']:
                expression = record['default']
                expansion = ''
            else:
                expression = record['scan']
                expansion = record['expansion']
            sequence_globals[group_name][global_name] = (
                expression,
                record['units'],
                expansion,
            )
    return sequence_globals


def get_frozen_globals(globals_details, shot):
    frozen_globals = {}
    for group_name, globals_data in globals_details.items():
        for global_name, record in globals_data.items():
            if record['jit_enabled']:
                continue
            if record['scan_enabled']:
                # numpy's default repr rounds to 8 digits and elides long arrays
                with np.printoptions(threshold=sys.maxsize, floatmode='unique'):
                    expression = repr(shot[global_name])
            else:
                expression = record['default']
            frozen_globals[global_name] = expression
    return frozen_globals


def get_shots_with_frozen_globals(globals_details, expansion_order=None):
    """Expand the globals into shots, each with the globals to freeze for it.

    Parameters
    ----------
    globals_details : dict
        The globals as returned by ``globals_file.get_globals_details``.
    expansion_order : dict, optional
        How the expansions are ordered and shuffled, as ``expand_globals`` takes.

    Returns
    -------
    list
        One ``(shot globals, frozen globals)`` pair per shot.
    dict
        The expansion type of each global.
    """
    sequence_globals = _details_to_sequence_globals(globals_details)
    evaled_globals, _, expansions = evaluate_globals(sequence_globals)
    shots = expand_globals(sequence_globals, evaled_globals, expansion_order)
    return (
        [(shot, get_frozen_globals(globals_details, shot)) for shot in shots],
        expansions,
    )


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


def evaluate_globals(sequence_globals, raise_exceptions=True):
    """Takes a dictionary of globals as returned by get_globals. These
    globals are unevaluated strings.  Evaluates them all in the same
    namespace so that the expressions can refer to each other. Iterates
    to allow for NameErrors to be resolved by subsequently defined
    globals. Throws an exception if this does not result in all errors
    going away. The exception contains the messages of all exceptions
    which failed to be resolved. If raise_exceptions is False, any
    evaluations resulting in an exception will instead return the
    exception object in the results dictionary"""

    # Flatten all the groups into one dictionary of {global_name:
    # expression} pairs. Also create the group structure of the results
    # dict, which has the same structure as sequence_globals:
    all_globals = {}
    results = {}
    expansions = {}
    global_hierarchy = {}
    # Pre-fill the results dictionary with groups, this is needed for
    # storing exceptions in the case of globals with the same name being
    # defined in multiple groups (all of them get the exception):
    for group_name in sequence_globals:
        results[group_name] = {}
    multiply_defined_globals = set()
    for group_name in sequence_globals:
        for global_name in sequence_globals[group_name]:
            if global_name in all_globals:
                # The same global is defined twice. Either raise an
                # exception, or store the exception for each place it is
                # defined, depending on whether raise_exceptions is True:
                groups_with_same_global = []
                for other_group_name in sequence_globals:
                    if global_name in sequence_globals[other_group_name]:
                        groups_with_same_global.append(other_group_name)
                exception = ValueError('Global named \'%s\' is defined in multiple active groups:\n    ' % global_name +
                                       '\n    '.join(groups_with_same_global))
                if raise_exceptions:
                    raise exception
                for other_group_name in groups_with_same_global:
                    results[other_group_name][global_name] = exception
                multiply_defined_globals.add(global_name)
            all_globals[global_name], units, expansion = sequence_globals[group_name][global_name]
            expansions[global_name] = expansion

    # Do not attempt to evaluate globals which are multiply defined:
    for global_name in multiply_defined_globals:
        del all_globals[global_name]

    # Eval the expressions in the same namespace as each other:
    evaled_globals = {}
    # we use a "TraceDictionary" to track which globals another global depends on
    sandbox = TraceDictionary()
    exec('from pylab import *', sandbox, sandbox)
    exec('from runmanager.functions import *', sandbox, sandbox)
    globals_to_eval = all_globals.copy()
    previous_errors = -1
    while globals_to_eval:
        errors = []
        for global_name, expression in globals_to_eval.copy().items():
            # start the trace to determine which globals this global depends on
            sandbox.start_trace()
            try:
                code = compile(expression, '<string>', 'eval')
                value = eval(code, sandbox)
                # Need to know the length of any iterator, convert to tuple:
                if hasattr(value, '__next__'):
                    value = iterator_to_tuple(value)
                # Make sure if we're zipping or outer-producting this value, that it can
                # be iterated over:
                if expansions[global_name]:
                    try:
                        iter(value)
                    except Exception as e:
                        raise ExpansionError(str(e))
            except ExpansionError as e:
                # The expression evaluated, so other globals can refer to the value:
                sandbox[global_name] = value
                errors.append((global_name, e))
                sandbox.stop_trace()
                continue
            except Exception as e:
                # Don't raise, just append the error to a list, we'll display them all later.
                errors.append((global_name, e))
                sandbox.stop_trace()
                continue
            # Put the global into the namespace so other globals can use it:
            sandbox[global_name] = value
            del globals_to_eval[global_name]
            evaled_globals[global_name] = value

            # get the results from the global trace
            trace_data = sandbox.stop_trace()
            # Only store names of globals (not other functions)
            for key in list(trace_data):  # copy the list before iterating over it
                if key not in all_globals:
                    trace_data.remove(key)
            if trace_data:
                global_hierarchy[global_name] = trace_data

        if len(errors) == previous_errors:
            # Since some globals may refer to others, we expect maybe
            # some NameErrors to have occured.  There should be fewer
            # NameErrors each iteration of this while loop, as globals
            # that are required become defined. If there are not fewer
            # errors, then there is something else wrong and we should
            # raise it.
            if raise_exceptions:
                message = 'Error parsing globals:\n'
                for global_name, exception in errors:
                    message += '%s: %s: %s\n' % (global_name, exception.__class__.__name__, str(exception))
                raise Exception(message)
            else:
                for global_name, exception in errors:
                    evaled_globals[global_name] = exception
                break
        previous_errors = len(errors)

    # Globals in the same zip group must have equal lengths. Errors, and iterables
    # without a length, are skipped:
    zip_groups = {}
    for global_name, value in evaled_globals.items():
        if expansions[global_name] not in ('', 'outer') and hasattr(value, '__len__'):
            zip_groups.setdefault(expansions[global_name], {})[global_name] = len(value)
    for zip_key, lengths in zip_groups.items():
        if len(set(lengths.values())) > 1:
            exception = ValueError(
                f"Globals in zip group '{zip_key}' have unequal lengths: "
                + ', '.join(f'{name} ({length})' for name, length in lengths.items())
            )
            if raise_exceptions:
                raise exception
            evaled_globals.update(dict.fromkeys(lengths, exception))

    # Each global is evaluated once for the whole sequence, so a global in a zip group
    # cannot vary with globals expanded on other axes, directly or through globals
    # that are not expanded:
    def expanded_dependencies(global_name, visited):
        for name in global_hierarchy.get(global_name, []):
            if name not in visited:
                visited.add(name)
                if expansions[name]:
                    yield name
                else:
                    yield from expanded_dependencies(name, visited)

    for global_name, value in evaled_globals.items():
        zip_key = expansions[global_name]
        if zip_key in ('', 'outer') or isinstance(value, Exception):
            continue
        other_axes = [
            name for name in expanded_dependencies(global_name, {global_name})
            if expansions[name] != zip_key
        ]
        if other_axes:
            exception = ValueError(
                f"Global '{global_name}' in zip group '{zip_key}' depends on globals "
                + 'on other axes: '
                + ', '.join(
                    f'{name} (outer)' if expansions[name] == 'outer'
                    else f"{name} (zip group '{expansions[name]}')"
                    for name in other_axes
                )
                + '. Globals are evaluated once for the whole sequence, not once per '
                + 'shot, so a global in a zip group cannot vary with other axes'
            )
            if raise_exceptions:
                raise exception
            evaled_globals[global_name] = exception

    # Assemble results into a dictionary of the same format as sequence_globals:
    for group_name in sequence_globals:
        for global_name in sequence_globals[group_name]:
            # Do not attempt to override exception objects already stored
            # as the result of multiply defined globals:
            if global_name not in results[group_name]:
                results[group_name][global_name] = evaled_globals[global_name]

    return results, global_hierarchy, expansions


def expand_globals(sequence_globals, evaled_globals, expansion_config = None, return_dimensions = False):
    """Expands iterable globals according to their expansion
    settings. Creates a number of 'axes' which are to be outer product'ed
    together. Some of these axes have only one element, these are globals
    that do not vary. Some have a set of globals being zipped together,
    iterating in lock-step. Others contain a single global varying
    across its values (the globals set to 'outer' expansion). Returns
    a list of shots, each element of which is a dictionary for that
    shot's globals."""

    if expansion_config is None:
        order = {}
        shuffle = {}
    else:
        order = {k:v['order'] for k,v in expansion_config.items() if 'order' in v}
        shuffle = {k:v['shuffle'] for k,v in expansion_config.items() if 'shuffle' in v}

    values = {}
    expansions = {}
    for group_name in sequence_globals:
        for global_name in sequence_globals[group_name]:
            expression, units, expansion = sequence_globals[group_name][global_name]
            value = evaled_globals[group_name][global_name]
            values[global_name] = value
            expansions[global_name] = expansion

    # Get a list of the zip keys in use:
    zip_keys = set(expansions.values())
    try:
        zip_keys.remove('outer')
    except KeyError:
        pass

    axes = {}
    global_names = {}
    dimensions = {}
    for zip_key in zip_keys:
        axis = []
        zip_global_names = []
        for global_name in expansions:
            if expansions[global_name] == zip_key:
                value = values[global_name]
                if isinstance(value, Exception):
                    continue
                if not zip_key:
                    # Wrap up non-iterating globals (with zip_key = '') in a
                    # one-element list. When zipped and then outer product'ed,
                    # this will give us the result we want:
                    value = [value]
                axis.append(value)
                zip_global_names.append(global_name)
        axis = list(zip(*axis, strict=True))
        dimensions['zip '+zip_key] = len(axis)
        axes['zip '+zip_key] = axis
        global_names['zip '+zip_key] = zip_global_names

    # Give each global being outer-product'ed its own axis. It gets
    # wrapped up in a list and zipped with itself so that it is in the
    # same format as the zipped globals, ready for outer-producting
    # together:
    for global_name in expansions:
        if expansions[global_name] == 'outer':
            value = values[global_name]
            if isinstance(value, Exception):
                continue
            axis = [value]
            axis = list(zip(*axis))
            dimensions['outer '+global_name] = len(axis)
            axes['outer '+global_name] = axis
            global_names['outer '+global_name] = [global_name]

    # add any missing items to order and dimensions
    for key, value in axes.items():
        if key not in order:
            order[key] = -1
        if key not in shuffle:
            shuffle[key] = False
        if key not in dimensions:
            dimensions[key] = 1

    # shuffle relevant axes
    for axis_name, axis_values in axes.items():
        if shuffle[axis_name]:
            random.shuffle(axis_values)

    # sort axes and global names by order. An ordered axis the globals no longer
    # produce, such as a scan just disabled, is skipped:
    ordered = [key for key in sorted(order, key=order.get) if key in axes]
    axes = [axes[key] for key in ordered]
    global_names = [global_names[key] for key in ordered]

    # flatten the global names
    global_names = [global_name for global_list in global_names for global_name in global_list]


    shots = []
    for axis_values in itertools.product(*axes):
        # values here is a tuple of tuples, with the outer list being over
        # the axes. We need to flatten it to get our individual values out
        # for each global, since we no longer care what axis they are on:
        global_values = [value for axis in axis_values for value in axis]
        shot_globals = dict(zip(global_names, global_values))
        shots.append(shot_globals)

    if return_dimensions:
        return shots, dimensions
    else:
        return shots


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
        except (OSError, IOError) as exc:
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
            except Exception as e:
                message = ('Global %s cannot be saved as an hdf5 attribute. ' % name +
                           'Globals can only have relatively simple datatypes, with no nested structures. ' +
                           'Original error was:\n' +
                           '%s: %s' % (e.__class__.__name__, str(e)))
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


def get_shot_globals(filepath):
    """Returns the evaluated globals for a shot, for use by labscript or lyse.
    Simple dictionary access as in dict(h5py.File(filepath).attrs) would be fine
    except we want to apply some hacks, so it's best to do that in one place.
    
    Deprecated: use identical function `labscript_utils.shot_utils.get_shot_globals`
    """
    
    warnings.warn(
        FutureWarning("get_shot_globals has moved to labscript_utils.shot_utils. "
                      "Please update your code to import it from there."))

    return labscript_utils.shot_utils.get_shot_globals(filepath)


def find_comments(src):
    """Return a list of start and end indices for where comments are in given Python
    source. Comments on separate lines with only whitespace in between them are
    coalesced. Whitespace preceding a comment is counted as part of the comment."""
    # Offset of the start of each line in src, so that token positions, which are
    # a line number and a column, can be converted to offsets:
    line_offsets = [0]
    for line in io.StringIO(src):
        line_offsets.append(line_offsets[-1] + len(line))
    comments = []
    tokens = tokenize.generate_tokens(io.StringIO(src).readline)
    try:
        for token_type, _, (row, start), (_, end), _ in tokens:
            if token_type == tokenize.COMMENT:
                line_start = line_offsets[row - 1]
                comments.append((line_start + start, line_start + end))
    except tokenize.TokenError:
        pass
    # coalesce comments with only whitespace between them:
    to_merge = []
    for i, ((start1, end1), (start2, end2)) in enumerate(zip(comments, comments[1:])):
        if not src[end1:start2].strip():
            to_merge.append(i)
    # Reverse order so deletion doesn't change indices:
    for i in reversed(to_merge):
        start1, end1 = comments[i]
        start2, end2 = comments[i + 1]
        comments[i] = (start1, end2)
        del comments[i + 1]
    # Extend each comment block to the left to include whitespace:
    for i, (start, end) in enumerate(comments):
        n_whitespace_chars = len(src[:start]) - len(src[:start].rstrip())
        comments[i] = start - n_whitespace_chars, end
    # Extend the final comment to the right to include whitespace:
    if comments:
        start, end = comments[-1]
        n_whitespace_chars = len(src[end:]) - len(src[end:].rstrip())
        comments[-1] = (start, end + n_whitespace_chars)
    return comments


def remove_comments_and_tokenify(src):
    """Removes comments from source code, leaving it otherwise intact,
    and returns it. Also returns the raw tokens for the code, allowing
    comparisons between source to be made without being sensitive to
    whitespace."""
    # Remove comments
    for (start, end) in reversed(find_comments(src)):
        src = src[:start] + src[end:]
    # Tokenify:
    tokens = []
    tokens_iter = tokenize.generate_tokens(io.StringIO(src).readline)
    try:
        for _, token_value, _, _, _ in tokens_iter:
            if token_value:
                tokens.append(token_value)
    except tokenize.TokenError:
        pass
    return src, tokens


def flatten_globals(sequence_globals, evaluated=False):
    """Flattens the data structure of the globals. If evaluated=False,
    saves only the value expression string of the global, not the
    units or expansion."""
    flattened_sequence_globals = {}
    for globals_group in sequence_globals.values():
        for name, value in globals_group.items():
            if evaluated:
                flattened_sequence_globals[name] = value
            else:
                value_expression, units, expansion = value
                flattened_sequence_globals[name] = value_expression
    return flattened_sequence_globals


def globals_diff_groups(active_groups, other_groups, max_cols=1000, return_string=True):
    """Given two sets of globals groups, perform a diff of the raw
    and evaluated globals. If return_string is True, returns the diff as a
    string. Otherwise prints it and returns it as a pandas DataFrame, which is
    empty if there are no differences."""
    our_sequence_globals = get_globals(active_groups)
    other_sequence_globals = get_globals(other_groups)

    # evaluate globals
    our_evaluated_sequence_globals, _, _ = evaluate_globals(our_sequence_globals, raise_exceptions=False)
    other_evaluated_sequence_globals, _, _ = evaluate_globals(other_sequence_globals, raise_exceptions=False)

    # flatten globals dictionaries
    our_globals = flatten_globals(our_sequence_globals, evaluated=False)
    other_globals = flatten_globals(other_sequence_globals, evaluated=False)
    our_evaluated_globals = flatten_globals(our_evaluated_sequence_globals, evaluated=True)
    other_evaluated_globals = flatten_globals(other_evaluated_sequence_globals, evaluated=True)

    # diff the *evaluated* globals
    value_differences = dict_diff(other_evaluated_globals, our_evaluated_globals)

    # We are interested only in displaying globals where *both* the
    # evaluated global *and* its unevaluated expression (ignoring comments
    # and whitespace) differ. This will minimise false positives where a
    # slight change in an expression still leads to the same value, or
    # where an object has a poorly defined equality operator that returns
    # False even when the two objects are identical.
    filtered_differences = {}
    for name, (other_value, our_value) in value_differences.items():
        our_expression = our_globals.get(name, '-')
        other_expression = other_globals.get(name, '-')
        # Strip comments, get tokens so we can diff without being sensitive to comments or whitespace:
        our_expression, our_tokens = remove_comments_and_tokenify(our_expression)
        other_expression, other_tokens = remove_comments_and_tokenify(other_expression)
        if our_tokens != other_tokens:
            filtered_differences[name] = [repr(other_value), repr(our_value), other_expression, our_expression]
    import pandas as pd
    df = pd.DataFrame.from_dict(
        filtered_differences,
        'index',
        columns=['Prev (Eval)', 'Current (Eval)', 'Prev (Raw)', 'Current (Raw)'],
    ).sort_index()
    if filtered_differences:
        payload = df.to_string(max_cols=max_cols) + '\n\n'
    else:
        payload = 'Evaluated globals are identical to those of selected file.\n'
    if return_string:
        return payload
    else:
        print(payload)
        return df


def globals_diff_shots(file1, file2, max_cols=100):
    # Get file's globals groups
    active_groups = get_all_groups(file1)

    # Get other file's globals groups
    other_groups = get_all_groups(file2)

    print('Globals diff between:\n%s\n%s\n\n' % (file1, file2))
    return globals_diff_groups(active_groups, other_groups, max_cols=max_cols, return_string=False)
