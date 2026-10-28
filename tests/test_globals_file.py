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
import os
import shutil
import tempfile
import threading
import unittest

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
