#####################################################################
#                                                                   #
# /tests/test_scans.py                                              #
#                                                                   #
# Copyright 2026, JQI                                               #
# Author: Ian Spielman                                              #
#                                                                   #
# This file is part of runmanager, in the labscript suite           #
# (see http://labscriptsuite.org), and is licensed under the        #
# Simplified BSD License. See the license.txt file in the root of   #
# the project for the full license.                                 #
#                                                                   #
#####################################################################
import os
import shutil
import tempfile
import types
import unittest

import runmanager
import runmanager.globals_file as globals_file
# fixtures stubs the splash and does the guarded import of the application.
from fixtures import RunManager


def evaluate(expressions):
    """Evaluate ``{name: (expression, expansion)}`` as one group, without raising."""
    sequence_globals = {
        'group': {
            name: (expression, '', expansion)
            for name, (expression, expansion) in expressions.items()
        }
    }
    results, _, _ = runmanager.evaluate_globals(sequence_globals, raise_exceptions=False)
    return sequence_globals, results


class ZipGroupTests(unittest.TestCase):
    def test_a_zip_group_member_that_is_not_a_list_is_reported_on_that_global(self):
        sequence_globals, results = evaluate({'a': ('[1, 2]', 'z'), 'b': ('3', 'z')})
        self.assertIsInstance(results['group']['b'], runmanager.ExpansionError)
        self.assertEqual(runmanager.expand_globals(sequence_globals, results), [{'a': 1}, {'a': 2}])

    def test_a_global_scanned_over_a_value_that_is_not_a_list_still_gives_its_value(self):
        _, results = evaluate({'x': ('5', 'outer'), 'y': ('2*x', '')})
        self.assertIsInstance(results['group']['x'], runmanager.ExpansionError)
        self.assertEqual(results['group']['y'], 10)

    def test_a_zip_group_of_unequal_lengths_is_refused(self):
        for a in ('[1, 2, 3]', 'iter([1, 2, 3])'):
            with self.subTest(a=a):
                sequence_globals, results = evaluate(
                    {'a': (a, 'z'), 'b': ('[4, 5]', 'z'), 'c': ('[6, 7]', 'outer')}
                )
                self.assertIsInstance(results['group']['a'], ValueError)
                self.assertIsInstance(results['group']['b'], ValueError)
                self.assertEqual(results['group']['c'], [6, 7])
                with self.assertRaises(ValueError):
                    runmanager.evaluate_globals(sequence_globals)

    def test_a_zipped_global_that_depends_on_another_axis_is_refused(self):
        _, results = evaluate({
            'x': ('[1, 2, 3]', 'outer'),
            'y': ('2 * x', ''),
            'a': ('[4, 5]', 'z'),
            'b': ('[len(y), 6]', 'z'),
            'c': ('[2 * v for v in a]', 'z'),
        })
        self.assertIsInstance(results['group']['b'], ValueError)
        self.assertEqual(results['group']['c'], [8, 10])


class ExpansionGuessTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        self.path = os.path.join(directory, 'globals.toml')
        globals_file.new_globals_file(self.path)
        globals_file.new_group(self.path, 'group')

    def guess(self, previous_values):
        """Guess expansions from a parse of the file, after one that gave ``previous_values``."""
        evaled_globals, global_hierarchy, expansions = runmanager.evaluate_globals(
            runmanager.get_globals({'group': self.path}), raise_exceptions=False
        )
        state = types.SimpleNamespace(
            previous_evaled_globals=previous_values,
            previous_global_hierarchy={},
            previous_expansion_types={},
            previous_expansions={},
        )
        RunManager.guess_expansion_modes(
            state, {'group': self.path}, evaled_globals, global_hierarchy, expansions
        )

    def expansion(self, name):
        return globals_file.get_field(self.path, 'group', name, 'expansion')

    def test_a_global_not_scanned_keeps_its_zip_group(self):
        # A zip group is kept while Scan? is off, for when it is ticked again.
        globals_file.new_globals(
            self.path, 'group', {'x': {'default': '[1, 2, 3]', 'expansion': 'pair'}}
        )
        for previous_values in [{}, {'group': {'x': 5}}]:
            with self.subTest(previous_values=previous_values):
                self.guess(previous_values)
                self.assertEqual(self.expansion('x'), 'pair')

    def test_a_refused_zip_group_keeps_its_expansion(self):
        globals_file.new_globals(
            self.path,
            'group',
            {
                name: {'scan': scan, 'scan_enabled': True, 'expansion': 'z'}
                for name, scan in [('a', '[1, 2, 3]'), ('b', '[4, 5]')]
            },
        )

        self.guess({'group': {'a': [1, 2, 3], 'b': [4, 5, 6]}})

        self.assertEqual([self.expansion('a'), self.expansion('b')], ['z', 'z'])
