#!/usr/bin/env python3
"""CLI entry point for bidsify module.

Usage:
    python -m bidsify --config config.json --analyse
    python -m bidsify --config config.json --run
    python -m bidsify --config config.json --report
"""
import sys
from .pipeline import main

if __name__ == "__main__":
    sys.exit(0 if main() else 1)
