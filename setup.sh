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

# --- Create local data dirs ---
mkdir -p ./data ./data/circle_executions

# --- Create .env if missing ---
if [ ! -f .env ]; then
  cp .env.example .env
  python3 - <<'PYENV'
from pathlib import Path
path = Path('.env')
text = path.read_text()
replacements = {
    'IDENTITY_STORE_PATH': './data/erc8004_identities.sqlite3',
    'REPUTATION_STORE_PATH': './data/erc8004_reputation.sqlite3',
    'X402_LEDGER_PATH': './data/x402_spend_ledger.sqlite3',
    'CIRCLE_EXECUTION_STATE_DIR': './data/circle_executions',
}
lines = []
seen = set()
for line in text.splitlines():
    key = line.split('=', 1)[0] if '=' in line and not line.startswith('#') else None
    if key in replacements:
        lines.append(f"{key}={replacements[key]}")
        seen.add(key)
    else:
        lines.append(line)
for key, value in replacements.items():
    if key not in seen:
        lines.append(f"{key}={value}")
path.write_text('\n'.join(lines) + '\n')
PYENV
  echo ""
  echo "⚠️  Created .env from .env.example"
  echo "   Local SQLite/state paths were set under ./data"
  echo "   Edit .env and fill in:"
  echo "   - CIRCLE_API_KEY"
  echo "   - CIRCLE_ENTITY_SECRET"
  echo "   - DCW_WALLET_ADDRESS"
  echo ""
else
  echo "✅ .env already exists"
  python3 - <<'PYENV'
from pathlib import Path
path = Path('.env')
text = path.read_text()
replacements = {
    'IDENTITY_STORE_PATH': './data/erc8004_identities.sqlite3',
    'REPUTATION_STORE_PATH': './data/erc8004_reputation.sqlite3',
    'X402_LEDGER_PATH': './data/x402_spend_ledger.sqlite3',
    'CIRCLE_EXECUTION_STATE_DIR': './data/circle_executions',
}
seen = {}
for line in text.splitlines():
    if '=' in line and not line.startswith('#'):
        key, value = line.split('=', 1)
        seen[key] = value.strip()
missing = [key for key in replacements if not seen.get(key)]
if missing:
    with path.open('a') as f:
        f.write('\n# Local-safe paths added by setup.sh\n')
        for key in missing:
            f.write(f'{key}={replacements[key]}\n')
if any(seen.get(key, '').startswith('/data') for key in replacements):
    print('⚠️  Local setup detected /data paths. Docker can use /data, but local installs should use ./data unless /data is writable.')
PYENV
fi

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
