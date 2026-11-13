#!/bin/bash
# =============================================================================
# GitHub Repository Setup Script
#
# Run this after creating the repo on GitHub:
#   1. Go to https://github.com/new
#   2. Create: trading-engine (public or private)
#   3. Do NOT initialize with README (we already have one)
#   4. Copy your repo URL and run this script:
#
#   ./scripts/setup_github.sh https://github.com/YOUR_USERNAME/trading-engine.git
# =============================================================================

set -e

REPO_URL="${1:-}"

if [ -z "$REPO_URL" ]; then
  echo "Usage: ./scripts/setup_github.sh https://github.com/YOUR_USERNAME/trading-engine.git"
  exit 1
fi

echo "Setting up remote: $REPO_URL"
git remote add origin "$REPO_URL"
git branch -M main
git push -u origin main

echo ""
echo "✓ Successfully pushed to GitHub!"
echo "  View: ${REPO_URL%.git}"
echo ""
echo "Next steps:"
echo "  1. Update README.md badge URLs with your GitHub username"
echo "  2. Add secrets in GitHub Settings > Secrets & Variables:"
echo "     - ALPACA_API_KEY"
echo "     - ALPACA_SECRET_KEY"
echo "     - OPENAI_API_KEY"
echo "     - POLYGON_API_KEY"
echo "  3. Enable Codecov at https://codecov.io (optional)"
