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
from unittest import mock

import numpy as np

import runmanager
import runmanager.globals_file as globals_file
# fixtures stubs the splash and does the guarded import of the application.
from fixtures import RunManager, labconfig


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

    def test_an_outer_global_and_a_zip_group_of_its_name_are_two_axes(self):
        sequence_globals, results = evaluate({'x': ('[1, 2]', 'outer'), 'y': ('[3]', 'x')})
        self.assertEqual(
            runmanager.expand_globals(sequence_globals, results),
            [{'x': 1, 'y': 3}, {'x': 2, 'y': 3}],
        )

    def test_a_zipped_global_that_depends_on_the_outer_global_of_its_name_is_refused(self):
        _, results = evaluate({'x': ('[1, 2]', 'outer'), 'y': ('np.array(x) * 2', 'x')})
        self.assertIsInstance(results['group']['y'], runmanager.ExpansionError)


class EvaluationTests(unittest.TestCase):
    def evaluate(self, expressions):
        """Evaluate ``{name: expression}`` as one group of globals not scanned, without
        raising. Returns the values and the hierarchy."""
        sequence_globals = {
            'group': {name: (expression, '', '') for name, expression in expressions.items()}
        }
        results, hierarchy, _ = runmanager.evaluate_globals(sequence_globals, raise_exceptions=False)
        return results['group'], hierarchy

    def test_an_expression_that_does_not_compile_holds_its_syntax_error(self):
        values, _ = self.evaluate({'a': '1 +', 'b': '(yield)', 'c': '2'})
        self.assertIsInstance(values['a'], SyntaxError)
        self.assertIsInstance(values['b'], SyntaxError)
        self.assertEqual(values['c'], 2)

    def test_the_hierarchy_holds_the_globals_an_expression_refers_to_on_every_branch(self):
        _, hierarchy = self.evaluate({
            'flag': 'True',
            'x': '1',
            'y': '2',
            'z': 'x if flag else y',
            'f': 'lambda x: x',
            'g': '([x for x in range(2)], lambda: x)[1]',
        })
        self.assertEqual(hierarchy, {'z': ['flag', 'x', 'y'], 'g': ['x']})

    def test_every_global_in_a_cycle_holds_an_error_naming_the_cycle(self):
        values, _ = self.evaluate({
            'apple': 'pear',
            'pear': 'apple + plum',
            'plum': 'pear',
            'quince': 'plum',
            'counter': 'counter + 1',
            'left': 'right',
            'right': 'left',
        })
        for cycle in [['apple', 'pear', 'plum'], ['counter'], ['left', 'right']]:
            for name in cycle:
                self.assertIsInstance(values[name], ValueError)
                for other in cycle:
                    self.assertIn(other, str(values[name]))
        self.assertIsInstance(values['quince'], NameError)

    def test_a_name_the_expression_binds_with_walrus_is_not_a_reference(self):
        values, _ = self.evaluate({
            'a': '((a := 2), (lambda: a)())[1]',
            'b': 'z + 1',
            'z': '(lambda x=(b := 2): b)()',
            'early': '(late, lambda: (late := 2))[0]',
            'late': '5',
        })
        self.assertEqual(
            (values['a'], values['b'], values['z'], values['early']), (2, 3, 2, 5)
        )

    def test_a_long_expression_evaluates(self):
        values, _ = self.evaluate({'x': ' + '.join(['1'] * 2000)})
        self.assertEqual(values['x'], 2000)

    def test_with_raise_exceptions_one_error_names_every_failed_global(self):
        with self.assertRaises(Exception) as context:
            runmanager.evaluate_globals(
                {'group': {'a': ('1 / 0', '', ''), 'b': ('undefined', '', ''), 'c': ('1', '', '')}}
            )
        self.assertIn('a: ZeroDivisionError', str(context.exception))
        self.assertIn('b: NameError', str(context.exception))

    def test_a_global_defined_in_two_groups_is_refused_before_any_other_error(self):
        sequence_globals = {
            'one': {'a': ('1', '', ''), 'b': ('1 +', '', '')},
            'two': {'a': ('2', '', ''), 'c': ('3', '', '')},
        }
        with self.assertRaisesRegex(ValueError, 'multiple active groups'):
            runmanager.evaluate_globals(sequence_globals)
        results, _, _ = runmanager.evaluate_globals(sequence_globals, raise_exceptions=False)
        self.assertIsInstance(results['one']['a'], ValueError)
        self.assertIs(results['two']['a'], results['one']['a'])
        self.assertIsInstance(results['one']['b'], SyntaxError)
        self.assertEqual(results['two']['c'], 3)


class ShotCountTests(unittest.TestCase):
    def test_the_shot_count_is_the_number_of_shots_engage_makes(self):
        for scan in [
            {'a': ('5', ''), 'x': ('[1, 2, 3]', 'outer'), 'p': ('[4, 5]', 'z'), 'q': ('[6, 7]', 'z')},
            {'a': ('5', '')},
            {'x': ('[]', 'outer'), 'y': ('[1, 2]', 'outer')},
        ]:
            with self.subTest(scan=scan):
                sequence_globals, results = evaluate(scan)
                shots, dimensions = runmanager.expand_globals(
                    sequence_globals, results, return_dimensions=True
                )
                self.assertEqual(
                    runmanager.count_shots(sequence_globals, results, return_dimensions=True),
                    (len(shots), dimensions),
                )


    def test_a_scan_with_no_values_is_refused_before_any_file_is_made(self):
        sequence_globals, results = evaluate({'x': ('[]', 'outer')})
        shots = runmanager.expand_globals(sequence_globals, results)
        with self.assertRaises(ValueError):
            runmanager.make_run_files('folder', sequence_globals, shots, {}, 'prefix')

