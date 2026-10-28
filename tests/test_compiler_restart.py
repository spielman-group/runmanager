#####################################################################
#                                                                   #
# /tests/test_compiler_restart.py                                   #
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
"""Restarting the compiler subprocess while a shot is compiling.

The subprocess is stood in for by a thread at the far end of real zprocess
queues, which are what process_tree.subprocess hands over. The rest is
runmanager's own.
"""
import faulthandler
import threading
import types
import unittest
from unittest import mock

import zmq
from qtutils.qt.QtWidgets import QApplication, QPushButton
from zprocess.process_tree import ReadQueue, WriteQueue

# fixtures stubs the splash and does the guarded import of the application.
from fixtures import RunManager, main_module, wait_for

_qapplication = None


class FakeChild:
    """A compiler subprocess that answers each compile only once `release` is set."""

    def __init__(self, release=None):
        context = zmq.Context.instance()
        commands, to_child = context.socket(zmq.PULL), context.socket(zmq.PUSH)
        from_child, replies = context.socket(zmq.PULL), context.socket(zmq.PUSH)
        commands.bind(f'inproc://commands-{id(self)}')
        to_child.connect(f'inproc://commands-{id(self)}')
        from_child.bind(f'inproc://replies-{id(self)}')
        replies.connect(f'inproc://replies-{id(self)}')
        self.to_child, self.from_child = WriteQueue(to_child), ReadQueue(from_child)
        self.returncode = None
        self.compiled = []

        def mainloop():
            while True:
                signal, data = commands.recv_pyobj()
                if signal == 'quit':
                    self.returncode = 0
                    return
                self.compiled.append(data[1])
                if release:
                    release.wait()
                replies.send_pyobj(['done', True, ''])

        threading.Thread(target=mainloop, daemon=True).start()

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15


class RestartDuringCompileTests(unittest.TestCase):
    def test_a_restart_during_a_compile_does_not_wedge_later_compiles(self):
        global _qapplication
        if QApplication.instance() is None:
            _qapplication = QApplication([])
        # The old child stays hung until the test is over, as one that has
        # stopped answering does:
        release = threading.Event()
        self.addCleanup(release.set)
        old, new = FakeChild(release), FakeChild()
        app = RunManager.__new__(RunManager)
        app.compiler_lock = threading.Lock()
        app.child_ready = threading.Event()
        app.child_ready.set()
        app.to_child, app.from_child, app.child = old.to_child, old.from_child, old
        app.output_box = types.SimpleNamespace(output=lambda *args, **kwargs: None, port=0)
        app.ui = types.SimpleNamespace(pushButton_restart_subprocess=QPushButton())
        spawn = mock.patch.object(
            main_module.process_tree,
            'subprocess',
            return_value=(new.to_child, new.from_child, new),
        )
        spawn.start()
        self.addCleanup(spawn.stop)
        # A deadlock in the restart would hang the run, so it exits with the
        # stacks instead:
        faulthandler.dump_traceback_later(30, exit=True)
        self.addCleanup(faulthandler.cancel_dump_traceback_later)

        first = []
        threading.Thread(
            target=lambda: first.append(app.compile_run_file('e.py', 'first.h5')), daemon=True
        ).start()
        wait_for(lambda: old.compiled)
        # Waiting on compiler_lock when the restart frees the first compile, so
        # it is the next thing sent to a child:
        threading.Thread(
            target=app.compile_run_file, args=('e.py', 'later.h5'), daemon=True
        ).start()
        app.on_restart_subprocess_clicked()

        wait_for(lambda: new.compiled)
        self.assertEqual([success for success, _ in first], [False])
        self.assertEqual(old.compiled, ['first.h5'])
        self.assertEqual(new.compiled, ['later.h5'])


if __name__ == '__main__':
    unittest.main()
