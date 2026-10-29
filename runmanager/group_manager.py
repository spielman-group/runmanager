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
"""Functions for reading, writing, and modifying globals groups.
"""

import warnings

import numpy as np
import labscript_utils.shot_utils

from runmanager import globals_file


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
        return 'outer'
    else:
        return ''


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
