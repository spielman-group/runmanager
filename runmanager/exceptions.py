#####################################################################
#                                                                   #
# /exceptions.py                                                    #
#                                                                   #
# Copyright 2013, Monash University                                 #
#                                                                   #
# This file is part of the program runmanager, in the labscript     #
# suite (see http://labscriptsuite.org), and is licensed under the  #
# Simplified BSD License. See the license.txt file in the root of   #
# the project for the full license.                                 #
#                                                                   #
#####################################################################
"""Exceptions raised by runmanager.
"""


class ExpansionError(ValueError):

    """An exception class so that error handling code can tell when a
    parsing exception was caused by a mismatch with the expansion mode"""
