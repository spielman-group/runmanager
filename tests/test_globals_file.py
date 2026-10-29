#####################################################################
#                                                                   #
# /tests/test_globals_file.py                                       #
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
import contextlib
import io
import os
import shutil
import tempfile
import threading
import unittest

import runmanager
import runmanager.globals_file as globals_file


class ConcurrentEditTests(unittest.TestCase):
    def test_edits_from_two_threads_all_persist(self):
        # A remote call and the preparse edit the same globals file at once.
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        path = os.path.join(directory, 'globals.toml')
        globals_file.new_globals_file(path)
        globals_file.new_group(path, 'group')
        globals_file.new_globals(path, 'group', {'x': {}, 'y': {}})
        errors = []

        def edit(name):
            try:
                for i in range(100):
                    globals_file.set_field(path, 'group', name, 'default', str(i))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=edit, args=(name,)) for name in ('x', 'y')]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(
            [globals_file.get_field(path, 'group', name, 'default') for name in ('x', 'y')],
            ['99', '99'],
        )


class MalformedFileTests(unittest.TestCase):
    def test_a_group_or_global_that_is_not_a_table_is_refused(self):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        path = os.path.join(directory, 'globals.toml')
        header = 'format = "runmanager_globals"\nversion = 1\n\n'
        for entry in ('[groups]\nnote = "kept"\n', '[groups.group.globals]\nx = "1"\n'):
            with self.subTest(entry=entry):
                with open(path, 'w') as f:
                    f.write(header + entry)
                with self.assertRaises(globals_file.GlobalsFileError):
                    runmanager.get_all_groups([path])


class GlobalsDiffTests(unittest.TestCase):
    def test_identical_globals_diff_to_an_empty_table(self):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        path = os.path.join(directory, 'globals.toml')
        globals_file.new_globals_file(path)
        globals_file.new_group(path, 'group')
        globals_file.new_globals(path, 'group', {'x': {'default': '1'}})

        with contextlib.redirect_stdout(io.StringIO()):
            table = runmanager.globals_diff_groups(
                {'group': path}, {'group': path}, return_string=False
            )

        self.assertTrue(table.empty)


class GlobalNameTests(unittest.TestCase):
    def test_a_global_is_named_only_as_an_expression_can_name_it(self):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        path = os.path.join(directory, 'globals.toml')
        globals_file.new_globals_file(path)
        runmanager.new_group(path, 'group')

        for name in ('x\u00b2', '\u0663', 'a b'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                runmanager.new_global(path, 'group', name)
        runmanager.new_global(path, 'group', 'x_2')
