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

import labscript_utils.h5_lock  # noqa: F401 (needed to ensure lock initializes properly)
from labscript_utils.versions import package_version

__version__ = package_version(__package__)

from runmanager.exceptions import (
    ExpansionError,
)
from runmanager.tokenizer import (
    find_comments,
    remove_comments_and_tokenify,
)
from runmanager.evaluator import (
    TraceDictionary,
    iterator_to_tuple,
    evaluate_globals,
)
from runmanager.group_manager import (
    is_valid_python_identifier,
    is_valid_hdf5_group_name,
    new_group,
    rename_group,
    new_global,
    new_globals,
    rename_global,
    guess_expansion_type,
    get_all_groups,
    get_globals,
    get_shot_globals,
)
from runmanager.expander import (
    count_shots,
    scanned_globals,
    expand_globals,
    get_frozen_globals,
    get_shots_with_frozen_globals,
)
from runmanager.compiler import (
    next_sequence_index,
    new_sequence_details,
    make_run_files,
    next_run_file,
    make_single_run_file,
    make_run_file_from_globals_files,
    get_queue_compile_globals,
    start_compiler,
    compile_with,
    compile_labscript_async,
    compile_multishot_async,
    compile_labscript_with_globals_files_async,
)
from runmanager.differ import (
    flatten_globals,
    dict_diff,
    globals_diff_groups,
    globals_diff_shots,
)

__all__ = [
    'ExpansionError',
    'find_comments',
    'remove_comments_and_tokenify',
    'TraceDictionary',
    'iterator_to_tuple',
    'evaluate_globals',
    'is_valid_python_identifier',
    'is_valid_hdf5_group_name',
    'new_group',
    'rename_group',
    'new_global',
    'new_globals',
    'rename_global',
    'guess_expansion_type',
    'get_all_groups',
    'get_globals',
    'get_shot_globals',
    'count_shots',
    'scanned_globals',
    'expand_globals',
    'get_frozen_globals',
    'get_shots_with_frozen_globals',
    'next_sequence_index',
    'new_sequence_details',
    'make_run_files',
    'next_run_file',
    'make_single_run_file',
    'make_run_file_from_globals_files',
    'get_queue_compile_globals',
    'start_compiler',
    'compile_with',
    'compile_labscript_async',
    'compile_multishot_async',
    'compile_labscript_with_globals_files_async',
    'flatten_globals',
    'dict_diff',
    'globals_diff_groups',
    'globals_diff_shots',
]
