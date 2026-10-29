#####################################################################
#                                                                   #
# /differ.py                                                        #
#                                                                   #
# Copyright 2013, Monash University                                 #
#                                                                   #
# This file is part of the program runmanager, in the labscript     #
# suite (see http://labscriptsuite.org), and is licensed under the  #
# Simplified BSD License. See the license.txt file in the root of   #
# the project for the full license.                                 #
#                                                                   #
#####################################################################
"""Functions for comparing two globals groups.
"""

from labscript_utils.dict_diff import dict_diff

from runmanager.evaluator import _evaluate_globals
from runmanager.group_manager import get_all_groups, get_globals
from runmanager.tokenizer import remove_comments_and_tokenify


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
                value_expression, _, _ = value
                flattened_sequence_globals[name] = value_expression
    return flattened_sequence_globals


def _shortened(text):
    """``text`` on one line, with its middle cut out if it is over 200 characters."""
    text = ' '.join(text.split())
    if len(text) > 200:
        text = text[:98] + ' ... ' + text[-97:]
    return text


def globals_diff_groups(active_groups, other_groups, return_string=True):
    """Given two sets of globals groups, perform a diff of the raw
    and evaluated globals. If return_string is True, returns the diff as a
    string. Otherwise prints it and returns it as a pandas DataFrame, which is
    empty if there are no differences. In the string, values and expressions
    over 200 characters are shortened; the DataFrame has them in full."""
    our_sequence_globals = get_globals(active_groups)
    other_sequence_globals = get_globals(other_groups)

    # evaluate globals
    our_evaluated_sequence_globals, _, _, _ = _evaluate_globals(our_sequence_globals, raise_exceptions=False)
    other_evaluated_sequence_globals, _, _, _ = _evaluate_globals(other_sequence_globals, raise_exceptions=False)

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
    columns = ['Prev (Eval)', 'Current (Eval)', 'Prev (Raw)', 'Current (Raw)']
    if filtered_differences:
        # A block per global, since a table pads every row to its longest cell,
        # which for a long list makes text the output box takes minutes to lay out:
        width = max(len(column) for column in columns) + 2
        blocks = []
        for name in sorted(filtered_differences):
            lines = [name]
            for column, text in zip(columns, filtered_differences[name]):
                lines.append(f"    {column + ':':<{width}}{_shortened(text)}")
            blocks.append('\n'.join(lines))
        payload = '\n\n'.join(blocks) + '\n\n'
    else:
        payload = 'Evaluated globals are identical to those of selected file.\n'
    if return_string:
        return payload
    print(payload)
    import pandas as pd
    return pd.DataFrame.from_dict(
        filtered_differences, 'index', columns=columns
    ).sort_index()


def globals_diff_shots(file1, file2):
    # Get file's globals groups
    active_groups = get_all_groups(file1)

    # Get other file's globals groups
    other_groups = get_all_groups(file2)

    print(f'Globals diff between:\n{file1}\n{file2}\n\n')
    return globals_diff_groups(active_groups, other_groups, return_string=False)
