# erc8004-deepagent-kit

Docker-first standalone SDK for building a LangChain Deep Agent with ERC-8004 identity on Arc Testnet via Circle Developer-Controlled Wallets. Includes x402 payment tools for agent commerce.

## What it does

1. **ERC-8004 Identity**: Register one on-chain identity (ERC-721 NFT) for a Circle DCW wallet on Arc Testnet.
2. **Reputation & Validation**: Read/write ERC-8004 reputation and validation registries.
3. **x402 Payments**: Two modes for agent-to-agent commerce:
   - **Batching**: Circle x402-batching protocol for high-frequency agent payments.
   - **Nanopayment Standalone**: One request = one payment, simpler for demos and single endpoints.

## Quick Start

```bash
cp .env.example .env
# Edit .env with your Circle credentials and DCW wallet
mkdir -p data

# Local (no Docker)
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
npm ci --omit=dev
erc8004-deepagent doctor
erc8004-deepagent status
erc8004-deepagent register

# Docker
docker compose build
docker compose run --rm erc8004-live doctor
docker compose run --rm erc8004-live status
docker compose run --rm erc8004-live register
```

## Commands

```bash
erc8004-deepagent config              # Print safe config (no secrets)
erc8004-deepagent doctor              # Validate env/RPC/contract/chain (+ x402 if X402_ENABLED)
erc8004-deepagent status              # Check identity status (local + on-chain)
erc8004-deepagent register            # Register one identity (idempotent)
erc8004-deepagent clear-expired-locks # Clear stale registration locks
erc8004-deepagent agent-register      # Let the Deep Agent register via tools
```

## Production Checklist

```txt
[ ] docker compose build succeeds
[ ] doctor returns ok=true, all 12+ checks pass
[ ] doctor verifies chain_id=5042002
[ ] doctor verifies bytecode at IdentityRegistry
[ ] status works with configured DCW wallet
[ ] first register returns status=registered + real tx_hash
[ ] tx on ArcScan targets IdentityRegistry
[ ] tx method is register(string)
[ ] tx emits ERC-721 Transfer mint to DCW wallet
[ ] SQLite has exactly one identity row
[ ] second register returns already_registered (no new tx)
[ ] if X402_ENABLED=true, doctor checks: sidecar files, batching package, gateway URL, ledger writable
[ ] if buyer exposed, doctor requires X402_DEFAULT_BUYER_WALLET_ID + X402_ALLOWED_HOSTS non-empty
[ ] if seller exposed, doctor requires X402_DEFAULT_SELLER_WALLET_ADDRESS non-empty
```

## x402 Payment Tools

Two modes for agent commerce on Arc:

### Mode 1: Batching (`X402_MODE=batching`)

For high-frequency agent API marketplace payments. Uses `@circle-fin/x402-batching`.

```bash
X402_ENABLED=true
X402_MODE=batching
X402_DEFAULT_BUYER_WALLET_ID=<dcw-wallet-id>
X402_DEFAULT_SELLER_WALLET_ADDRESS=0x...
X402_ALLOWED_HOSTS=api.vendor.com,agent-api.example.com
X402_MAX_PER_REQUEST_USDC=0.000001
X402_MAX_DAILY_USDC=0.01
X402_MAX_REQUESTS_PER_DAY=100
```

Tools exposed to agent:
- `x402_batch_pay(url, method)` — Buyer: pay for x402-batching endpoint
- `x402_batch_sell_settle(payment_signature, resource, request_id)` — Seller: verify + settle
- `x402_batch_balance(wallet_address)` — Read Gateway balance

### Mode 2: Nanopayment Standalone (`X402_MODE=nano`)

For single paid API calls, demos, lightweight endpoints. 1 request = 1 authorization.

```bash
X402_ENABLED=true
X402_MODE=nano
X402_DEFAULT_BUYER_WALLET_ID=<dcw-wallet-id>
X402_DEFAULT_SELLER_WALLET_ADDRESS=0x...
X402_ALLOWED_HOSTS=api.example.com
X402_MAX_PER_REQUEST_USDC=0.000001
```

