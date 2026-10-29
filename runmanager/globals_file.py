#####################################################################
#                                                                   #
# /globals_file.py                                                  #
#                                                                   #
# Copyright 2026, Ian Spielman                                      #
#                                                                   #
# This file is part of the program runmanager, in the labscript     #
# suite (see http://labscriptsuite.org), and is licensed under the  #
# Simplified BSD License. See the license.txt file in the root of   #
# the project for the full license.                                 #
#                                                                   #
#####################################################################

"""Storage helpers for runmanager globals files.

This module keeps the globals-file schema and CRUD operations in one place.
Canonical editable globals files are TOML-backed. Legacy HDF5 globals files
remain readable as migration inputs and can be converted to TOML when the user
wants to edit them.
"""

import copy
import functools
import os
import threading

import labscript_utils.h5_lock
import h5py

from labscript_utils import ensure_str
from labscript_utils.file_utils import warn_legacy_file
from labscript_profile.toml_config import dump_toml_file, load_toml_file


CANONICAL_FORMAT = "runmanager_globals"
CURRENT_VERSION = 1
LEGACY_HDF5_EXTENSIONS = {".h5", ".hdf5"}
# Held for each read and each load-modify-save. The GUI, remote calls and the
# preparse edit globals files from different threads, and an edit that
# interleaves with another loses one of them.
_file_lock = threading.RLock()


def _locked(function):
    @functools.wraps(function)
    def locked(*args, **kwargs):
        with _file_lock:
            return function(*args, **kwargs)
    return locked


class GlobalsFileError(Exception):
    pass


class LegacyGlobalsFileError(GlobalsFileError):
    pass


def _blank_record():
    return {
        "default": "",
        "units": "",
        "scan_enabled": False,
        "jit_enabled": False,
        "scan": "",
        "expansion": "",
    }


def _normalise_record(record):
    normalised = dict(record)
    normalised.update(
        {
            "default": ensure_str(record.get("default", "")),
            "units": ensure_str(record.get("units", "")),
            "scan_enabled": bool(record.get("scan_enabled", False)),
            "jit_enabled": bool(record.get("jit_enabled", False)),
            "scan": ensure_str(record.get("scan", "")),
            "expansion": ensure_str(record.get("expansion", "")),
        }
    )
    if normalised["scan_enabled"] and normalised["jit_enabled"]:
        normalised["jit_enabled"] = False
    # The stored expansion is retained while a scan is disabled, so that a
    # zip group survives a scan off/on cycle. runmanager.get_globals()
    # reports an empty expansion for globals that are not being scanned, so
    # retaining it here does not affect how the global is expanded.
    if normalised["scan_enabled"] and not normalised["expansion"]:
        normalised["expansion"] = "outer"
    return normalised


def _new_document():
    return {
        "format": CANONICAL_FORMAT,
        "version": CURRENT_VERSION,
        "groups": {},
    }


def _normalise_document(data):
    # Keys not recognised here are data, and are kept at every level.
    document = {**data, "groups": {}}
    document["format"] = ensure_str(data.get("format", CANONICAL_FORMAT))
    document["version"] = int(data.get("version", CURRENT_VERSION))
    groups = data.get("groups", {})
    for group_name, group_data in groups.items():
        if not isinstance(group_data, dict):
            raise GlobalsFileError(f'Group "{group_name}" is not a table.')
        document["groups"][ensure_str(group_name)] = {**group_data, "globals": {}}
        for global_name, record in group_data.get("globals", {}).items():
            if not isinstance(record, dict):
                raise GlobalsFileError(
                    f'Global "{global_name}" in group "{group_name}" is not a table.'
                )
            document["groups"][ensure_str(group_name)]["globals"][ensure_str(global_name)] = (
                _normalise_record(record)
            )
    return document


def detect_backend(globals_file):
    ext = os.path.splitext(globals_file)[1].lower()
    if ext == ".toml":
        return "toml"
    if ext in LEGACY_HDF5_EXTENSIONS:
        return "legacy_hdf5"
    raise GlobalsFileError(
        "Unsupported globals file format for %s. Expected .toml, .h5, or .hdf5."
        % globals_file
    )


