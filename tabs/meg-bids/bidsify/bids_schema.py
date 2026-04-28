"""
BIDS Schema validator — validates completed BIDS conversions and checks metadata compliance.
Supports MEG, EEG, and hybrid modalities with intelligent schema validation.
Focuses on post-conversion validation (not pre-conversion checks on raw data).
"""

import json
import mne
from os.path import exists, join
from typing import Dict, List, Any, Optional
from pathlib import Path
import pandas as pd
import os


# BIDS required fields by modality (core fields that must be present)
BIDS_REQUIRED_FIELDS = {
    'meg': {
        'dataset_description': ['Name', 'BIDSVersion', 'DatasetType'],
        'meg_json': ['TaskName', 'SamplingFrequency', 'PowerLineFrequency'],
        'channels': ['name', 'x', 'y', 'z', 'type'],
    },
    'eeg': {
        'dataset_description': ['Name', 'BIDSVersion', 'DatasetType'],
        'eeg_json': ['TaskName', 'SamplingFrequency', 'PowerLineFrequency', 'EEGReference'],
        'channels': ['name', 'x', 'y', 'z', 'type'],
    },
}


class BIDSSchemaValidator:
    """
    Validates BIDS metadata compliance and identifies data quality issues.
    """

    def __init__(self, bids_root: str, config: dict):
        """
        Initialize validator with BIDS root path and config.
        """
        self.bids_root = bids_root
        self.config = config
        self.issues = []
        self.modality = config.get('Modality', 'meg').lower()
        self.bids_validator_output = None
        self._load_bids_validator_results()

    def validate_dataset_structure(self) -> List[Dict[str, Any]]:
        """
        Validate dataset-level structure (dataset_description.json, README, etc.).
        """
        issues = []

        # Check dataset_description.json
        desc_file = join(self.bids_root, 'dataset_description.json')
        if not exists(desc_file):
            issue = {
                'file': 'dataset_description.json',
                'category': 'BIDS',
                'severity': 'error',
                'issue': 'Missing dataset_description.json at BIDS root',
                'suggestion': 'Create dataset_description.json with required fields: Name, BIDSVersion, DatasetType',
                'affected_fields': ['dataset_description.json'],
                'bids_level': 'dataset'
            }
            issues.append(issue)
        else:
            try:
                with open(desc_file, 'r') as f:
                    desc = json.load(f)

                required = BIDS_REQUIRED_FIELDS['meg']['dataset_description']
                for field in required:
                    if field not in desc:
                        issue = {
                            'file': 'dataset_description.json',
                            'category': 'BIDS',
                            'severity': 'error',
                            'issue': f'Missing required field: {field}',
                            'suggestion': f'Add "{field}" to dataset_description.json',
                            'affected_fields': [field],
                            'bids_level': 'dataset'
                        }
                        issues.append(issue)
            except json.JSONDecodeError:
                issue = {
                    'file': 'dataset_description.json',
                    'category': 'BIDS',
                    'severity': 'error',
                    'issue': 'Invalid JSON in dataset_description.json',
                    'suggestion': 'Fix JSON syntax errors',
                    'affected_fields': [],
                    'bids_level': 'dataset'
                }
                issues.append(issue)

        # Check README
        readme_file = join(self.bids_root, 'README')
        readme_md = join(self.bids_root, 'README.md')

        readme_path = None
        if exists(readme_file):
            readme_path = readme_file
        elif exists(readme_md):
            readme_path = readme_md

        if not readme_path:
            issue = {
                'file': 'README',
                'category': 'BIDS',
                'severity': 'warning',
                'issue': 'Missing README at BIDS root',
                'suggestion': 'Create README with dataset description, acquisition information, and instructions',
                'affected_fields': [],
                'bids_level': 'dataset'
            }
            issues.append(issue)

        # Check participants.tsv
        participants_file = join(self.bids_root, 'participants.tsv')
        if not exists(participants_file):
            issue = {
                'file': 'participants.tsv',
                'category': 'BIDS',
                'severity': 'warning',
                'issue': 'Missing participants.tsv',
                'suggestion': 'Create participants.tsv with at least: participant_id, age, sex',
                'affected_fields': [],
                'bids_level': 'dataset'
            }
            issues.append(issue)

        return issues

    def _load_bids_validator_results(self) -> None:
        """Load BIDS validator output if available."""
        bids_results_path = join(self.bids_root, 'bids_results.json')
        if exists(bids_results_path):
            try:
                with open(bids_results_path, 'r') as f:
                    self.bids_validator_output = json.load(f)
            except:
                self.bids_validator_output = None

    def _get_bids_hierarchy_level(self, file_path: str) -> str:
        """
        Determine the BIDS hierarchy level for a file path.
        """
        if not file_path or file_path in ['dataset', 'multiple']:
            return 'dataset'

        import re
        has_subject = re.search(r'sub-([^_/]+)', file_path)
        has_session = re.search(r'ses-([^_/]+)', file_path)
        has_filename = '.' in file_path

        if has_filename:
            return 'file'
        elif has_session:
            return 'session'
        elif has_subject:
            return 'subject'
        else:
            return 'dataset'

    def run_full_validation(self, conversion_table: Optional[pd.DataFrame] = None) -> Dict[str, List[Dict[str, Any]]]:
        """
        Run full validation suite and return comprehensive report.
        """
        results = {
            'dataset_structure': self.validate_dataset_structure(),
            'metadata_consistency': [],
            'all_issues': []
        }

        results['all_issues'] = results['dataset_structure'] + results['metadata_consistency']

        return results