Tools exposed to agent:
- `x402_nano_pay(url, method)` — Buyer: one request, one payment
- `x402_nano_sell_settle(payment_signature, resource, request_id)` — Seller: verify/settle
- `x402_nano_balance(wallet_address)` — Read Gateway balance

### Security Model

| Control | Enforcement |
|---------|-------------|
| Wallet from LLM | **Blocked** — always uses `X402_DEFAULT_BUYER_WALLET_ID` from env |
| Max amount from LLM | **Blocked** — always enforces `X402_MAX_PER_REQUEST_USDC` from env |
| Host allowlist | `X402_ALLOWED_HOSTS` — **fail-closed** (empty = block all) |
| Challenge validation | Two-phase: prefetch → `assert_challenge_valid()` in Python → sign |
| Network pinning | Only `eip155:5042002` (Arc Testnet) accepted |
| Asset pinning | Only Arc USDC (`0x3600...0000`) accepted |
| HTTPS | `X402_REQUIRE_HTTPS=true` — reject http:// |
| Daily budget | `X402_MAX_DAILY_USDC` — SQLite ledger tracks cumulative spend |
| Request count | `X402_MAX_REQUESTS_PER_DAY` — SQLite ledger tracks count |
| Idempotency | Seller tools check payment_hash before settling |
| Fail-closed | If limits exceeded → `PermissionError`, no payment signed |

### Agent Exposure Controls

```bash
X402_EXPOSE_BALANCE_TO_AGENT=true       # Always safe — read only
X402_EXPOSE_BATCH_BUYER_TO_AGENT=false  # Enable when ready for batch buying
X402_EXPOSE_BATCH_SELLER_TO_AGENT=false # Enable when ready for batch selling
X402_EXPOSE_NANO_BUYER_TO_AGENT=false   # Enable when ready for nano buying
X402_EXPOSE_NANO_SELLER_TO_AGENT=false  # Enable when ready for nano selling
```

### Docker Commands

```bash
# Batching mode
X402_MODE=batching docker compose run --rm erc8004-live doctor

# Nano standalone mode
X402_MODE=nano docker compose run --rm erc8004-live doctor

# With x402 enabled
X402_ENABLED=true X402_MODE=batching docker compose run --rm erc8004-live config
```

### Doctor x402 Checks

When `X402_ENABLED=true`, `erc8004-deepagent doctor` adds these checks (no transactions, no signing):

- Sidecar files exist (`scripts/x402_batching.mjs`, `scripts/x402_nano.mjs`)
- If `X402_MODE=batching`: `@circle-fin/x402-batching` package is importable
- `X402_GATEWAY_API_URL` is configured
- `X402_LEDGER_PATH` directory is writable
- If buyer tools exposed: `X402_DEFAULT_BUYER_WALLET_ID` and `X402_ALLOWED_HOSTS` non-empty
- If seller tools exposed: `X402_DEFAULT_SELLER_WALLET_ADDRESS` non-empty

## `ERC8004_FROM_BLOCK` — Keep It Current

Set `ERC8004_FROM_BLOCK` to the block **before your agent's first registration** on Arc Testnet.

```bash
# Find your registration tx block on https://testnet.arcscan.app
# Set to that block number (or slightly before)
ERC8004_FROM_BLOCK=41338000
```

Why: the SDK always scans from `ERC8004_FROM_BLOCK` to latest for duplicate prevention. A recent block = faster scan. An old block = more RPC calls but still correct.

Default: `41338000` (registry first Transfer event ~41338604).

## Tech Stack

```txt
Python 3.11+
LangChain / Deep Agents
Web3.py
Circle Developer-Controlled Wallets SDK (Node.js sidecar)
@circle-fin/x402-batching (Node.js sidecar)
SQLite (identity store + x402 spend ledger)
Arc Testnet (chain 5042002)
Docker
```

## Environment Variables

See `.env.example` for the full list with descriptions.

## License

MIT
