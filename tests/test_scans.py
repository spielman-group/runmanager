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
import unittest

import runmanager


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
