"""BIDSify package for MEG BIDS conversion.

This package provides tools for converting raw MEG/EEG data to BIDS format.
"""

from .constants import (
    EXCLUDE_PATTERNS,
    NOISE_PATTERNS,
    HEADPOS_PATTERNS,
    OPM_EXEPCIONS_PATTERNS,
    PROC_PATTERNS,
    CONVERSION_TABLE_FIELDS,
    DERIVATIVES_SUBFOLDER,
)
from .utils import setLogPath, file_contains, get_parameters
from .parsing import extract_info_from_filename, get_split_file_parts, bids_path_from_rawname
from .templates import create_dataset_description, create_participants_files, create_proc_description
from .sidecars import update_sidecars, add_channel_parameters, copy_eeg_to_meg
from .conversion_table import generate_new_conversion_table, load_conversion_table, update_conversion_table
from .pipeline import bidsify, update_bids_report, args_parser, main

__version__ = '1.0.0'

__all__ = [
    'EXCLUDE_PATTERNS',
    'NOISE_PATTERNS',
    'HEADPOS_PATTERNS',
    'OPM_EXEPCIONS_PATTERNS',
    'PROC_PATTERNS',
    'CONVERSION_TABLE_FIELDS',
    'DERIVATIVES_SUBFOLDER',
    'setLogPath',
    'file_contains',
    'get_parameters',
    'extract_info_from_filename',
    'get_split_file_parts',
    'bids_path_from_rawname',
    'create_dataset_description',
    'create_participants_files',
    'create_proc_description',
    'update_sidecars',
    'add_channel_parameters',
    'copy_eeg_to_meg',
    'generate_new_conversion_table',
    'load_conversion_table',
    'update_conversion_table',
    'bidsify',
    'update_bids_report',
    'args_parser',
    'main',
]
