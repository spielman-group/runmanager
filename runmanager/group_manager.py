#####################################################################
#                                                                   #
# /group_manager.py                                                 #
#                                                                   #
# Copyright 2013, Monash University                                 #
#                                                                   #
# This file is part of the program runmanager, in the labscript     #
# suite (see http://labscriptsuite.org), and is licensed under the  #
# Simplified BSD License. See the license.txt file in the root of   #
# the project for the full license.                                 #
#                                                                   #
#####################################################################
"""Functions for reading, writing, and modifying globals groups."""

import warnings

import numpy as np
import labscript_utils.shot_utils

from runmanager import globals_file


def is_valid_python_identifier(name):
    return name.isidentifier()


def is_valid_hdf5_group_name(name):
    """Whether ``name`` is a valid hdf5 group name: ASCII only, with no ``/`` or ``.``."""
    return name.isascii() and '.' not in name and '/' not in name


def new_group(filename, groupname):
    if not is_valid_hdf5_group_name(groupname):
        raise ValueError(
            'Invalid group name. Group names must contain only ASCII '
            'characters and cannot include "/" or ".".'
        )
    globals_file.new_group(filename, groupname)


def rename_group(filename, oldgroupname, newgroupname):
    if oldgroupname == newgroupname:
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
            raise ValueError(f'{globalname} is not a valid Python variable name')
    globals_file.new_globals(filename, groupname, records)


def rename_global(filename, groupname, oldglobalname, newglobalname):
    if oldglobalname == newglobalname:
        return
    if not is_valid_python_identifier(newglobalname):
        raise ValueError(f'{newglobalname} is not a valid Python variable name')
    globals_file.rename_global(filename, groupname, oldglobalname, newglobalname)


def guess_expansion_type(value):
    if isinstance(value, (np.ndarray, list)):
        return 'outer'
    else:
        return ''


def get_all_groups(h5_files):
    """Return ``{group_name: path}`` for the groups of the globals files ``h5_files``,
    a path or a list of them. Raises ValueError for a group name that is in more
    than one."""
    if isinstance(h5_files, (bytes, str)):
        h5_files = [h5_files]
    groups = {}
    for path in h5_files:
        for group_name in globals_file.get_group_names(path):
            if group_name in groups:
                raise ValueError(f'Error: group {group_name} is defined in both {groups[group_name]} and {path}. '
                                 'Only uniquely named groups can be used together '
                                 'to make a run file.')
            groups[group_name] = path
    return groups


def get_globals(groups):
    """Return ``{group_name: {global_name: (expression, units, expansion)}}`` for
    ``groups``, a dict of group name to globals file. A scanned global has its Scan
    expression and its expansion, any other its default expression and no expansion."""
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


def get_shot_globals(filepath):
    """Return the evaluated globals of a shot file.

    Deprecated: use ``labscript_utils.shot_utils.get_shot_globals``, which this calls.
    """
    warnings.warn(
        FutureWarning("get_shot_globals has moved to labscript_utils.shot_utils. "
                      "Please update your code to import it from there."), stacklevel=2)

    return labscript_utils.shot_utils.get_shot_globals(filepath)
