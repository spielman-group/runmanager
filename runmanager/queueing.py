#####################################################################
#                                                                   #
# /runmanager/queueing.py                                           #
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
"""Runmanager's shot queue: its state, its edits and compile loop, and its widget.

QueueController holds the queue as shot records, safe to use from any thread.
QueueManager makes the queue's edits, including those that delete files or redraw the
queue tab, and owns the background compile loop that Engage uses.
RunmanagerQueueWidget shows each row's file name and compile mode, and reddens a row
that needs an operator.

blacs' ``docs/source/shot-management.rst`` is the contract of record for the BLACS
handover, so change either side against it.
"""

import os
import queue
import threading
import time
import uuid

from qtutils.qt import QtCore, QtGui, QtWidgets
from qtutils.qt.QtCore import pyqtSignal as Signal

from labscript_utils import shared_drive
from labscript_utils.qtwidgets.shotqueue import ShotQueueWidget
from zprocess import raise_exception_in_thread

from runmanager.client import (
    BLACS_CANCELLED,
    BLACS_COMPLETED,
    BLACS_FAILED,
    BLACS_REJECTED,
    BLACS_RUNNING,
    BLACS_WAITING,
    COMPILE_COMPILED,
    COMPILE_COMPILING,
    COMPILE_FAILED,
    COMPILE_WAITING,
    LYSE_WAITING,
    QUEUE_BLOCKED,
    QUEUE_LEFT,
    QUEUE_NOT_QUEUED,
    QUEUE_QUEUED,
)

EMPTY_QUEUE_NOTHING = 'nothing'
EMPTY_QUEUE_DEFAULT_LABSCRIPT = 'default_labscript'
COMPILE_MODE_EAGER = 'eager'
COMPILE_MODE_LAZY = 'lazy'
# Only a failed or rejected row is tinted: it came back without running and is waiting
# for an operator. The reserved first row is set apart by the rule below it, and the
# rows below the rule are work still waiting.
FAILED_ROW_BACKGROUND = QtGui.QColor('#ffcccc')
ROW_BACKGROUNDS = {
    BLACS_FAILED: FAILED_ROW_BACKGROUND,
    BLACS_REJECTED: FAILED_ROW_BACKGROUND,
}
# Set with the fill, not left to the theme: on a dark theme the palette's near-white
# text is unreadable on these pale fills.
TINTED_ROW_FOREGROUND = QtGui.QColor('#202020')
# Fields that record this session's attempt at a shot, not the shot, so a saved queue
# leaves them out:
SESSION_ONLY_FIELDS = (
    'compiling', 'state', 'message', 'reclaimed', 'since', 'blacs_outcome',
    'lyse', 'lyse_message',
)


# The states a row reaches by being given to BLACS. A new state that means a
# handover joins by being named here.
BLACS_STATES = (BLACS_RUNNING, BLACS_FAILED, BLACS_REJECTED, BLACS_CANCELLED)

# The states the queue will not hand a row over in, each against whether a row in it
# also holds up the rows behind. Listing the refusals, not what is left, offers a state
# added later, so a shot is not reported dead while the apparatus is about to run it.
REFUSED_STATES = {
    # BLACS could not read the shot at all (a file that has gone, or a connection table
    # that does not match the apparatus), so offering it again would only be refused
    # again: it is held at the head until an operator deletes it.
    BLACS_REJECTED: True,
    # The operator said the shot is not to be sent. The queue clears the row at BLACS's
    # next request (see drop_cancelled_head()), so the rows behind are only waiting
    # their turn.
    BLACS_CANCELLED: False,
}


def new_shot_id():
    """A fresh identifier for one queued shot."""
    return uuid.uuid4().hex


def sent_to_blacs(row):
    """Whether this row has been handed to BLACS.

    The reserved first row, what Clear leaves alone and a row's BLACS status all turn on
    it, so a state added to BLACS_STATES joins all three."""
    return row.get('state') in BLACS_STATES


def shot_receipt(record):
    """What a remote caller is told about a shot."""
    return {
        'shot_id': record['shot_id'],
        'sequence_id': record['sequence_attrs'].get('sequence_id'),
        'sequence_index': record['sequence_attrs'].get('sequence_index'),
        'run_number': record['run_no'],
        'path': record['path'],
    }


