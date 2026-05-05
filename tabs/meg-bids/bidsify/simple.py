"""Simplified BIDS conversion functions with minimal config requirements.

This module provides config-free alternatives for common BIDS conversion tasks.
"""

import json
import os
from glob import glob
from os.path import join, exists, dirname
from typing import Optional, Dict, Any
import pandas as pd

from .constants import DERIVATIVES_SUBFOLDER, CALIBRATION_PATH, CROSSTALK_PATH
from .conversion_table import generate_new_conversion_table, load_conversion_table, CONVERSION_COLUMNS
from .pipeline import bidsify as _bidsify_core
from .templates import create_dataset_description, create_participants_files, create_proc_description
from .sidecars import update_sidecars
from .utils import setLogPath


# Default configuration for simplified mode
DEFAULT_SIMPLE_CONFIG = {
    'Name': 'MEG Dataset',
    'Root': '',
    'Raw': '',
    'BIDS': '',
    'Tasks': [],
    'Conversion_file': 'logs/bids_conversion.tsv',
    'overwrite': False,
    'Overwrite_conversion': False,
    'Participants_mapping_file': '',
    'Dataset_description': 'dataset_description.json',
    'InstitutionName': '',
    'InstitutionDepartmentName': '',
}


def get_default_config() -> Dict[str, Any]:
    """Return a copy of the default configuration."""
    return DEFAULT_SIMPLE_CONFIG.copy()


def load_minimal_config(config_path: str) -> Dict[str, Any]:
    """
    Load a minimal JSON configuration file.

    Expected format:
    {
        "project_name": "My Study",
        "raw_dir": "raw/natmeg",
        "bids_dir": "BIDS",
        "tasks": ["rest", "task1"],
        "conversion_file": "logs/bids_conversion.tsv",
        "overwrite": false
    }
    """
    with open(config_path, 'r') as f:
        minimal = json.load(f)

    # Prefer explicit project root hints; otherwise infer from config file location.
    raw_dir = minimal.get('raw_dir', '')
    config_root = minimal.get('root') or minimal.get('project_root')
    if not config_root:
        config_root = dirname(os.path.abspath(config_path))
    
    # Convert minimal config to full config format
    config = DEFAULT_SIMPLE_CONFIG.copy()
    config.update({
        'Name': minimal.get('project_name', 'MEG Dataset'),
        'Root': config_root,
        'Raw': raw_dir,
        'BIDS': minimal.get('bids_dir', ''),
        'Tasks': minimal.get('tasks', []),
        'Conversion_file': minimal.get('conversion_file', 'logs/bids_conversion.tsv'),
        'overwrite': minimal.get('overwrite', False),
        # Use fixed calibration/crosstalk paths from constants
        'Calibration': CALIBRATION_PATH,
        'Crosstalk': CROSSTALK_PATH,
    })

    return config


def create_conversion_table_simple(
    raw_dir: str,
    bids_dir: str,
    tasks: list = None,
    output_file: str = None
) -> pd.DataFrame:
    """
    Create a conversion table without requiring a full config file.

    Args:
        raw_dir: Path to raw MEG data directory
        bids_dir: Path to BIDS output directory
        tasks: List of expected task names
        output_file: Optional path to save the conversion table

    Returns:
        DataFrame with conversion table
    """
    config = DEFAULT_SIMPLE_CONFIG.copy()
    config['Raw'] = raw_dir
    config['BIDS'] = bids_dir
    config['Root'] = dirname(raw_dir)
    if tasks:
        config['Tasks'] = tasks

    # Generate conversion table
    results = list(generate_new_conversion_table(config))
    df = pd.DataFrame(results)

    if output_file:
        df.to_csv(output_file, sep='\t', index=False)
        print(f"Conversion table saved to: {output_file}")

    return df


def bidsify_simple(
    config_path: Optional[str] = None,
    raw_dir: Optional[str] = None,
    bids_dir: Optional[str] = None,
    tasks: Optional[list] = None,
    overwrite: bool = False,
    verbose: bool = False
) -> bool:
    """
    Run BIDS conversion with minimal configuration.

    Args:
        config_path: Path to minimal JSON config (optional)
        raw_dir: Path to raw data (if no config_path)
        bids_dir: Path to BIDS output (if no config_path)
        tasks: List of task names (if no config_path)
        overwrite: Whether to overwrite existing BIDS files
        verbose: Enable verbose output

    Returns:
        True if successful, False otherwise
    """
    if config_path:
        config = load_minimal_config(config_path)
    else:
        config = DEFAULT_SIMPLE_CONFIG.copy()
        config['Raw'] = raw_dir or ''
        config['BIDS'] = bids_dir or ''
        config['Root'] = dirname(raw_dir) if raw_dir else ''
        config['Tasks'] = tasks or []
        config['overwrite'] = overwrite
        # Use fixed calibration/crosstalk paths from constants
        config['Calibration'] = CALIBRATION_PATH
        config['Crosstalk'] = CROSSTALK_PATH

    try:
        # Load or create conversion table
        conversion_table, conversion_file = load_conversion_table(config, refresh_status=True)

        if conversion_table.empty:
            print("No files found to convert")
            return False

        # Run conversion
        _bidsify_core(config, conversion_table=conversion_table, conversion_file=conversion_file, verbose=verbose)

        # Update sidecars
        update_sidecars(config)

        return True

    except Exception as e:
        print(f"Error during BIDS conversion: {e}")
        return False


def generate_report_simple(bids_dir: str, output_file: str = None) -> Dict[str, Any]:
    """
    Generate a simple QA report for a BIDS dataset.

    Args:
        bids_dir: Path to BIDS dataset
        output_file: Optional path to save JSON report

    Returns:
        Dictionary with report data
    """
    config = DEFAULT_SIMPLE_CONFIG.copy()
    config['BIDS'] = bids_dir

    from .qa_agent import BIDSQAAgent

    agent = BIDSQAAgent(bids_dir, config, enable_llm=False)

    # Create empty conversion table for analysis
    conversion_table = pd.DataFrame(columns=CONVERSION_COLUMNS)

    results = agent.analyze_dataset(conversion_table)

    if output_file:
        with open(output_file, 'w') as f:
            json.dump(results, f, indent=2)
        print(f"Report saved to: {output_file}")

    return results
