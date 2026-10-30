#####################################################################
#                                                                   #
# /runmanager/blacs_status.py                                       #
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
"""What the BLACS that runmanager offers shots to is doing, in words."""

import os

import labscript_utils.shared_drive as shared_drive


def blacs_state(status):
    """Describe what BLACS is doing, for the BLACS link indicator.

    ``status`` is BLACS's answer to ``get_status``. Returns ``(state, details)``:
    ``state`` is shown beside the BLACS light and says whether BLACS is requesting
    shots, the shot it is running, or what stopped it; ``details`` are tooltip
    lines, BLACS's own activity and the shot it is running.
    """
    # BLACS sends the path shared-drive-agnostic, so put it in this machine's form:
    shot_path = shared_drive.path_to_local(str(status.get('shot_path') or ''))
    error = str(status.get('error') or '')
    if error:
        state = f'stopped - {error}'
    elif shot_path:
        # A shot under way is named even once BLACS stops requesting more.
        state = f'running {os.path.basename(shot_path)}'
    elif status.get('requesting_shots'):
        state = 'requesting shots'
    else:
        state = 'not requesting shots'
    details = []
    activity = str(status.get('status') or '')
    if activity:
        details.append(f'Activity: {activity}')
    if shot_path:
        details.append(f'Shot: {shot_path}')
    shot_id = status.get('shot_id')
    if shot_id:
        details.append(f'Shot id: {shot_id}')
    elif shot_path:
        # BLACS reports no id for a local override shot, which is in no runmanager's
        # queue:
        details.append('Not a queued shot: BLACS local override')
    return state, details
