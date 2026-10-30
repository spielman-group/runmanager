#####################################################################
#                                                                   #
# /tests/conftest.py                                                #
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
"""No excepthook dialogs and an offscreen Qt, set before the suite imports either."""
import os

os.environ.setdefault('LABSCRIPT_DISABLE_GRAPHICAL_EXCEPTHOOK', '1')
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
