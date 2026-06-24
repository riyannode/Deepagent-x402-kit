#!/usr/bin/env bash
set -euo pipefail

echo "╔══════════════════════════════════════════╗"
echo "║   ERC-8004 Deep Agent Kit — Setup        ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# --- Check Python ---
if ! command -v python3 &>/dev/null; then
  echo "❌ python3 not found. Install Python 3.11+ first."
  exit 1
fi

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "✅ Python $PY_VERSION"

# --- Check Node ---
if ! command -v node &>/dev/null; then
  echo "❌ node not found. Install Node.js 18+ first."
  exit 1
fi
echo "✅ Node $(node -v)"

# --- Create venv ---
echo ""
echo "📦 Creating virtual environment..."
python3 -m venv .venv
source .venv/bin/activate

# --- Install Python deps ---
echo "📦 Installing Python dependencies..."
pip install -U pip -q
pip install -e . -q

# --- Install Node deps ---
echo "📦 Installing Node.js sidecar dependencies..."
npm ci --omit=dev --silent 2>/dev/null || npm install --omit=dev --silent

# --- Create .env if missing ---
if [ ! -f .env ]; then
  cp .env.example .env
  echo ""
  echo "⚠️  Created .env from .env.example"
  echo "   Edit .env and fill in:"
  echo "   - CIRCLE_API_KEY"
  echo "   - CIRCLE_ENTITY_SECRET"
  echo "   - DCW_WALLET_ADDRESS"
  echo ""
else
  echo "✅ .env already exists"
fi

# --- Create data dirs ---
mkdir -p /data 2>/dev/null || true

# --- Validate ---
echo ""
echo "🔍 Validating configuration..."
erc8004-deepagent config 2>/dev/null || echo "⚠️  Config check needs .env values"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   ✅ Setup complete!                      ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "Next steps:"
echo "  1. Edit .env with your Circle credentials"
echo "  2. Run: source .venv/bin/activate"
echo "  3. Run: erc8004-deepagent doctor"
echo "  4. Run: erc8004-deepagent register"
echo ""
echo "CLI commands:"
echo "  erc8004-deepagent config              # Show config"
echo "  erc8004-deepagent doctor              # Validate everything"
echo "  erc8004-deepagent status              # Check identity"
echo "  erc8004-deepagent register            # Register agent"
echo ""
echo "Agent chat:"
echo "  python -m erc8004_deepagent_kit       # Start Deep Agent"
echo ""