class FrozenGlobalsTests(unittest.TestCase):
    def test_a_frozen_value_evaluates_back_to_itself_with_only_numpy(self):
        values = [
            np.array(1.5),
            np.linspace(0, 1, 7),
            np.arange(6).reshape(2, 3),
            np.array([1 + 2j, 3.25 - 1e-300j]),
            np.array([True, False]),
            np.array(['a', 'bc']),
            np.array([0.1, 1 / 3], dtype=np.float32),
            np.float64(1 / 3),
            np.float32(0.1),
            np.int64(-7),
            np.bool_(True),
            np.complex128(1 - 1j),
            np.str_('text'),
            1 / 3,
            [1, 2.5],
            'text',
        ]
        details = {
            'group': {
                f'g{i}': {'default': '0', 'scan_enabled': True, 'jit_enabled': False}
                for i in range(len(values))
            }
        }
        shot = {f'g{i}': value for i, value in enumerate(values)}
        frozen = runmanager.get_frozen_globals(details, shot)
        for i, value in enumerate(values):
            with self.subTest(value=value):
                thawed = eval(frozen[f'g{i}'], {'np': np})
                self.assertEqual(type(thawed), type(value))
                if isinstance(value, np.ndarray):
                    self.assertEqual((thawed.dtype, thawed.shape), (value.dtype, value.shape))
                    np.testing.assert_array_equal(thawed, value)
                else:
                    self.assertEqual(thawed, value)

class NamespaceTests(unittest.TestCase):
    def evaluate_with_labconfig(self, expressions, **runmanager_settings):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        config = labconfig(directory, **runmanager_settings)
        with mock.patch.object(runmanager.evaluator, 'LabConfig', lambda: config):
            return evaluate(expressions)[1]['group']

    def test_a_pylab_name_still_works_with_a_warning_naming_its_numpy_replacement(self):
        with self.assertWarnsRegex(FutureWarning, r'np\.linspace'):
            results = self.evaluate_with_labconfig({'x': ('linspace(0, 1, 3)', '')})
        np.testing.assert_array_equal(results['x'], np.linspace(0, 1, 3))

    def test_a_pylab_name_in_a_branch_not_taken_still_warns(self):
        with self.assertWarnsRegex(FutureWarning, r'np\.linspace'):
            self.evaluate_with_labconfig({'x': ('1 if True else linspace(0, 1, 3)', '')})

    def test_a_global_named_like_a_namespace_name_shadows_it_unless_it_fails(self):
        results = self.evaluate_with_labconfig({'x': ('2 * pi', ''), 'pi': ('3', '')})
        self.assertEqual(results['x'], 6)
        results = self.evaluate_with_labconfig({'x': ('2 * pi', ''), 'pi': ('1 / 0', '')})
        self.assertIsInstance(results['pi'], ZeroDivisionError)
        self.assertEqual(results['x'], 2 * np.pi)

    def namespace_file(self, source):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        path = os.path.join(directory, 'namespace.py')
        with open(path, 'w') as f:
            f.write(source)
        return path

    def test_each_global_is_evaluated_once_after_the_globals_it_refers_to(self):
        path = self.namespace_file(
            'calls = []\n\ndef mark(value):\n    calls.append(value)\n    return value\n'
        )
        results = self.evaluate_with_labconfig(
            {
                'g0': ('mark(g1)', ''),
                'g1': ('mark(g2)', ''),
                'g2': ('mark(2)', ''),
                'bad': ('mark(0) / 0', ''),
                'calls_made': ('calls', ''),
            },
            globals_namespace=path,
        )
        self.assertEqual(results['g0'], 2)
        self.assertIsInstance(results['bad'], ZeroDivisionError)
        self.assertEqual(sorted(results['calls_made']), [0, 2, 2, 2])

    def test_a_namespace_file_that_fails_gives_its_error_to_each_global_without_one(self):
        path = self.namespace_file('raise RuntimeError("broken")\n')
        results = self.evaluate_with_labconfig(
            {'a': ('1 +', ''), 'b': ('1', '')}, globals_namespace=path
        )
        self.assertIsInstance(results['a'], SyntaxError)
        self.assertIsInstance(results['b'], RuntimeError)

    def test_without_pylab_its_names_are_gone_and_a_namespace_file_adds_names(self):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        namespace_file = os.path.join(directory, 'namespace.py')
        with open(namespace_file, 'w') as f:
            f.write('def double(x):\n    return 2 * x\n')
        results = self.evaluate_with_labconfig(
            {
                'a': ('linspace(0, 1, 3)', ''),
                'b': ('sum([1, 2])', ''),
                'c': ('double(np.float64(2))', ''),
            },
            pylab_namespace=False,
            globals_namespace=namespace_file,
        )
        self.assertIsInstance(results['a'], NameError)
        self.assertEqual(results['b'], 3)
        self.assertEqual(results['c'], 4)

class ScannedGlobalsTests(unittest.TestCase):
    def test_a_global_derived_from_another_on_its_axis_is_not_counted_as_scanned(self):
        sequence_globals = {
            'group': {
                name: (expression, '', expansion)
                for name, expression, expansion in [
                    ('x', '[1, 2, 3]', 'x'),
                    ('x2', '[2 * v for v in x]', 'x'),
                    ('y', '[4, 5]', 'outer'),
                    ('a', '[6, 7]', 'pair'),
                    ('b', '[8, 9]', 'pair'),
                    ('c', '10', ''),
                ]
            }
        }
        results, hierarchy, _ = runmanager.evaluate_globals(sequence_globals)

        self.assertEqual(
            runmanager.scanned_globals(sequence_globals, results, hierarchy),
            {'x': 'zip x', 'y': 'outer y', 'a': 'zip pair', 'b': 'zip pair'},
        )

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