def is_legacy_hdf5(globals_file):
    return detect_backend(globals_file) == "legacy_hdf5"


def default_toml_path(globals_file):
    root, _ = os.path.splitext(globals_file)
    return root + ".toml"


@_locked
def load_document(globals_file):
    backend = detect_backend(globals_file)
    if backend == "toml":
        return load_toml_document(globals_file)
    return load_legacy_hdf5_document(globals_file)


def load_toml_document(globals_file):
    data = load_toml_file(globals_file)
    document = _normalise_document(data)
    if document["format"] != CANONICAL_FORMAT:
        raise GlobalsFileError(
            "Unsupported globals TOML format %r in %s." % (document["format"], globals_file)
        )
    return document


def load_legacy_hdf5_document(globals_file):
    # LEGACY HDF5 GLOBALS FILES. DEPRECATED CODE, WILL BE REMOVED.
    warn_legacy_file(globals_file, 'is an HDF5 globals file, not TOML')
    document = _new_document()
    with h5py.File(globals_file, "r") as f:
        try:
            globals_root = f["globals"]
        except KeyError as exc:
            raise GlobalsFileError("%s does not contain a /globals group." % globals_file) from exc
        for group_name in globals_root:
            group = f["globals"][group_name]
            units_group = group.get("units")
            expansion_group = group.get("expansion")
            values = dict(group.attrs)
            units = dict(units_group.attrs) if units_group is not None else {}
            expansions = dict(expansion_group.attrs) if expansion_group is not None else {}
            document["groups"][group_name] = {"globals": {}}
            for global_name, value in values.items():
                # A global the h5 file expands is one it scans:
                expansion = ensure_str(expansions.get(global_name, ""))
                document["groups"][group_name]["globals"][global_name] = {
                    "default": ensure_str(value),
                    "units": ensure_str(units.get(global_name, "")),
                    "scan_enabled": bool(expansion),
                    "jit_enabled": False,
                    "scan": ensure_str(value) if expansion else "",
                    "expansion": expansion,
                }
    return document


@_locked
def save_document(globals_file, document):
    if detect_backend(globals_file) != "toml":
        raise LegacyGlobalsFileError(
            "Legacy HDF5 globals files are import-only. Save to a .toml globals file instead."
        )
    document = _normalise_document(document)
    directory = os.path.dirname(globals_file)
    if directory:
        os.makedirs(directory, exist_ok=True)
    # Every edit to a single global rewrites the whole file, which is replaced
    # whole: an interrupted save keeps the globals that were there.
    dump_toml_file(globals_file, document)


def new_globals_file(globals_file):
    save_document(globals_file, _new_document())


def get_group_names(globals_file):
    document = load_document(globals_file)
    return list(document["groups"])


def get_globals_details(groups):
    details = {}
    for filepath in set(groups.values()):
        document = load_document(filepath)
        groups_from_file = [group_name for group_name, path in groups.items() if path == filepath]
        for group_name in groups_from_file:
            group = document["groups"][group_name]["globals"]
            details[group_name] = {
                global_name: copy.deepcopy(record) for global_name, record in group.items()
            }
    return details


def _require_editable(globals_file):
    if is_legacy_hdf5(globals_file):
        raise LegacyGlobalsFileError(
            "Legacy HDF5 globals files are import-only. Convert this file to .toml before editing."
        )


def _get_group(document, group_name):
    try:
        return document["groups"][group_name]["globals"]
    except KeyError as exc:
        raise GlobalsFileError('No such group "%s".' % group_name) from exc


@_locked
def new_group(globals_file, group_name):
    _require_editable(globals_file)
    document = load_document(globals_file)
    if group_name in document["groups"]:
        raise GlobalsFileError("Can't create group: target name already exists.")
    document["groups"][group_name] = {"globals": {}}
    save_document(globals_file, document)


