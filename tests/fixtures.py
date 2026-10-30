#####################################################################
#                                                                   #
# /tests/fixtures.py                                                #
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
"""Stand-ins shared by runmanager's tests.

Importing ``runmanager.__main__`` builds a ``Splash`` and shows it at module
scope, and only ``if __name__ == '__main__'`` hides it again, so a test that
imported it would leave the startup banner on screen. This module stubs
``labscript_utils.splash`` and then imports the application, so no
``QApplication`` is built either, and re-exports what the tests borrow from
``runmanager.__main__``. Tests import those names from here and never from
``runmanager.__main__``, which keeps the stub installed before the import.

Nothing here creates a ``QApplication`` or shows a widget: tests that need a
``QApplication`` build their own. The functions at the end serve a real lyse and
submit to it through a real ``AnalysisSubmission``.
"""
import os
import socket
import sys
import threading
import time
import types

from labscript_utils.labconfig import LabConfig
from qtutils import UiLoader
import tomli_w


class Splash:
    """Stands in for ``labscript_utils.splash.Splash``, whose ``__init__`` builds a
    ``QApplication``; every method here does nothing."""

    def __init__(self, *args, **kwargs):
        pass

    def show(self):
        pass

    def hide(self):
        pass

    def update_text(self, text):
        pass


def _stub_splash_module():
    """Put a splash module that cannot build a window in ``sys.modules``, for good.

    It is not restored: ``runmanager.__main__`` stays imported holding the stub,
    so restoring the real module would give later importers a second ``Splash``.
    """
    module = types.ModuleType('labscript_utils.splash')
    module.Splash = Splash
    # Only what runmanager's and runviewer's __main__ import, so that it fails
    # loudly if they import more; BLACS's __main__ also imports get_qapplication.
    sys.modules['labscript_utils.splash'] = module


_stub_splash_module()

import runmanager.__main__ as main_module  # noqa: E402
from runmanager.__main__ import (  # noqa: E402
    FingerTabWidget,
    GroupTab,
    RunManager,
    RunmanagerServer,
    TreeView,
)
# runviewer's application, whose server the runviewer tests serve, shows a
# splash at import too:
import runviewer.__main__ as runviewer_main  # noqa: E402
from lyse.client import LyseClient  # noqa: E402
from lyse.communication import LyseServer  # noqa: E402
from qtutils.qt.QtWidgets import QApplication  # noqa: E402
from runmanager.analysis_submission import AnalysisSubmission  # noqa: E402
from runmanager.widgets import Editor  # noqa: E402

# main_module is for the tests that patch names in its namespace rather than
# borrow a method from it.
__all__ = [
    'Editor',
    'FingerTabWidget',
    'GroupTab',
    'RunManager',
    'RunmanagerServer',
    'Splash',
    'TreeView',
    'labconfig',
    'load_main_ui',
    'main_module',
    'runviewer_main',
    'serve_lyse',
    'stop_submission',
    'submit_to_lyse',
    'wait_for',
]


def labconfig(shot_storage, **runmanager_settings):
    """A real labconfig storing shots here, with these [runmanager] settings.

    Everything else it is asked for falls back to runmanager's default, as a
    setting missing from a lab's own labconfig does.
    """
    path = os.path.join(shot_storage, 'labconfig.toml')
    settings = {
        'default': {'experiment_shot_storage': shot_storage},
        'runmanager': runmanager_settings,
    }
    with open(path, 'wb') as f:
        tomli_w.dump(settings, f)
    return LabConfig(config_path=path)


def wait_for(condition, timeout=10):
    """Process events until condition() is true, failing if it is not in time.

    Work on other threads that hops to the main thread, as AnalysisSubmission's
    loop and runmanager's server do, gets on only while this runs.
    """
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError(f'Timed out waiting for {condition}')
        QApplication.processEvents()
        time.sleep(0.001)


def free_port():
    """A loopback port nothing is listening on."""
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def serve_lyse(testcase, lyse_app, port=None):
    """A real LyseServer for lyse_app, a stand-in for the window lyse builds.

    On a free port unless given one, never lyse's own."""
    if port is None:
        port = free_port()
    server = LyseServer(lyse_app, port=port, bind_address='tcp://127.0.0.1')
    testcase.addCleanup(server.shutdown)
    return server


def load_main_ui():
    """runmanager's main window, loaded from main.ui as RunManager loads it."""
    loader = UiLoader()
    loader.registerCustomWidget(FingerTabWidget)
    loader.registerCustomWidget(TreeView)
    return loader.load(os.path.join(os.path.dirname(main_module.__file__), 'main.ui'))


def submit_to_lyse(testcase, port, on_lyse_outcome=lambda *outcome: None):
    """A real AnalysisSubmission, sending shots to the lyse on this port."""
    submission = AnalysisSubmission(load_main_ui(), on_lyse_outcome)
    testcase.addCleanup(stop_submission, submission)
    submission.lyse = LyseClient(host='127.0.0.1', port=port, timeout=1)
    submission.send_to_server = True
    return submission


def stop_submission(submission):
    # shutdown joins the submission thread, which may be waiting on the main
    # thread, so it is joined from another while events are processed:
    stopping = threading.Thread(target=submission.shutdown)
    stopping.start()
    wait_for(lambda: not stopping.is_alive())
