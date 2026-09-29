"""
Main CLI entry point for Audi A6 C5 Monitoring Service.

Usage:
    python -m src.main [--once] [--dry-run] [--source <source_name>] [--config <path>]
"""

import sys
from src.runner import main

if __name__ == "__main__":
    main()
