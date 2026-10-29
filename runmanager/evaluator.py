#####################################################################
#                                                                   #
# /evaluator.py                                                     #
#                                                                   #
# Copyright 2013, Monash University                                 #
#                                                                   #
# This file is part of the program runmanager, in the labscript     #
# suite (see http://labscriptsuite.org), and is licensed under the  #
# Simplified BSD License. See the license.txt file in the root of   #
# the project for the full license.                                 #
#                                                                   #
#####################################################################
"""Functions for evaluating runmanager globals.
Evaluating converts strings into python expressions.
"""

import builtins
import runpy
import warnings

import numpy as np
from labscript_utils.labconfig import LabConfig

import runmanager.functions
from runmanager.exceptions import ExpansionError

PYLAB_NAMESPACE_NOTE = (
    'To evaluate globals without the names of `from pylab import *` now, set '
    + 'pylab_namespace = False in the [runmanager] section of the labconfig.'
)


class TraceDictionary(dict):

    def __init__(self, *args, **kwargs):
        self.trace_data = None
        dict.__init__(self, *args, **kwargs)

    def start_trace(self):
        self.trace_data = []

    def __getitem__(self, key):
        if self.trace_data is not None and key not in self.trace_data:
            self.trace_data.append(key)
        return dict.__getitem__(self, key)

    def stop_trace(self):
        trace_data = self.trace_data
        self.trace_data = None
        return trace_data


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
                             f'If you really want an iterator longer than {max_length}, ' +
                             'please modify runmanager.iterator_to_tuple and increase max_length.')
    return tuple(temp_list)


def _numpy_name(name, value):
    """The name of value in numpy, as np.<name> or np.<submodule>.<name>, or None if
    numpy does not have it under that name."""
    for prefix, module in [('np', np), ('np.random', np.random), ('np.fft', np.fft), ('np.linalg', np.linalg)]:
        if getattr(module, name, None) is value:
            return f'{prefix}.{name}'
    return None


def _pylab_deprecation(names, namespace):
    """The warning for a global that uses the given names from `from pylab import *`"""
    replacements = []
    for name in names:
        numpy_name = _numpy_name(name, namespace[name])
        if hasattr(builtins, name):
            replacements.append(f'{name} (use {numpy_name}; without pylab, {name} is the Python builtin)')
        elif numpy_name is not None:
            replacements.append(f'{name} (use {numpy_name})')
        else:
            replacements.append(f'{name} (not in numpy; import it in a globals_namespace file)')
    return (
        'Uses names from `from pylab import *`, which runmanager will stop providing in a '
        + 'future version: '
        + ', '.join(replacements)
        + '.'
    )


def _namespace():
    """Returns the namespace in which globals are evaluated, and the set of names in it
    that only `from pylab import *` provides. The namespace holds the names of
    runmanager.functions and of the file given by globals_namespace in the [runmanager]
    section of the labconfig, which take precedence, and, unless pylab_namespace is False
    in that section, the names of `from pylab import *`. The names of the file are those
    in its __all__, or if it has none, those that do not start with an underscore."""
    config = LabConfig()
    namespace = {}
    pylab_names = set()
    if config.getboolean('runmanager', 'pylab_namespace', fallback=True):
        exec('from pylab import *', namespace, namespace) # noqa: S102
        pylab_names = {
            name for name, value in namespace.items()
            if not name.startswith('_') and getattr(builtins, name, None) is not value
        }
    names = {name: getattr(runmanager.functions, name) for name in runmanager.functions.__all__}
    path = config.get('runmanager', 'globals_namespace', fallback='')
    if path:
        try:
            module_globals = runpy.run_path(path)
        except Exception as e:
            raise RuntimeError(
                f'Could not load the globals namespace file {path}: {e.__class__.__name__}: {e!s}'
            ) from e
        names.update(
            (name, module_globals[name]) for name in module_globals.get(
                '__all__', [name for name in module_globals if not name.startswith('_')]
            )
        )
    namespace.update(names)
    return namespace, pylab_names - names.keys()


def evaluate_globals(sequence_globals, raise_exceptions=True):
    """Takes a dictionary of globals as returned by get_globals. These
    globals are unevaluated strings.  Evaluates them all in the same
    namespace so that the expressions can refer to each other. Iterates
    to allow for NameErrors to be resolved by subsequently defined
    globals. Throws an exception if this does not result in all errors
    going away. The exception contains the messages of all exceptions
    which failed to be resolved. If raise_exceptions is False, any
    evaluations resulting in an exception will instead return the
    exception object in the results dictionary.

    If any global uses names that only `from pylab import *` provides, a FutureWarning
    names the globals and the replacement for each name."""
    results, global_hierarchy, expansions, deprecations = _evaluate_globals(
        sequence_globals, raise_exceptions
    )
    if deprecations:
        message = '\n'.join(f'{name}: {message}' for name, message in deprecations.items())
        warnings.warn(FutureWarning(f'{message}\n{PYLAB_NAMESPACE_NOTE}'), stacklevel=2)
    return results, global_hierarchy, expansions


def _evaluate_globals(sequence_globals, raise_exceptions=True):
    """evaluate_globals, which returns (results, global_hierarchy, expansions,
    deprecations) instead of warning. deprecations maps the name of each global that
    uses names that only `from pylab import *` provides to a warning message that names
    their replacements."""

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
                exception = ValueError(f'Global named \'{global_name}\' is defined in multiple active groups:\n    ' +
                                       '\n    '.join(groups_with_same_global))
                if raise_exceptions:
                    raise exception
                for other_group_name in groups_with_same_global:
                    results[other_group_name][global_name] = exception
                multiply_defined_globals.add(global_name)
            all_globals[global_name], _, expansion = sequence_globals[group_name][global_name]
            expansions[global_name] = expansion

    # Do not attempt to evaluate globals which are multiply defined:
    for global_name in multiply_defined_globals:
        del all_globals[global_name]

    # Eval the expressions in the same namespace as each other. If the namespace cannot
    # be loaded, every global holds the error, or it is raised if raise_exceptions is
    # True:
    evaled_globals = {}
    deprecations = {}
    globals_to_eval = all_globals.copy()
    try:
        namespace, pylab_names = _namespace()
    except Exception as e:
        if raise_exceptions:
            raise
        evaled_globals = dict.fromkeys(all_globals, e)
        globals_to_eval = {}
        namespace, pylab_names = {}, set()
    # we use a "TraceDictionary" to track which globals another global depends on
    sandbox = TraceDictionary(namespace)
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
                    except Exception as e: # noqa: BLE001
                        # Any error here is an expansion error
                        raise ExpansionError(str(e))
            except ExpansionError as e:
                # The expression evaluated, so other globals can refer to the value:
                sandbox[global_name] = value
                errors.append((global_name, e))
                sandbox.stop_trace()
                continue
            except Exception as e: # noqa: BLE001
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
            used = sorted(set(trace_data) & pylab_names - all_globals.keys())
            if used:
                deprecations[global_name] = _pylab_deprecation(used, sandbox)
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
                    message += f'{global_name}: {exception.__class__.__name__}: {exception!s}\n'
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
            exception = ExpansionError(
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
            exception = ExpansionError(
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

    return results, global_hierarchy, expansions, deprecations
