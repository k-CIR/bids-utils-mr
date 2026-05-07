"""
Legacy functions for backwards compatibility.
These functions are maintained for reference but may be deprecated in future versions.
"""

import pandas as pd
from os.path import exists, join
from glob import glob
import os


def legacy_load_conversion_table(config: dict):
    """
    Legacy conversion table loader for backwards compatibility.
    """
    from .conversion_table import load_conversion_table
    return load_conversion_table(config)


def legacy_update_conversion_table(config: dict, conversion_file: str = None):
    """
    Legacy conversion table updater for backwards compatibility.
    """
    from .conversion_table import update_conversion_table
    return update_conversion_table(config, conversion_file)
