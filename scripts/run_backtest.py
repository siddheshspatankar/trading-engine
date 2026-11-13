#!/usr/bin/env python3
"""
Standalone backtest runner script.

Usage:
    python scripts/run_backtest.py \
        --strategy momentum \
        --symbols AAPL,MSFT,GOOGL \
        --start 2020-01-01 \
        --end 2023-12-31 \
        --capital 100000
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.main import cli

if __name__ == "__main__":
    cli(["backtest"] + sys.argv[1:])
