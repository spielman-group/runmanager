#####################################################################
#                                                                   #
# /expander.py                                                      #
#                                                                   #
# Copyright 2013, Monash University                                 #
#                                                                   #
# This file is part of the program runmanager, in the labscript     #
# suite (see http://labscriptsuite.org), and is licensed under the  #
# Simplified BSD License. See the license.txt file in the root of   #
# the project for the full license.                                 #
#                                                                   #
#####################################################################
"""Functions for expanding iterable globals into a list of shots.
"""

import itertools
import math
import random
import sys


from runmanager.evaluator import evaluate_globals
from runmanager.group_manager import _details_to_sequence_globals


def _make_axes(sequence_globals, evaled_globals, expansion_config=None):
    """The scan's axes as ``(axes, global_names, shuffle)``, each a dict by axis name,
    in the axes' order: their values, the globals on them, and whether to shuffle."""
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
        axes['zip '+zip_key] = list(zip(*axis, strict=True))
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
            axes['outer '+global_name] = list(zip(*[value]))
            global_names['outer '+global_name] = [global_name]

    # sort the axes by order. An ordered axis the globals no longer produce, such
    # as a scan just disabled, is skipped:
    ordered = sorted(axes, key=lambda key: order.get(key, -1))
    return (
        {key: axes[key] for key in ordered},
        {key: global_names[key] for key in ordered},
        {key: shuffle.get(key, False) for key in ordered},
    )


def count_shots(sequence_globals, evaled_globals, expansion_config=None, return_dimensions=False):
    """Count the shots expand_globals makes from the same arguments, without making them.

    Parameters
    ----------
    sequence_globals, evaled_globals, expansion_config
        As expand_globals takes them.
    return_dimensions : bool, optional
        Also return the length of each axis.

    Returns
    -------
    int
        The number of shots.
    dict
        With ``return_dimensions``, the length of each axis, by its name.
    """
    axes, _, _ = _make_axes(sequence_globals, evaled_globals, expansion_config)
    dimensions = {key: len(axis) for key, axis in axes.items()}
    n_shots = math.prod(dimensions.values())
    if return_dimensions:
        return n_shots, dimensions
    return n_shots


def scanned_globals(sequence_globals, evaled_globals, global_hierarchy):
    """Map each global that is scanned over to the name of its axis.

    A global is scanned over if it is on an outer or zip-group axis, of any
    length, and does not depend, directly or through other globals, on another
    global on the same axis. ``global_hierarchy`` is as evaluate_globals
    returns it.
    """
    _, global_names, _ = _make_axes(sequence_globals, evaled_globals)

    def dependencies(global_name):
        result = set()
        to_visit = [global_name]
        while to_visit:
            for name in global_hierarchy.get(to_visit.pop(), []):
                if name not in result:
                    result.add(name)
                    to_visit.append(name)
        return result

    scanned = {}
    for axis_name, names in global_names.items():
        if axis_name == 'zip ':
            # The globals with no expansion
            continue
        for global_name in names:
            if not dependencies(global_name) & (set(names) - {global_name}):
                scanned[global_name] = axis_name
    return scanned


def expand_globals(sequence_globals, evaled_globals, expansion_config = None, return_dimensions = False):
    """Expands iterable globals according to their expansion
    settings. Creates a number of 'axes' which are to be outer product'ed
    together. Some of these axes have only one element, these are globals
    that do not vary. Some have a set of globals being zipped together,
    iterating in lock-step. Others contain a single global varying
    across its values (the globals set to 'outer' expansion). Returns
    a list of shots, each element of which is a dictionary for that
    shot's globals."""
    axes, global_names, shuffle = _make_axes(sequence_globals, evaled_globals, expansion_config)
    dimensions = {key: len(axis) for key, axis in axes.items()}

    # shuffle relevant axes
    for axis_name, axis_values in axes.items():
        if shuffle[axis_name]:
            random.shuffle(axis_values)

    axes = list(axes.values())
    # flatten the global names
    global_names = [global_name for global_list in global_names.values() for global_name in global_list]

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