@_locked
def copy_group(source_globals_file, source_group_name, dest_globals_file, delete_source_group=False):
    source_document = load_document(source_globals_file)
    if dest_globals_file is None:
        dest_globals_file = source_globals_file
    _require_editable(dest_globals_file)
    dest_document = load_document(dest_globals_file)
    try:
        source_group = source_document["groups"][source_group_name]["globals"]
    except KeyError as exc:
        raise GlobalsFileError(
            'Can\'t copy: there is no group "%s".' % source_group_name
        ) from exc
    i = 0 if not delete_source_group else 1
    dest_group_name = source_group_name
    while dest_group_name in dest_document["groups"]:
        if i == 0:
            dest_group_name = "%s_copy" % source_group_name
        else:
            dest_group_name = "%s(%d)" % (source_group_name, i)
        i += 1
    dest_document["groups"][dest_group_name] = {
        "globals": copy.deepcopy(source_group),
    }
    save_document(dest_globals_file, dest_document)
    return dest_group_name


@_locked
def rename_group(globals_file, previous_group_name, new_group_name):
    _require_editable(globals_file)
    if previous_group_name == new_group_name:
        return
    document = load_document(globals_file)
    if new_group_name in document["groups"]:
        raise GlobalsFileError("Can't rename group: target name already exists.")
    try:
        document["groups"][new_group_name] = document["groups"].pop(previous_group_name)
    except KeyError as exc:
        raise GlobalsFileError('No such group "%s".' % previous_group_name) from exc
    save_document(globals_file, document)


@_locked
def delete_group(globals_file, group_name):
    _require_editable(globals_file)
    document = load_document(globals_file)
    try:
        del document["groups"][group_name]
    except KeyError as exc:
        raise GlobalsFileError('No such group "%s".' % group_name) from exc
    save_document(globals_file, document)


@_locked
def new_globals(globals_file, group_name, records):
    """Add globals to a group, ``{name: record}``, or none if any name is taken.

    A field a record leaves out is blank."""
    _require_editable(globals_file)
    document = load_document(globals_file)
    group = _get_group(document, group_name)
    taken = sorted(set(records) & set(group))
    if taken:
        raise GlobalsFileError(
            "Can't create globals: %s already exist." % ", ".join(taken)
        )
    for global_name, record in records.items():
        group[global_name] = _normalise_record({**_blank_record(), **record})
    save_document(globals_file, document)


@_locked
def rename_global(globals_file, group_name, previous_global_name, new_global_name):
    _require_editable(globals_file)
    if previous_global_name == new_global_name:
        return
    document = load_document(globals_file)
    group = _get_group(document, group_name)
    if new_global_name in group:
        raise GlobalsFileError("Can't rename global: target name already exists.")
    try:
        group[new_global_name] = group.pop(previous_global_name)
    except KeyError as exc:
        raise GlobalsFileError('No such global "%s".' % previous_global_name) from exc
    save_document(globals_file, document)


@_locked
def delete_global(globals_file, group_name, global_name):
    _require_editable(globals_file)
    document = load_document(globals_file)
    group = _get_group(document, group_name)
    try:
        del group[global_name]
    except KeyError as exc:
        raise GlobalsFileError('No such global "%s".' % global_name) from exc
    save_document(globals_file, document)


def get_global_record(globals_file, group_name, global_name):
    document = load_document(globals_file)
    group = _get_group(document, group_name)
    try:
        return copy.deepcopy(group[global_name])
    except KeyError as exc:
        raise GlobalsFileError('No such global "%s".' % global_name) from exc


@_locked
def set_global_record(globals_file, group_name, global_name, record):
    _require_editable(globals_file)
    document = load_document(globals_file)
    group = _get_group(document, group_name)
    if global_name not in group:
        raise GlobalsFileError('No such global "%s".' % global_name)
    group[global_name] = _normalise_record(record)
    save_document(globals_file, document)


def get_field(globals_file, group_name, global_name, field):
    return get_global_record(globals_file, group_name, global_name)[field]


@_locked
def set_field(globals_file, group_name, global_name, field, value):
    record = get_global_record(globals_file, group_name, global_name)
    record[field] = value
    # Saving keeps Scan? over JIT? and gives a scan an expansion, so ticking
    # JIT? is the one change that must untick Scan? itself:
    if field == "jit_enabled" and value:
        record["scan_enabled"] = False
    set_global_record(globals_file, group_name, global_name, record)


def convert_to_toml(source_globals_file, dest_globals_file):
    document = load_document(source_globals_file)
    save_document(dest_globals_file, document)
    return dest_globals_file
