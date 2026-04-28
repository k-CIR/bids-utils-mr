"""
BIDS QA Agent — AI-powered quality assessment for completed MEG/EEG BIDS datasets.
Performs post-conversion validation to detect dataset-level inconsistencies.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
import pandas as pd

from .bids_schema import BIDSSchemaValidator


class BIDSQAAgent:
    """
    AI-powered QA agent for BIDS datasets.
    Performs automated quality checks and generates structured findings.
    """

    def __init__(self, bids_root: str, config: dict, enable_llm: bool = False):
        """
        Initialize QA agent.
        """
        self.bids_root = bids_root
        self.config = config
        self.enable_llm = enable_llm
        self.schema_validator = BIDSSchemaValidator(bids_root, config)
        self.findings = []
        self.summary = {}

    def analyze_dataset(self, conversion_table: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
        """
        Run comprehensive QA analysis on entire dataset.
        """
        self.findings = []
        self.summary = {
            'dataset': os.path.basename(self.bids_root),
            'timestamp': datetime.now().isoformat(),
            'modality': self.config.get('Modality', 'meg').lower(),
            'total_issues': 0,
            'severity_counts': {'error': 0, 'warning': 0, 'info': 0},
            'categories': {},
            'recommendations': []
        }

        # Run schema validation on BIDS output
        validation_results = self.schema_validator.run_full_validation(conversion_table)

        for issue in validation_results['all_issues']:
            self._process_finding(issue)

        # Analyze completed conversions for dataset-level inconsistencies
        if conversion_table is not None and not conversion_table.empty:
            dataset_issues = self.analyze_completed_dataset(conversion_table)
            for issue in dataset_issues:
                self._process_finding(issue)

        # Calculate summary statistics
        self._update_summary()

        return {
            'findings': self.findings,
            'summary': self.summary,
            'timestamp': datetime.now().isoformat()
        }

    def analyze_completed_dataset(self, conversion_table: pd.DataFrame) -> List[Dict[str, Any]]:
        """
        Analyze completed conversions for post-processing inconsistencies.
        """
        issues = []

        if conversion_table.empty:
            return issues

        # Only analyze processed/successful conversions
        completed = conversion_table[conversion_table['status'] == 'processed']
        if completed.empty:
            return issues

        # Detect subject-level inconsistencies
        all_subjects = completed['participant_to'].dropna().unique()
        all_sessions = completed['session_to'].dropna().unique()
        all_tasks = completed['task'].dropna().unique()

        # Check for incomplete sessions per subject
        if len(all_sessions) > 1:
            for subject in all_subjects:
                subj_data = completed[completed['participant_to'] == subject]
                subj_sessions = subj_data['session_to'].dropna().unique()

                if len(subj_sessions) < len(all_sessions):
                    missing_sessions = set(all_sessions) - set(subj_sessions)
                    issues.append({
                        'file': f"sub-{subject}",
                        'category': 'Dataset Consistency',
                        'severity': 'warning',
                        'issue': f'Subject missing sessions: {", ".join(map(str, missing_sessions))}',
                        'suggestion': f'Verify if sub-{subject} should have all sessions or mark as expected',
                        'affected_fields': ['session'],
                        'bids_level': 'subject'
                    })

        return issues

    def _process_finding(self, issue: Dict[str, Any]) -> None:
        """
        Process individual finding and update internal state.
        """
        issue.setdefault('severity', 'info')
        issue.setdefault('category', 'Other')

        if 'bids_level' not in issue:
            issue['bids_level'] = self.schema_validator._get_bids_hierarchy_level(issue.get('file', ''))

        self.findings.append(issue)

        severity = issue.get('severity', 'info')
        if severity in self.summary['severity_counts']:
            self.summary['severity_counts'][severity] += 1

    def _update_summary(self) -> None:
        """Update summary statistics."""
        self.summary['total_issues'] = len(self.findings)

        for finding in self.findings:
            cat = finding.get('category', 'Other')
            if cat not in self.summary['categories']:
                self.summary['categories'][cat] = 0
            self.summary['categories'][cat] += 1

    def format_report(self, detailed: bool = False) -> str:
        """
        Format findings as human-readable report.
        """
        lines = []
        lines.append("=" * 70)
        lines.append("BIDS QA REPORT")
        lines.append("=" * 70)
        lines.append(f"Dataset: {self.summary['dataset']}")
        lines.append(f"Timestamp: {self.summary['timestamp']}")
        lines.append(f"Modality: {self.summary['modality']}")
        lines.append("")

        lines.append("SUMMARY")
        lines.append("-" * 70)
        lines.append(f"Total Issues: {self.summary['total_issues']}")
        lines.append(f"Errors: {self.summary['severity_counts']['error']}, "
                     f"Warnings: {self.summary['severity_counts']['warning']}, "
                     f"Info: {self.summary['severity_counts']['info']}")
        lines.append("")

        if self.summary['categories']:
            lines.append("Issues by Category:")
            for cat, count in sorted(self.summary['categories'].items()):
                lines.append(f"  {cat}: {count}")
            lines.append("")

        if detailed and self.findings:
            lines.append("DETAILED FINDINGS")
            lines.append("-" * 70)
            for i, finding in enumerate(self.findings, 1):
                lines.append(f"\n{i}. [{finding.get('severity', 'info').upper()}] {finding.get('issue', 'Unknown')}")
                lines.append(f"   Category: {finding.get('category', 'Other')}")
                lines.append(f"   File: {finding.get('file', 'N/A')}")
                if finding.get('suggestion'):
                    lines.append(f"   Suggestion: {finding['suggestion']}")

        lines.append("\n" + "=" * 70)
        return "\n".join(lines)