class RunmanagerQueueWidget(ShotQueueWidget):
    """Shot queue widget configured for runmanager-owned shot records."""

    deleteRowsRequested = Signal(list)

    def __init__(self, parent=None):
        super().__init__(
            parent=parent,
            column_titles=['Mode', 'Shot file'],
            path_column=1,
        )
        self.queue_view.deleteRequested.connect(self._emit_delete)
        self.queue_view.setContextMenuPolicy(
            QtCore.Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.queue_view.customContextMenuRequested.connect(self._show_row_menu)

    def _row_info(self, display_item):
        mode = display_item.get('mode', '')
        row_info = {
            'path': display_item['path'],
            'label': display_item.get('label', os.path.basename(display_item['path'])),
            'tooltip': display_item.get('tooltip', display_item['path']),
            'row_id': display_item.get('shot_id'),
            'columns': [
                {
                    'text': mode,
                    'tooltip': f'Compile mode: {mode}' if mode else '',
                    'alignment': QtCore.Qt.AlignmentFlag.AlignCenter,
                }
            ],
        }
        background = ROW_BACKGROUNDS.get(display_item.get('state'))
        if background is not None:
            row_info['background'] = background
            row_info['foreground'] = TINTED_ROW_FOREGROUND
        return row_info

    def _sent_to_blacs_row(self, display_item):
        """The reserved first row: whichever shot has gone to BLACS.

        It is set apart from the queue below by a rule, and keeps the columns so that a
        column added later describes it too. What counts as sent is named in
        BLACS_STATES; a compile failure is not one, as that row never left runmanager.
        """
        if display_item is None:
            return {
                'path': '',
                'label': 'Nothing sent to BLACS',
                'tooltip': 'The shot BLACS was given appears here while it has one.',
                'rule_below': True,
                # Nothing to act on, so nothing to select:
                'selectable': False,
            }
        row_info = self._row_info(display_item)
        row_info['rule_below'] = True
        if display_item['state'] == BLACS_RUNNING:
            row_info['tooltip'] = (
                f"{display_item['path']}\nBLACS has this shot. Delete cancels it: the "
                'row stays until BLACS next asks for work, because its file may still '
                'be being written, but it will not be sent again.'
            )
        elif display_item['state'] == BLACS_CANCELLED:
            # Struck through and not selectable: a second Delete could not free the
            # file any sooner than the first.
            row_info['strikeout'] = True
            row_info['selectable'] = False
        return row_info

    def set_display_items(self, display_items):
        selected_ids = set(self.selected_ids())
        display_items = list(display_items)
        # Only the head can have been sent: it is the row that gets offered,
        # and an outcome either retires it or leaves it there with a state.
        sent = (
            display_items[0]
            if display_items and sent_to_blacs(display_items[0])
            else None
        )
        waiting = display_items[1:] if sent is not None else display_items
        row_infos = [self._sent_to_blacs_row(sent)]
        for display_item in waiting:
            row_infos.append(self._row_info(display_item))
        self.set_row_infos(row_infos)
        self.select_ids(selected_ids)

    def _show_row_menu(self, position):
        shot_ids = self.selected_ids()
        menu = QtWidgets.QMenu(self.queue_view)
        delete = menu.addAction('Delete selected rows')
        delete.setEnabled(bool(shot_ids))
        chosen = menu.exec(self.queue_view.viewport().mapToGlobal(position))
        if chosen is delete:
            self._emit_delete()

    def _emit_delete(self):
        # By id, not position: the queue moves on its own, as when a shot finishing
        # removes a row while another is selected.
        shot_ids = self.selected_ids()
        if shot_ids:
            self.deleteRowsRequested.emit(shot_ids)


class QueueController:
    """Thread-safe queue of shot records."""

    def __init__(self):
        self.empty_queue_policy = EMPTY_QUEUE_NOTHING
        self.default_labscript_file = ''
        self.compile_mode = COMPILE_MODE_EAGER
        # Whether this runmanager is offering its work. Pausing withholds the next shot
        # and leaves the queue as it is, so resuming offers the same head again.
        self.paused = False
        self.last_sent_from_queue = None
        # The sequence of that shot, whose row has left the queue.
        self.last_sent_sequence_attrs = None
        self._items = []
        # The record of each shot that left the queue this session, as it was
        # when it left, and of each shot compiled without being queued.
        self._out_of_queue = {}
        # {local path: shot id} of each file handed to lyse and not yet settled.
        # BLACS may have run a fresh copy, so the path lyse is given is not
        # always the row's.
        self._lyse_paths = {}
        # Whether or not their rows are still queued: a compile that finishes
        # after its row has gone writes its file and then deletes it.
        self._compiling_paths = set()
        self._lock = threading.RLock()

    def _normalise_item(self, item):
        if isinstance(item, str):
            item = {'path': item, 'compile_mode': COMPILE_MODE_EAGER, 'compiled': True}
        record = dict(item)
        record['path'] = os.path.abspath(str(record['path']))
        # One id per queue row, kept across a save and restore and every retry. It names
        # the row, not a run of it, so BLACS's outcome finds its row even if it ran a
        # fresh copy of the file:
        record['shot_id'] = str(record.get('shot_id') or new_shot_id())
        labscript_file = record.get('labscript_file', '')
        record['labscript_file'] = (
            os.path.abspath(str(labscript_file)) if labscript_file else ''
        )
        compile_mode = record.get('compile_mode', COMPILE_MODE_EAGER)
        record['compile_mode'] = compile_mode
        record['compiled'] = bool(record.get('compiled', compile_mode == COMPILE_MODE_EAGER))
        # A shot runmanager made for an empty queue.
        record['default_shot'] = bool(record.get('default_shot', False))
        # Whether runviewer was ticked when this shot was engaged. The
        # compile reads it from here, not from the checkbox as it stands then.
        record['send_to_runviewer'] = bool(record.get('send_to_runviewer', False))
        # A compile in progress belongs to this session only, so a restored
        # shot never starts out claimed:
        record['compiling'] = False
        record['frozen_globals'] = {
            str(name): str(expression)
            for name, expression in record.get('frozen_globals', {}).items()
        }
        record['sequence_attrs'] = {
            str(name): value for name, value in record.get('sequence_attrs', {}).items()
        }
        record['active_groups'] = {
            str(name): os.path.abspath(str(path))
            for name, path in record.get('active_groups', {}).items()
        }
        record['run_no'] = int(record.get('run_no', 0))
        record['n_runs'] = int(record.get('n_runs', 1))
        # 'state' and 'message' say how the row's offer to BLACS stands, and why it did
        # not complete (see offer_next() and shot_finished()). Both belong to this
        # session only, so a restored shot starts with neither:
        record['state'] = ''
        record['message'] = ''
        # 'blacs_outcome' is what BLACS last reported, as 'state' calls an aborted and a
        # failed shot alike 'failed'. 'since' is when the row last changed, for
        # shot_status.
        record['blacs_outcome'] = ''
        record['since'] = time.time()
        # What runmanager did with the file for lyse, once BLACS completes the
        # shot; see record_lyse(). shot_status reports it 'waiting' until the
        # row leaves:
        record['lyse'] = LYSE_WAITING
        record['lyse_message'] = ''
        return record

    def set_empty_queue_policy(self, value):
        if value not in (EMPTY_QUEUE_NOTHING, EMPTY_QUEUE_DEFAULT_LABSCRIPT):
            raise ValueError(f'Invalid empty queue policy: {value}')
        with self._lock:
            self.empty_queue_policy = value

    def set_default_labscript_file(self, value):
        with self._lock:
            self.default_labscript_file = os.path.abspath(value) if value else ''

    def set_compile_mode(self, value):
        if value not in (COMPILE_MODE_EAGER, COMPILE_MODE_LAZY):
            raise ValueError(f'Invalid compile mode: {value}')
        with self._lock:
            self.compile_mode = value

    def set_paused(self, value):
        with self._lock:
            self.paused = bool(value)

    def enqueue(self, items):
        records = [self._normalise_item(item) for item in items]
        with self._lock:
            self._items.extend(records)

    def delete_rows(self, shot_ids):
        """Delete the queued shots with these ids.

        Rows are named by id, not position: the queue moves on its own, and an id that
        has gone deletes nothing instead of whatever took its place.

        A running row is cancelled, not removed, because BLACS may still be using its
        file. It stays struck through until BLACS's next request or an outcome shows the
        file is free, and cannot be deleted again. Waiting, failed and rejected rows are
        removed.

        Returns ``(removed_paths, protected)``: the paths removed and the running rows
        that were cancelled and kept."""
        wanted = set(shot_ids)
        removed_paths = []
        protected = []
        with self._lock:
            keep = []
            for item in self._items:
                if item['shot_id'] not in wanted:
                    keep.append(item)
                elif item['state'] == BLACS_RUNNING:
                    # Marked, not removed, as BLACS may still be using its file. It is
                    # never offered again, and goes when BLACS's next request carries
                    # no outcome for it, which proves the file is free.
                    item['state'] = BLACS_CANCELLED
                    item['message'] = (
                        'Cancelled. It will not be sent again, and goes when '
                        'BLACS next asks for work.'
                    )
                    item['since'] = time.time()
                    protected.append(dict(item))
                    keep.append(item)
                elif item['state'] == BLACS_CANCELLED:
                    keep.append(item)
                else:
                    removed_paths.append(item['path'])
                    self._leave(item, 'Deleted from the queue.')
            self._items = keep
            return removed_paths, protected

    def clear(self):
        """Empty the queue, leaving whatever has been sent to BLACS.

        Used by the submission modes that empty the queue and by Abort, local or remote.
        The queue is the work still waiting: a shot that has gone to BLACS has left it,
        and sits in the row the display reserves above the rest. A running row stays so
        that a file being written is not pulled away, and a failed one because it came
        back needing attention. Discarding it is explicit: select it and delete it.

        Returns ``(removed_paths, protected)``: the paths removed and the rows kept."""
        with self._lock:
            kept = [item for item in self._items if sent_to_blacs(item)]
            dropped = [item for item in self._items if not sent_to_blacs(item)]
            for item in dropped:
                self._leave(item, 'Emptied from the queue.')
            removed_paths = [item['path'] for item in dropped]
            self._items = kept
            return removed_paths, [dict(item) for item in kept]

    def get_queue_paths(self):
        """Return the paths of the queued shots, leaving out the shots
        runmanager produced itself, whose sequence no batch joins."""
        with self._lock:
            return [item['path'] for item in self._items if not item['default_shot']]

    def _status(self, item, queue_step, pending=False):
        """The shot_status record of a row. Call with the lock held.

        For a row that has left the queue, a step it never reached and lyse
        read None, and ``since`` is when it left.
        """
        state = item['state']
        if item['compiling']:
            compile_step = COMPILE_COMPILING
        elif item['compiled']:
            compile_step = COMPILE_COMPILED
        else:
            compile_step = COMPILE_WAITING
        if state == BLACS_FAILED:
            blacs = item['blacs_outcome']
        elif sent_to_blacs(item):
            blacs = state
        else:
            blacs = BLACS_WAITING
        lyse = LYSE_WAITING
        since = item['since']
        if queue_step == QUEUE_LEFT:
            if compile_step not in (COMPILE_COMPILED, COMPILE_FAILED):
                compile_step = None
            if blacs == BLACS_WAITING:
                blacs = None
            lyse = None
            since = time.time()
        return dict(
            shot_receipt(item),
            compile=compile_step,
            queue=queue_step,
            blacs=blacs,
            lyse=lyse,
            pending=pending,
            since=since,
            message=item['message'],
        )

    def _leave(self, item, message, **changes):
        """Record that a row has left the queue, with ``changes`` to its record.

        Call with the lock held.
        """
        self._out_of_queue[item['shot_id']] = dict(
            self._status(item, QUEUE_LEFT), message=message, **changes
        )

    def _row_statuses(self):
        """Yield the record of each row, front first. Call with the lock held.

        Only the head is ever offered, so a row behind one the queue will not
        hand over is ``'blocked'`` and not pending.
        """
        held = False
        for item in self._items:
            state = item['state']
            if state in REFUSED_STATES:
                # Never blocked: its own record is what an operator acts on.
                held = held or REFUSED_STATES[state]
                yield self._status(item, QUEUE_QUEUED, state == BLACS_CANCELLED)
            elif held:
                yield self._status(item, QUEUE_BLOCKED)
            else:
                yield self._status(item, QUEUE_QUEUED, True)

    def track_unqueued(self, items):
        """Keep a record of shots compiled without being queued, as not yet compiled."""
        with self._lock:
            for item in items:
                self._out_of_queue[item['shot_id']] = dict(
                    shot_receipt(item),
                    compile=COMPILE_WAITING,
                    queue=QUEUE_NOT_QUEUED,
                    blacs=None,
                    lyse=None,
                    pending=False,
                    since=time.time(),
                    message='',
                )

    def set_unqueued_compile(self, item, compile_step, message=''):
        """Record how far the compile of a shot that is not queued got."""
        with self._lock:
            self._out_of_queue[item['shot_id']].update(
                compile=compile_step, message=message, since=time.time()
            )

    def expect_lyse(self, shot_id, path):
        """Note that this shot's file is being handed to lyse as ``path``."""
        with self._lock:
            self._lyse_paths[os.path.abspath(path)] = shot_id

    def record_lyse(self, path, lyse, message):
        """Record what runmanager did with a completed shot's file for lyse.

        Called from the analysis submission's thread, for a path given to
        expect_lyse. The outcome can arrive before or after the row is retired: a
        queued row keeps it, and a record that has left takes it while it is still
        waiting."""
        with self._lock:
            shot_id = self._lyse_paths.pop(os.path.abspath(path), None)
            if shot_id is None:
                return
            for item in self._items:
                if item['shot_id'] == shot_id:
                    item['lyse'] = lyse
                    item['lyse_message'] = message
                    return
            record = self._out_of_queue.get(shot_id)
            if record is not None and record['lyse'] == LYSE_WAITING:
                record.update(lyse=lyse, message=message, since=time.time())

    def get_shot_statuses(self, shot_ids):
        """Give the record of each of these shot ids, as ``shot_status`` does."""
        with self._lock:
            live = {record['shot_id']: record for record in self._row_statuses()}
            answer = {}
            for shot_id in shot_ids:
                if shot_id in live:
                    answer[shot_id] = live[shot_id]
                elif shot_id in self._out_of_queue:
                    answer[shot_id] = dict(self._out_of_queue[shot_id])
                else:
                    answer[shot_id] = None
        return answer

    def get_queue(self):
        """List the rows of the queue in queue order, as the client's get_queue does."""
        with self._lock:
            return list(self._row_statuses())

    def get_queued_sequence_attrs(self, path):
        """The sequence attributes of the shot at ``path``, or None if none is recorded.

        The last matching row answers, so a newer row wins over an older one."""
        with self._lock:
            for item in reversed(self._items):
                if item['path'] == path:
                    return dict(item['sequence_attrs']) or None
            if (
                self.last_sent_sequence_attrs
                and self.last_sent_from_queue == os.path.abspath(path)
            ):
                return dict(self.last_sent_sequence_attrs)
        return None

    def get_shot_path(self, shot_id):
        """The path recorded for one queued shot, or None for no row."""
        with self._lock:
            for item in self._items:
                if item['shot_id'] == shot_id:
                    return item['path']
            return None

    def get_queue_display_items(self):
        with self._lock:
            items = []
            for item in self._items:
                compile_mode = item.get('compile_mode', COMPILE_MODE_EAGER)
                mode_label = 'lazy' if compile_mode == COMPILE_MODE_LAZY else 'compiled'
                path = item['path']
                # A failed row says why in its tooltip rather than in a column
                # that would be empty on every other row:
                message = item['message']
                items.append(
                    {
                        'path': path,
                        'label': os.path.basename(path),
                        'mode': mode_label,
                        'tooltip': f'{path}\n{message}' if message else path,
                        'state': item['state'],
                        # Separately from the tooltip it is folded into, because
                        # the reserved row says the reason on its face:
                        'message': message,
                        # What the widget reports back when the operator selects
                        # a row, so that deleting never depends on a position:
                        'shot_id': item['shot_id'],
                    }
                )
            return items

    def set_last_sent_from_queue(self, value, sequence_attrs=None):
        """Record the last shot handed out, by local path, and its sequence.

        Returns whether that changed which shot it is."""
        if value:
            value = os.path.abspath(shared_drive.path_to_local(str(value)))
        else:
            value = None
        with self._lock:
            self.last_sent_sequence_attrs = dict(sequence_attrs or {}) or None
            if self.last_sent_from_queue == value:
                return False
            self.last_sent_from_queue = value
            return True

    def get_save_data(self):
        with self._lock:
            return {
                'empty_queue_policy': self.empty_queue_policy,
                'default_labscript_file': self.default_labscript_file,
                'compile_mode': self.compile_mode,
                'paused': self.paused,
                # Left out: this session's state of each row, which restore_save_data
                # discards and which would prompt a save after every offer, and the
                # default shots, whose globals go stale and whose files may be gone.
                'items': [
                    {
                        name: value
                        for name, value in item.items()
                        if name not in SESSION_ONLY_FIELDS
                    }
                    for item in self._items
                    if not item['default_shot']
                ],
            }

    def restore_save_data(self, state, restore_rows=True):
        with self._lock:
            self.empty_queue_policy = state.get('empty_queue_policy', EMPTY_QUEUE_NOTHING)
            default_labscript_file = state.get('default_labscript_file', '')
            self.default_labscript_file = (
                os.path.abspath(default_labscript_file)
                if default_labscript_file
                else ''
            )
            self.compile_mode = state.get('compile_mode', COMPILE_MODE_EAGER)
            # A configuration written before there was a pause control opens
            # with the queue running, rather than silently stopped:
            self.paused = bool(state.get('paused', False))
            if restore_rows:
                self._items = [
                    self._normalise_item(item) for item in state.get('items', [])
                ]

    def get_queue_state(self):
        with self._lock:
            return {
                'empty_queue_policy': self.empty_queue_policy,
                'default_labscript_file': self.default_labscript_file,
                'compile_mode': self.compile_mode,
                'paused': self.paused,
                'last_sent_from_queue': self.last_sent_from_queue,
                'n_items': len(self._items),
            }

    def drop_overtaken_default_shots(self):
        """Forget default shots that real work has got in front of.

        A default shot is made because the queue was empty when asked, but deciding
        that and enqueueing it are two lock acquisitions, and a batch landing between
        leaves it parked behind work engaged after its globals were frozen. This
        corrects that where it shows, rather than holding the lock across the decision,
        which would hold a filesystem check on the request thread and make BLACS's
        exchange wait behind runmanager's own work. Returns the paths whose files
        should go with them."""
        with self._lock:
            if not self._items or self._items[0]['default_shot']:
                return []
            dropped = [item for item in self._items[1:] if item['default_shot']]
            if dropped:
                self._items = [self._items[0]] + [
                    item for item in self._items[1:] if not item['default_shot']
                ]
            for item in dropped:
                self._leave(
                    item, 'Dropped: work was queued ahead of this default shot.'
                )
            return [item['path'] for item in dropped]

    def drop_cancelled_head(self):
        """Forget a cancelled row, now that nobody can be running it.

        Called when BLACS asks for work. The request's outcome has already been
        applied, so a cancelled row still here is one it said nothing about, and a
        request with no outcome for a row proves nobody is running it: its file is
        free to go. Returns the paths to delete."""
        with self._lock:
            if not self._items or self._items[0]['state'] != BLACS_CANCELLED:
                return []
            item = self._items.pop(0)
            self._leave(item, 'Cancelled; released when BLACS asked for more work.')
            return [item['path']]

    def offer_next(self):
        """Offer the shot at the head of the queue if it is ready to hand over.

        A lazy shot not yet compiled stays in the queue, so that it is neither lost nor
        overtaken by the empty-queue policy while it compiles. The offered shot stays
        too, marked running. A failed row is offered again as well as a waiting one,
        which is the retry; a rejected row is held at the head until it is deleted or
        runmanager is restarted. A cancelled head is removed before this is called.
        Returns a copy of the offered record, or None.

        A row already marked running is offered again too, so that a lost reply cannot
        strand the queue behind it. BLACS is sequential and asks for a shot only when
        it is idle, carrying the outcome of the shot it has just finished on the same
        exchange, so a request without an outcome retiring this row proves BLACS is
        not running it: the offer reply never arrived, or BLACS restarted while holding
        it. queue_exchange() applies the outcome before offering, so a row BLACS has
        just reported on is not mistaken for a stranded one, and only offer_shot() may
        call this, because only there does a call mean that BLACS asked for work. The
        returned copy says in ``reclaimed`` whether the row was such a one.

        That holds for one BLACS per runmanager: a second BLACS asking the same
        runmanager would be handed the row the first is still executing. Running means
        offered and not yet reported on, not that the shot is on the hardware."""
        with self._lock:
            if not self._items or not self._items[0]['compiled']:
                return None
            item = self._items[0]
            if item['state'] in REFUSED_STATES:
                # A row the queue will not hand over (see REFUSED_STATES) is held
                # here, marked, until an operator deletes it or the queue clears it.
                # BLACS keeps asking, gets nothing, and runs its own shot.
                return None
            reclaimed = item['state'] == BLACS_RUNNING
            item['state'] = BLACS_RUNNING
            if not reclaimed:
                item['since'] = time.time()
            # Whatever went wrong last time is being attempted again, so the row looks
            # as it does when running, without the old reason:
            item['message'] = ''
            record = dict(item)
            record['reclaimed'] = reclaimed
            return record

    def shot_finished(self, shot_id, status, message=''):
        """Record how BLACS says the shot it was offered turned out.

        The outcome names the row by id, so it applies to the row that was offered
        whichever file BLACS ran. Completion removes the row, and so does any outcome
        for a cancelled row; the caller still handles a completed shot for analysis. An
        aborted or failed shot stays red at the head and is offered again on the next
        request. A rejected shot stays red but is held until deleted or runmanager is
        restarted.

        Returns the row when an outcome changes queue state, or None when it does not.
        An identical repeated rejection is ignored while the row is still rejected.
        After a failed or aborted row is offered again, an identical failure is a
        retry's outcome and is reported again."""
        message = str(message)
        with self._lock:
            for index, item in enumerate(self._items):
                if item['shot_id'] != shot_id:
                    continue
                if status == BLACS_COMPLETED or item['state'] == BLACS_CANCELLED:
                    # A cancelled row goes on any outcome at all: the operator does not
                    # want this shot, so a failure is no reason to try it again, and a
                    # completed one is still reported onward by the caller.
                    changes = {'blacs': status}
                    if status == BLACS_COMPLETED:
                        changes.update(compile=COMPILE_COMPILED, lyse=item['lyse'])
                        # The latest change to the shot is what its message says:
                        if item['lyse'] != LYSE_WAITING:
                            message = item['lyse_message']
                    self._leave(item, message, **changes)
                    return self._items.pop(index)
                state = BLACS_REJECTED if status == BLACS_REJECTED else BLACS_FAILED
                # Catches a resend only while the row still shows this outcome, in
                # practice a rejected row. A failed row is offered again and reset, so
                # its resend is reported again: a resend and a second run that failed
                # identically are the same bytes, and the protocol has no token to tell
                # them apart. A repeated line is the safer error (see ReplayTests).
                if item['state'] == state and item['message'] == message:
                    return None
                item['state'] = state
                item['blacs_outcome'] = status
                item['message'] = message
                item['since'] = time.time()
                return dict(item)
            return None

    def claim_next_for_compile(self):
        """Claim the shot at the head of the queue for compilation.

        Returns ``(item, pending)``. ``item`` is the shot to compile, or None
        if there is nothing to start. ``pending`` is True while a queued shot
        is not yet ready to hand over, whether this call claimed it or another
        compile is already under way."""
        with self._lock:
            if not self._items:
                return None, False
            item = self._items[0]
            if item['compiled']:
                return None, False
            if item['compiling']:
                return None, True
            item['compiling'] = True
            item['since'] = time.time()
            self._compiling_paths.add(item['path'])
            return item, True

    def claim_next_to_compile_ahead(self):
        """Claim the first eager row still to be compiled, or return None.

        Wherever it is in the queue, so that a batch compiles in order ahead of
        BLACS."""
        with self._lock:
            for item in self._items:
                if (
                    item['compile_mode'] == COMPILE_MODE_EAGER
                    and not item['compiled']
                    and not item['compiling']
                ):
                    item['compiling'] = True
                    item['since'] = time.time()
                    self._compiling_paths.add(item['path'])
                    return item
            return None

    def get_compiling_paths(self):
        """The paths of the shots being compiled now."""
        with self._lock:
            return set(self._compiling_paths)

    def finish_compile(self, item, success, message=''):
        """Record the outcome of a background compile.

        A shot that failed to compile leaves the queue, since it would never
        run, and the shots behind it carry on.

        Returns ``(changed, still_queued)``: whether the queue changed, and
        whether the shot is still queued. It is not if it failed, or if the
        operator deleted it while it was compiling."""
        with self._lock:
            item['compiling'] = False
            item['since'] = time.time()
            self._compiling_paths.discard(item['path'])
            item['compiled'] = bool(success)
            for queued in self._items:
                if queued is item:
                    break
            else:
                return False, False
            if not success:
                self._items.remove(item)
                self._leave(item, str(message), compile=COMPILE_FAILED)
                return True, False
            item['state'] = ''
            item['message'] = ''
            return True, True


class QueueManager(QtCore.QObject):
    """The queue's edits, and the worker thread that compiles shots.

    State operations act directly on the controller, which is safe to use from any
    thread. Only compilation is handed to the worker thread, so that a long Engage
    batch cannot hold up a queue-tab control or a BLACS request for the next shot."""

    queueChanged = Signal()
    # Why a shot could not be prepared or compiled, when that raised:
    compileFailed = Signal(str)

    def __init__(
        self,
        controller,
        prepare_run_file,
        compile_run_file,
        send_to_runviewer,
        output,
    ):
        super().__init__()
        self.controller = controller
        self.command_queue = queue.Queue()
        self.prepare_run_file_callback = prepare_run_file
        self.compile_run_file_callback = compile_run_file
        self.send_to_runviewer_callback = send_to_runviewer
        self.output = output
        # A compile_shots batch is given the event that is current when it is
        # submitted, and stopping replaces it, so an Engage after Empty queue is
        # not stopped by the click that came before it.
        self._compile_shots_stop = threading.Event()
        self.thread = threading.Thread(target=self.mainloop, daemon=True)
        self.thread.start()

    def shutdown(self):
        self.command_queue.put(('close', ()))
        if self.thread.is_alive() and threading.current_thread() is not self.thread:
            self.thread.join(timeout=1)

    def enqueue(self, items):
        self.controller.enqueue(list(items))
        self.queueChanged.emit()

    def compile_ahead(self):
        """Ask the worker to compile the queued eager rows not yet compiled."""
        self.command_queue.put(('compile_ahead', ()))

    def compile_shots(self, records, send_to_blacs, send_to_runviewer):
        """Queue these records if send_to_blacs, and compile them.

        Returns the records, each now carrying its shot id and its batch's
        ``send_to_runviewer``. A batch not bound for the queue is compiled for
        runviewer and queues nothing; one that is queued compiles its eager
        rows in order, and BLACS asks for the lazy ones."""
        records = list(records)
        for record in records:
            # Set before enqueue so the returned record and the shot file carry
            # the row's id.
            record['shot_id'] = new_shot_id()
            record['send_to_runviewer'] = send_to_runviewer
        if send_to_blacs:
            self.enqueue(records)
            self.compile_ahead()
            return records
        self.controller.track_unqueued(records)
        self.command_queue.put(('compile_shots', (records, self._compile_shots_stop)))
        return records

    def stop_compile_shots(self):
        """Stop the compile_shots batches submitted so far, after the shot each is on."""
        self._compile_shots_stop.set()
        self._compile_shots_stop = threading.Event()

    def compile_shot(self, item, default_globals=False):
        """Write one shot's file, compile it, and send it to runviewer if asked.

        The record's ``send_to_runviewer`` says whether it is asked. With
        ``default_globals`` the file is written from the globals' defaults, as
        a default shot is, rather than from the item's frozen globals.

        Returns ``(success, error)``: whether it compiled and, if not, the
        compile's traceback. An exception is emitted as ``compileFailed`` and raised.
        """
        # A header, so that the output of one compile does not run into the last:
        self.output(f"Compiling {os.path.basename(item['path'])}\n")
        try:
            self.prepare_run_file_callback(item, default_globals=default_globals)
            success, error = self.compile_run_file_callback(
                item['labscript_file'], item['path']
            )
        except Exception as exc:
            self.compileFailed.emit(
                f"Could not compile shot {os.path.basename(item['path'])}: {exc}"
            )
            raise
        if success and item['send_to_runviewer']:
            self.send_to_runviewer_callback(item['path'])
        # Not marked compiled here: for a queued row that is finish_compile's to do
        # under the controller's lock, or the row would be offerable before the compile
        # is recorded, and its running state wiped when the compile finishes.
        return success, error

    def compile_next_in_background(self):
        """Start compiling the shot at the head of the queue if it is not ready.

        Returns True while a queued shot is pending, so the caller reports it
        as pending rather than falling back to the empty-queue policy.

        The compile runs on its own thread rather than through the worker's
        command queue, so that it is not held up behind an Engage batch. The
        compiler lock is taken per shot, so it starts at the next shot
        boundary."""
        item, pending = self.controller.claim_next_for_compile()
        if item is not None:
            thread = threading.Thread(
                target=self._background_compile, args=(item,), daemon=True
            )
            thread.start()
        return pending

    def _compile_saying_why(self, item):
        """Compile the shot, returning ``(success, message)``.

        The message is what the shot's record says if it failed: the traceback of a
        failure in the user's script, or the exception of a failure before that."""
        try:
            success, error = self.compile_shot(item)
            return success, f'Could not be compiled:\n{error}'
        except Exception as exc:
            self.output(
                f"Could not compile shot {os.path.basename(item['path'])}: {exc}\n",
                red=True,
            )
            return False, f'Could not be compiled: {exc}'

    def _background_compile(self, item):
        success, message = self._compile_saying_why(item)
        changed, still_queued = self.controller.finish_compile(item, success, message)
        if not still_queued:
            # It failed, or was deleted while it compiled, so its file goes.
            self.delete_shot_files([item['path']])
        if not success and changed:
            self.output(
                f"Queued shot {os.path.basename(item['path'])} could not be compiled, "
                'and has left the queue.\n',
                red=True,
            )
        if changed:
            self.queueChanged.emit()

    def set_last_sent_from_queue(self, value, sequence_attrs=None):
        if self.controller.set_last_sent_from_queue(value, sequence_attrs):
            self.queueChanged.emit()

    def delete_shot_files(self, paths):
        """Delete the files of shots runmanager has finished with.

        A file already gone is passed over; one that cannot be deleted is
        reported."""
        for path in paths:
            try:
                os.remove(path)
            except FileNotFoundError:
                continue
            except Exception as exc:
                self.output(
                    f'Could not delete shot {os.path.basename(path)}: {exc}\n',
                    red=True,
                )

    def set_empty_queue_policy(self, value):
        self.controller.set_empty_queue_policy(value)
        self.queueChanged.emit()

    def set_default_labscript_file(self, value):
        self.controller.set_default_labscript_file(value)
        self.queueChanged.emit()

    def set_compile_mode(self, value):
        self.controller.set_compile_mode(value)
        self.queueChanged.emit()

    def set_paused(self, value):
        self.controller.set_paused(value)
        self.queueChanged.emit()

    def _remove_from_queue(self, removed_paths, protected):
        """Delete the files of the rows that went, and say why a protected row stayed.

        The reason goes in the output box, as a line rather than a dialog, because the
        rest of what the operator asked for has happened. It is read off the row:
        Delete keeps only the row BLACS is executing, but Clear keeps every row that
        went to BLACS, including ones that came back failed or rejected, of which
        "BLACS is running it" would be untrue. Returns the paths that were removed."""
        for row in protected:
            if row['state'] == BLACS_RUNNING:
                reason = 'BLACS is running it.'
            elif row['state'] == BLACS_CANCELLED:
                # Not something BLACS said: the operator did this, and the row
                # already carries the whole of it.
                reason = row['message']
            else:
                reason = f"BLACS reported it as {row['state']}" + (
                    f": {row['message']}" if row['message'] else '.'
                )
            self.output(
                f"Kept queued shot {os.path.basename(row['path'])}: {reason}\n",
                red=True,
            )
        if removed_paths:
            self.delete_shot_files(removed_paths)
            self.queueChanged.emit()
        return removed_paths

    def delete_rows(self, shot_ids):
        return self._remove_from_queue(*self.controller.delete_rows(list(shot_ids)))

    def clear(self):
        return self._remove_from_queue(*self.controller.clear())

    def offer_next(self):
        cancelled = self.controller.drop_cancelled_head()
        if cancelled:
            self.delete_shot_files(cancelled)
        overtaken = self.controller.drop_overtaken_default_shots()
        if overtaken:
            self.delete_shot_files(overtaken)
        item = self.controller.offer_next()
        if item is not None:
            if item['reclaimed']:
                # The offer it was marked running for never got there. The row looks no
                # different afterwards, so say so: a reply that keeps going missing is
                # worth an operator knowing about.
                self.output(
                    f"Shot {os.path.basename(item['path'])} was still marked as running "
                    'in the queue; offering it to BLACS again.\n',
                    red=True,
                )
            self.queueChanged.emit()
        return item

    def shot_finished(self, shot_id, status, message=''):
        record = self.controller.shot_finished(shot_id, status, message)
        if record is None:
            # The outcome changed nothing: it names a row that has gone, or one already
            # carrying this failure, as when BLACS resends because our reply went
            # missing. It is neither reported again nor allowed to repaint the queue.
            return None
        if status != BLACS_COMPLETED:
            self.output(
                f"BLACS reported shot {os.path.basename(record['path'])} as {status}"
                + (f': {message}' if message else '')
                + '\n',
                red=True,
            )
        self.queueChanged.emit()
        return record

    def restore_save_data(self, state, restore_rows=True):
        state = dict(state or {})
        state['items'] = [
            dict(item) if isinstance(item, dict) else item
            for item in state.get('items', [])
        ]
        owners = [(state, 'The queue')]
        if restore_rows:
            owners += [
                (item, os.path.basename(str(item.get('path', ''))))
                for item in state['items']
                if isinstance(item, dict)
            ]
        # Only a hand-edited file holds a setting runmanager does not know. Its
        # default replaces it, as the dropdowns and the rows can show no other:
        for record, owner in owners:
            for key, default, other in (
                ('empty_queue_policy', EMPTY_QUEUE_NOTHING, EMPTY_QUEUE_DEFAULT_LABSCRIPT),
                ('compile_mode', COMPILE_MODE_EAGER, COMPILE_MODE_LAZY),
            ):
                if record.get(key, default) not in (default, other):
                    self.output(
                        f'{owner} was saved with {key} {record[key]!r}, which '
                        f'runmanager does not know, so it is {default!r}.\n',
                        red=True,
                    )
                    record[key] = default
        self.controller.restore_save_data(state, restore_rows)
        self.queueChanged.emit()

    def mainloop(self):
        while True:
            try:
                try:
                    command, args = self.command_queue.get(timeout=1)
                except queue.Empty:
                    continue

                if command == 'close':
                    return

                if command == 'compile_ahead':
                    item = self.controller.claim_next_to_compile_ahead()
                    while item is not None:
                        self._background_compile(item)
                        item = self.controller.claim_next_to_compile_ahead()
                    self.output('Ready.\n\n')
                elif command == 'compile_shots':
                    records, stop = args
                    aborted = False
                    # A shot that fails does not stop the rest, as in the queue.
                    for item in records:
                        if aborted or stop.is_set():
                            aborted = True
                            self.controller.set_unqueued_compile(
                                item, None, 'Not compiled: compilation was aborted.'
                            )
                            continue
                        self.controller.set_unqueued_compile(item, COMPILE_COMPILING)
                        success, message = self._compile_saying_why(item)
                        if success:
                            self.controller.set_unqueued_compile(item, COMPILE_COMPILED)
                        else:
                            self.controller.set_unqueued_compile(item, COMPILE_FAILED, message)
                            self.output(
                                f"Shot {os.path.basename(item['path'])} "
                                'could not be compiled.\n',
                                red=True,
                            )
                    if aborted:
                        self.output('Compilation aborted.\n\n', red=True)
                    else:
                        self.output('Ready.\n\n')
                else:
                    raise ValueError(f'Invalid queue command: {command}')
            except Exception as exc:
                raise_exception_in_thread((type(exc), exc, exc.__traceback__))
