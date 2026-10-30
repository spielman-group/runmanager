#####################################################################
#                                                                   #
# /batch_compiler.py                                                #
#                                                                   #
# Copyright 2013, Monash University                                 #
#                                                                   #
# This file is part of the program runmanager, in the labscript     #
# suite (see http://labscriptsuite.org), and is licensed under the  #
# Simplified BSD License. See the license.txt file in the root of   #
# the project for the full license.                                 #
#                                                                   #
#####################################################################

import os
import queue
import signal
import sys
import threading
import traceback
from types import ModuleType

import numpy as np
from zprocess import interrupt_main_thread
import labscript
from labscript_utils.ls_zprocess import ProcessTree
from labscript_utils.modulewatcher import ModuleWatcher

# How long a compiler told to quit waits for an interrupted compile to stop, in
# seconds, and then for its HDF5 files to be closed before it exits anyway:
ABORT_TIMEOUT = 5
KILL_LOCK_TIMEOUT = 60


class BatchProcessor:
    """The compiler subprocess's loop: compiles each labscript its parent asks for."""

    def __init__(self, to_parent, from_parent, kill_lock):
        self.to_parent = to_parent
        self.from_parent = from_parent
        self.kill_lock = kill_lock
        # Create a module object in which we execute the user's script. From its
        # perspective it will be the __main__ module:
        self.script_module = ModuleType('__main__')
        # Save the dict so we can reset the module to a clean state later:
        self.script_module_clean_dict = self.script_module.__dict__.copy()
        sys.modules[self.script_module.__name__] = self.script_module
        # The path and source of the last script compiled, and its code object:
        self.script_path = None
        self.script_source = None
        self.script_code = None

        self.requests = queue.Queue()
        # Set while no compile is running:
        self.idle = threading.Event()
        self.idle.set()
        # True while the main thread runs the user's script, so that an interrupt
        # is raised in the compile and not in the code around it:
        self.compiling = False
        signal.signal(signal.SIGINT, self.on_sigint)
        threading.Thread(target=self.receive, daemon=True).start()
        self.mainloop()

    def receive(self):
        # Requests are read here, so that an abort or quit is heard while the main
        # thread compiles:
        while True:
            request, data = self.from_parent.get()
            if request == 'abort':
                self.abort()
            elif request == 'quit':
                self.quit()
            else:
                self.requests.put([request, data])

    def mainloop(self):
        while True:
            request, data = self.requests.get()
            if request == 'compile':
                self.idle.clear()
                success, error = self.compile(*data)
                self.idle.set()
                self.to_parent.put(['done', success, error])
            else:
                raise ValueError(request)

    def abort(self):
        """Raise KeyboardInterrupt in the compile, if one is running."""
        if self.compiling:
            interrupt_main_thread()

    def quit(self):
        """Interrupt any compile and exit, once no HDF5 file is open for writing."""
        self.abort()
        if not self.idle.wait(ABORT_TIMEOUT):
            print(
                f'The compile did not stop within {ABORT_TIMEOUT} s. The compiler will '
                f'exit once it has no HDF5 file open for writing, or after '
                f'{KILL_LOCK_TIMEOUT} s.',
                file=sys.stderr,
            )
        sys.stdout.flush()
        sys.stderr.flush()
        self.kill_lock.exit_when_released(status=0, timeout=KILL_LOCK_TIMEOUT)

    def on_sigint(self, signum, frame):
        if self.compiling:
            raise KeyboardInterrupt

    def compile(self, labscript_file, run_file):
        self.script_module.__file__ = labscript_file

        # Save the current working directory before changing it to the location of the
        # user's script:
        cwd = os.getcwd()

        try:
            os.chdir(os.path.dirname(labscript_file))
            # Do not let the modulewatcher unload any modules whilst we're working.
            # The kill lock is not held throughout, so that a quit can end a compile
            # that does not stop; h5_lock holds it while a file is open for writing:
            with module_watcher.lock:
                try:
                    self.compiling = True
                    labscript.labscript_init(run_file, labscript_file=labscript_file)
                    with open(labscript_file) as f:
                        source = f.read()
                    if (labscript_file, source) != (self.script_path, self.script_source):
                        self.script_code = compile(
                            source, self.script_module.__file__, 'exec', dont_inherit=True
                        )
                        self.script_path = labscript_file
                        self.script_source = source
                    exec(self.script_code, self.script_module.__dict__)
                finally:
                    self.compiling = False
            return True, ''
        except KeyboardInterrupt:
            sys.stderr.write('Compilation interrupted\n')
            return False, 'Compilation interrupted'
        # A script that calls sys.exit has failed to compile, as any other error:
        except (Exception, SystemExit) as e:
            if type(e) is NameError and e.name is not None and hasattr(np, e.name):
                e.add_note(
                    "labscript no longer provides numpy's names to experiment scripts. "
                    f"Import '{e.name}' from numpy."
                )
            traceback_lines = traceback.format_exception(*sys.exc_info())
            del traceback_lines[1:2]
            message = ''.join(traceback_lines)
            sys.stderr.write(message)
            return False, message
        finally:
            labscript.labscript_cleanup()
            os.chdir(cwd)
            self.script_module.__dict__.clear()
            self.script_module.__dict__.update(self.script_module_clean_dict)


if __name__ == '__main__':
    process_tree = ProcessTree.connect_to_parent()
    # Set a meaningful name for the zlock client's id:
    process_tree.zlock_client.set_process_name('runmanager.batch_compiler')
    module_watcher = ModuleWatcher()  # Make sure modified modules are reloaded
    # Rename this module to '_runmanager_batch_compiler' and put it in sys.modules under
    # that name. The user's script will become the __main__ module:
    __name__ = '_runmanager_batch_compiler'
    sys.modules[__name__] = sys.modules['__main__']
    batch_processor = BatchProcessor(
        process_tree.to_parent, process_tree.from_parent, process_tree.kill_lock
    )
