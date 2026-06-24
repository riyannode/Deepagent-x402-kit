# deepagent x402 Kit

Standalone SDK for Arc/Circle and single-wallet ERC-8004 + x402 DeepAgent built on **LangChain**.

One Circle Developer-Controlled Wallet (DCW) = one on-chain ERC-8004 agent identity (ERC-721 NFT) on Arc Testnet. The Deep Agent can bootstrap its identity once via `register_identity_once`. Optional Circle x402 payment tools are policy-gated and off by default.

**Built on LangChain / Deep Agents.** The agent logic, tools, system prompt, and LLM routing are all standard LangChain — you can fork this repo and modify the agent behavior, add tools, change the model, swap the system prompt, or integrate it into any LangChain-compatible pipeline. The SDK provides building blocks, not a black box.

---

## Architecture

```
Deepagent-x402-kit/
├── src/erc8004_deepagent_kit/
│   ├── agent.py                  # LangChain Deep Agent builder (tools + system prompt)
│   ├── cli.py                    # CLI: doctor, status, register, config
│   ├── config.py                 # KitConfig singleton from .env (all settings)
│   ├── __main__.py               # python -m entry
│   ├── deepagent/
│   │   └── system_prompt.py      # Agent system prompt (editable)
│   ├── erc8004/
│   │   ├── abi_identity.py       # IdentityRegistry ABI (includes balanceOf)
│   │   ├── abi_reputation.py     # ReputationRegistry ABI
│   │   ├── abi_validation.py     # ValidationRegistry ABI
│   │   ├── addresses.py          # Zero address constants
│   │   ├── events.py             # Transfer event parsing, MintEvent
│   │   ├── metadata.py           # Registration file builder + data URI
│   │   ├── receipts.py           # IdentityReceipt dataclass
│   │   └── registry_clients.py   # On-chain RPC clients (identity, validation)
│   ├── mcp/
│   │   ├── schemas.py            # MCP tool schemas
│   │   └── server.py             # MCP server entry
│   ├── plugins/
│   │   ├── erc8183/disabled.py   # ERC-8183 plugin (future)
│   │   └── x402/disabled.py      # x402 plugin (replaced by tools/)
│   ├── store/
│   │   ├── identity_store.py     # IdentityStore interface
│   │   └── sqlite_store.py       # SQLite implementation (identity + locks)
│   ├── tools/
│   │   ├── identity.py           # LangChain tools: register_identity_once, get_identity_status
│   │   ├── registry_status.py    # get_erc8004_config
│   │   ├── reputation.py         # Reputation read/write tools
│   │   ├── validation.py         # Validation read/write tools
│   │   ├── x402_batching.py      # x402_batch_pay, x402_batch_sell_settle, x402_batch_balance
│   │   └── x402_nano.py          # x402_nano_pay, x402_nano_sell_settle, x402_nano_balance
│   ├── wallet/
│   │   ├── contract_executor.py  # Circle DCW sidecar executor (Node.js subprocess)
│   │   ├── dcw.py                # get_configured_wallet()
│   │   └── policy.py             # WalletPolicy — allowed contracts + function signatures
│   └── x402/
│       ├── __init__.py
│       ├── ledger.py             # SQLite x402_spend_ledger (daily budget + request count)
│       └── policy.py             # Host allowlist, HTTPS, amount, challenge validation
├── scripts/
│   ├── circle_execute_contract.mjs   # DCW contract execution sidecar
│   ├── x402_batching.mjs             # Circle x402-batching sidecar (BatchFacilitatorClient)
│   └── x402_nano.mjs                 # Nanopayment standalone sidecar (Gateway REST)
├── Dockerfile
├── docker-compose.yml
├── docker-entrypoint.sh
├── Makefile
├── pyproject.toml
├── package.json
└── .env.example
```

---

## ERC-8004 Identity

### What happens when you register

`register_identity_once` does exactly one thing: mints one ERC-721 identity NFT on the IdentityRegistry contract at `0x8004A818BFB912233c491871b3d84c89A494BD9e` on Arc Testnet (chain ID 5042002).

The identity NFT is minted to the configured DCW wallet address. The SDK enforces one registration per wallet by scanning historical mint events. One wallet = one identity. The agent URI is a `data:application/json;base64,...` containing the agent metadata (name, description, image, services, x402 support).

### On-chain duplicate scan

Before submitting a registration transaction, the SDK scans **all** ERC-721 `Transfer` mint events from `ERC8004_FROM_BLOCK` to `latest` in chunks of `EVENT_SCAN_BLOCK_RANGE` (default 10,000 blocks).

**There is no fast-path shortcut.**

- No `balanceOf` shortcut — `balanceOf > 0` does NOT skip the scan. The scan is the source of truth.
- No auto-narrow scan range — the SDK always scans the full configured range.
- If `get_logs` fails, the exception propagates. The SDK does NOT return `None` and does NOT proceed to submit a transaction.

This means:
- **First-time scan** on a large block range may take 1–5 minutes (hundreds of RPC calls). This is by design.
- **Subsequent scans** are fast because the SDK caches the registration in local SQLite after the first successful scan or registration.

If the scan finds an existing identity for your wallet, the SDK returns `already_registered` (from local store) or `already_registered_onchain` (from on-chain) and **creates no new Circle transaction**.

### `ERC8004_FROM_BLOCK` — keep it current

Set `ERC8004_FROM_BLOCK` to the block **before your agent's first registration** on Arc Testnet.

```bash
# Find your registration tx block on https://testnet.arcscan.app
# Set to that block number (or slightly before)
ERC8004_FROM_BLOCK=41338000
```

A recent block = faster scan. An old block = more RPC calls but still correct.

> **Warning:** Never set `ERC8004_FROM_BLOCK` after any previous registration transaction for this wallet. If it is set too recent, duplicate-prevention can miss the historical mint. If unsure, use the registry first Transfer event / default block even if scanning is slower.

Default: `41338000` (registry first Transfer event ~41338604).

### Agent Metadata Format

ERC-8004 stores a metadata URI on-chain as the `tokenURI` of the identity NFT. The JSON fields at that URI are **application-defined** — there is no enforced schema from Arc or Circle. This kit uses a richer format than the [Arc quickstart default](https://docs.arc.io/arc/tutorials/register-your-first-ai-agent) to support agent-to-agent discovery, x402 payment negotiation, and trust mechanisms.

**This kit's metadata format:**

```json
{
  "type": "https://eips.ethereum.org/EIPS/eip-8004#registration-v1",
  "name": "My Deep Agent",
  "description": "Agent description",
  "image": "https://example.com/agent.png",
  "services": [
    { "name": "discovery", "endpoint": "https://my-agent.example.com/api/discover" }
  ],
  "x402Support": true,
  "active": true,
  "registrations": [],
  "supportedTrust": ["reputation", "validation"]
}
```

**Arc quickstart default (simpler):**

```json
{
  "name": "DeFi Arbitrage Agent v1.0",
  "description": "Autonomous trading agent",
  "image": "ipfs://QmAgentAvatarHash...",
  "agent_type": "trading",
  "capabilities": ["arbitrage_detection"],
  "version": "1.0.0"
}
```

### Data URI vs IPFS

This kit stores metadata as a `data:application/json;base64,...` URI (self-contained, no external dependency). The [Arc quickstart](https://docs.arc.io/arc/tutorials/register-your-first-ai-agent) recommends IPFS.

| | Data URI (this kit) | IPFS (Arc quickstart) |
|---|---------------------|----------------------|
| **Dependency** | None — fully self-contained | Requires IPFS pinning service (Pinata, NFT.Storage, etc.) |
| **Size limit** | 32KB (enforced by SDK) | No practical limit |
| **Gas cost** | Higher — data embedded in tx calldata | Lower — only CID stored on-chain |
| **Persistence** | Lives on-chain forever | Depends on pinning service |

Switch to IPFS in Production, for large metadata, and decentralized storage 

To use IPFS instead, upload your metadata JSON to [Pinata](https://pinata.cloud/), [NFT.Storage](https://nft.storage/), or [Web3.Storage](https://web3.storage/), then pass the `ipfs://...` URI to the registration call. The on-chain contract doesn't care — any valid URI works.

### What `register_identity_once` does NOT do

- Does NOT submit a second transaction if identity already exists.
- Does NOT accept `wallet_id` from the LLM. The wallet comes from `.env`.
- Does NOT reveal secrets (API key, entity secret) in any output.

---

## Gateway Funding (Required for x402)

Before using x402 payment tools, deposit USDC to the Circle Gateway. Without a Gateway balance, x402 payments will fail.

| | Value |
|---|-------|
| Gateway Address | `0x0077777d7EBA4688BDeF3E311b846F25870A19B9` |
| Network | Arc Testnet (chain ID 5042002) |
| Token | USDC (`0x3600000000000000000000000000000000000000`) |
| Faucet | https://faucet.circle.com |

**Quick start:**

1. Get testnet USDC from https://faucet.circle.com
2. Ask the agent to deposit: *"Deposit 0.01 USDC to Gateway"*
3. Check balance: *"What's my Gateway balance?"*

**Under the hood**, the agent calls:

```
gateway_deposit("0.01")
  → DCW wallet transfers 0.01 USDC to Gateway address
  → Returns tx_hash + explorer_url

x402_batch_balance("0xYourWallet")
  → Reads Gateway balance (no payment)
```

**How it works:**
- `gateway_deposit` calls `transfer(address,uint256)` on the USDC contract via Circle DCW
- The DCW wallet sends USDC to the Gateway wallet address
- Gateway tracks balance per sender address
- x402 payments deduct from this balance
- Gateway batches settlements on-chain (buyer pays 0 gas)

**Safety limits:**
- Max single deposit: 100 USDC
- Only transfers to Gateway address allowed (policy-enforced)
- WalletPolicy rejects USDC transfers to any other destination

---

## Circle x402 Payment Tools

Two mutually exclusive modes. Set `X402_MODE` to choose one.

### Mode 1: Batching (`X402_MODE=batching`)

For high-frequency agent commerce. Uses `@circle-fin/x402-batching` (BatchFacilitatorClient) for seller verify/settle.

**Buyer flow** (`x402_batch_pay`):
1. Python validates URL against host allowlist + HTTPS policy
2. Python checks daily budget + request count in SQLite ledger
3. Sidecar prefetches the 402 challenge (no signing)
4. Python validates challenge: network, asset, scheme, amount, payTo, resource
5. Python inserts pending ledger row
6. Sidecar signs EIP-712 typed data via DCW `signTypedData`
7. Sidecar retries request with payment header
8. Python updates ledger to `success` or `failed`

**Seller flow** (`x402_batch_sell_settle`):
1. Python checks idempotency cache (payment_hash uniqueness)
2. Python inserts pending ledger row
3. Sidecar calls `BatchFacilitatorClient.verify()` then `.settle()`
4. Python updates ledger with tx_hash

**Tools exposed to agent:**
- `gateway_deposit(amount_usdc)` — Deposit USDC to Gateway (required before x402 payments)
- `x402_batch_pay(url, method="GET")` — Buyer: pay for x402-batching endpoint
- `x402_batch_sell_settle(payment_signature, resource, request_id)` — Seller: verify + settle
- `x402_batch_balance(wallet_address)` — Read Gateway balance

### Mode 2: Nanopayment Standalone (`X402_MODE=nano`)

For single paid API calls, demos, lightweight endpoints. 1 request = 1 payment authorization. Uses Circle Gateway REST API directly (no BatchFacilitatorClient).

**Buyer flow** (`x402_nano_pay`): Same two-phase validation as batching.

**Seller flow** (`x402_nano_sell_settle`): Same idempotency + ledger as batching, but uses Gateway `/v1/x402/verify` and `/v1/x402/settle` REST endpoints instead of BatchFacilitatorClient.

**Tools exposed to agent:**
- `x402_nano_pay(url, method="GET")` — Buyer: one request, one payment
- `x402_nano_sell_settle(payment_signature, resource, request_id)` — Seller: verify/settle
- `x402_nano_balance(wallet_address)` — Gateway balance read

### Agent exposure controls

Buyer/seller tools are **not exposed** to the Deep Agent unless explicitly enabled:

```bash
X402_EXPOSE_BALANCE_TO_AGENT=true       # Default: read-only balance
X402_EXPOSE_BATCH_BUYER_TO_AGENT=false  # Opt-in: batch buyer
X402_EXPOSE_BATCH_SELLER_TO_AGENT=false # Opt-in: batch seller
X402_EXPOSE_NANO_BUYER_TO_AGENT=false   # Opt-in: nano buyer
X402_EXPOSE_NANO_SELLER_TO_AGENT=false  # Opt-in: nano seller
```

When `X402_ENABLED=false`, no x402 tools are exposed at all.

---

## Security Model

| Control | Enforcement |
|---------|-------------|
| Wallet from LLM | **Blocked.** Buyer wallet always comes from `X402_DEFAULT_BUYER_WALLET_ID` env. The LLM/tool args cannot override it. |
| Max amount from LLM | **Blocked.** Always enforces `X402_MAX_PER_REQUEST_USDC` from env. |
| Host allowlist | `X402_ALLOWED_HOSTS` must be non-empty. **Empty = reject ALL buyer payments** (fail-closed). |
| Private/localhost hosts | Blocked: `127.0.0.1`, `localhost`, `0.0.0.0`, private ranges, link-local, metadata IPs. |
| HTTPS required | `X402_REQUIRE_HTTPS=true` (default). Rejects `http://`. |
| Challenge validation | Two-phase: prefetch → `assert_challenge_valid()` in Python → sign. Validates network (`eip155:5042002`), asset (Arc USDC `0x3600...0000`), scheme (`exact`/`exact_nano`), amount, payTo, resource. |
| Daily budget | `X402_MAX_DAILY_USDC=0.01` — SQLite ledger with `BEGIN IMMEDIATE` transaction. Check + insert atomic. Projected values (count+1, total+amount) checked, not just current. |
| Request count | `X402_MAX_REQUESTS_PER_DAY=100` — SQLite ledger tracks count. |
| Idempotency | Seller tools check `payment_hash` before settling. Same payment never settles twice. |
| Fail-closed | If any limit exceeded → `PermissionError`. No payment signed. |
| ERC-8004 on-chain scan | Full scan from `ERC8004_FROM_BLOCK` to `latest`. No `balanceOf` shortcut. If `get_logs` fails → exception propagates → no tx submitted. |
| Registration lock | SQLite lock with TTL (default 1260s). Lock TTL validated to cover full Circle polling window. |
| Sidecar pay mode | `pay` mode **requires** prevalidated challenge from Python. No fallback fetch. Python runs `assert_url_allowed()` + `assert_challenge_valid()` before signing. |
| State directory | `0o700` permissions on Circle execution state directory. |
| Non-root Docker | `USER appuser` in Dockerfile. |

---

## LangChain Customization

This SDK is built on **LangChain / Deep Agents**. You can modify anything:

**Change the LLM:**
```python
# In .env
DEEPAGENT_MODEL=anthropic:claude-sonnet-4-6
DEEPAGENT_MODEL=openai:gpt-4o
DEEPAGENT_MODEL=custom-provider:model-name
```

**Add custom tools:**
```python
from erc8004_deepagent_kit.agent import build_erc8004_deep_agent

agent = build_erc8004_deep_agent()
# agent is a standard LangChain Deep Agent — add tools, change prompt, etc.
```

**Modify the system prompt:**
Edit `src/erc8004_deepagent_kit/deepagent/system_prompt.py`.

**Add new LangChain tools:**
Create a new file in `src/erc8004_deepagent_kit/tools/`, decorate with `@tool`, and add it to the tools list in `agent.py`.

**Use in a LangChain pipeline:**
```python
from erc8004_deepagent_kit.agent import build_erc8004_deep_agent

agent = build_erc8004_deep_agent(model="anthropic:claude-sonnet-4-6")
result = agent.invoke({"messages": "Check my identity status"})
```

**CLI without LLM:**
```bash
erc8004-deepagent doctor    # No LLM needed
erc8004-deepagent status    # No LLM needed
erc8004-deepagent register  # No LLM needed
```

---

## Environment Variables

### Core (ERC-8004 Identity)

| Variable | Default | Description |
|----------|---------|-------------|
| `DEEPAGENT_MODEL` | `anthropic:claude-sonnet-4-6` | LLM model for agent |
| `ANTHROPIC_API_KEY` | — | Anthropic API key (for agent LLM) |
| `NETWORK_PROFILE` | `arc-testnet` | Network profile name |
| `CHAIN_ID` | `5042002` | Arc Testnet chain ID |
| `BLOCKCHAIN` | `ARC-TESTNET` | Circle blockchain identifier |
| `RPC_URL` | `https://rpc.drpc.testnet.arc.network` | Arc Testnet RPC |
| `EXPLORER_URL` | `https://testnet.arcscan.app` | Block explorer base URL |
| `IDENTITY_REGISTRY` | `0x8004A818BFB912233c491871b3d84c89A494BD9e` | ERC-8004 IdentityRegistry contract |
| `REPUTATION_REGISTRY` | `0x8004B663056A597Dffe9eCcC1965A193B7388713` | ERC-8004 ReputationRegistry contract |
| `VALIDATION_REGISTRY` | `0x8004Cb1BF31DAf7788923b405b754f57acEB4272` | ERC-8004 ValidationRegistry contract |
| `ERC8004_FROM_BLOCK` | `41338000` | Start block for on-chain event scan |
| `EVENT_SCAN_BLOCK_RANGE` | `10000` | Blocks per `get_logs` call (max 10000) |
| `VERIFY_CHAIN_ID` | `true` | Verify RPC chain ID on startup |
| `RECEIPT_POLL_SECONDS` | `3` | Seconds between receipt polls |
| `RECEIPT_MAX_POLLS` | `60` | Max receipt poll attempts |
| `REGISTRATION_LOCK_TTL_SECONDS` | `1260` | SQLite lock TTL for registration |

### Circle DCW

| Variable | Default | Description |
|----------|---------|-------------|
| `CIRCLE_API_KEY` | — | Circle API key (format: `TEST_API_KEY:key_id:secret`) |
| `CIRCLE_ENTITY_SECRET` | — | Circle entity secret (raw 64-hex) |
| `DCW_WALLET_ADDRESS` | — | Your DCW wallet address (0x...) |
| `CIRCLE_FEE_LEVEL` | `MEDIUM` | Transaction fee level: LOW, MEDIUM, HIGH |
| `CIRCLE_TX_POLL_SECONDS` | `5` | Seconds between Circle tx status polls |
| `CIRCLE_TX_MAX_POLLS` | `180` | Max Circle tx poll attempts |
| `CIRCLE_EXECUTION_STATE_DIR` | `/data/circle_executions` | Directory for Circle execution state files |

### Agent Registration Metadata

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_KEY` | `default-agent` | Stable developer-defined agent key (3–128 chars) |
| `AGENT_NAME` | `Example ERC-8004 Deep Agent` | Agent display name |
| `AGENT_DESCRIPTION` | `LangChain Deep Agent with ERC-8004 tools.` | Agent description |
| `AGENT_IMAGE` | `https://example.com/agent.png` | Agent image URL |
| `AGENT_SERVICES_JSON` | `[]` | JSON array of agent services |
| `AGENT_SUPPORTED_TRUST_JSON` | `["reputation", "validation"]` | Supported trust mechanisms |
| `AGENT_X402_SUPPORT` | `false` | Whether agent supports x402 |

### Local State

| Variable | Default | Description |
|----------|---------|-------------|
| `IDENTITY_STORE_PATH` | `/data/erc8004_identities.sqlite3` | SQLite identity store path |

### x402 Payment Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `X402_ENABLED` | `false` | Enable x402 payment tools |
| `X402_MODE` | `batching` | Mode: `batching` or `nano` |
| `X402_GATEWAY_API_URL` | `https://gateway-api-testnet.circle.com` | Circle Gateway API URL |
| `X402_DEFAULT_BUYER_WALLET_ID` | — | Circle DCW wallet ID for buyer role (env only) |
| `X402_DEFAULT_SELLER_WALLET_ADDRESS` | — | EVM address for seller role |
| `X402_ALLOWED_HOSTS` | — | Comma-separated allowed hosts. **Empty = block ALL** |
| `X402_REQUIRE_HTTPS` | `true` | Require HTTPS for buyer requests |
| `X402_MAX_PER_REQUEST_USDC` | `0.000001` | Max USDC per single request |
| `X402_MAX_DAILY_USDC` | `0.01` | Daily budget cap (USDC) |
| `X402_MAX_REQUESTS_PER_DAY` | `100` | Max x402 requests per day |
| `X402_LEDGER_PATH` | `/data/x402_spend_ledger.sqlite3` | SQLite spend ledger path |

### x402 Agent Exposure

| Variable | Default | Description |
|----------|---------|-------------|
| `X402_EXPOSE_BALANCE_TO_AGENT` | `true` | Expose balance read tools |
| `X402_EXPOSE_BATCH_BUYER_TO_AGENT` | `false` | Expose batching buyer tool |
| `X402_EXPOSE_BATCH_SELLER_TO_AGENT` | `false` | Expose batching seller tool |
| `X402_EXPOSE_NANO_BUYER_TO_AGENT` | `false` | Expose nano buyer tool |
| `X402_EXPOSE_NANO_SELLER_TO_AGENT` | `false` | Expose nano seller tool |

### Reputation / Validation (Optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_REPUTATION_WRITES` | `false` | Allow reputation write tools |
| `ENABLE_VALIDATION_WRITES` | `false` | Allow validation write tools |
| `EXPOSE_REPUTATION_WRITE_TOOLS_TO_AGENT` | `false` | Expose reputation writes to agent |
| `EXPOSE_VALIDATION_WRITE_TOOLS_TO_AGENT` | `false` | Expose validation writes to agent |

---

## Quick Start

**One command:**

```bash
git clone https://github.com/riyannode/Deepagent-x402-kit.git
cd Deepagent-x402-kit
bash setup.sh
```

Then edit `.env` with your Circle credentials and run:

```bash
source .venv/bin/activate
erc8004-deepagent doctor
erc8004-deepagent register
```

---

## CLI Commands

```bash
erc8004-deepagent config              # Print safe config (no secrets)
erc8004-deepagent doctor              # Validate env/RPC/contract/chain + x402 checks
erc8004-deepagent status              # Check identity status (local + on-chain)
erc8004-deepagent register            # Register one identity (idempotent)
erc8004-deepagent clear-expired-locks # Clear stale registration locks
erc8004-deepagent agent-register      # Let the Deep Agent register via tools
```

---

## Doctor — Live No-Transaction Validation

`erc8004-deepagent doctor` validates your entire environment **without sending any transaction and without signing any typed data.**

### Identity checks (always):
- Registry addresses are valid EVM addresses
- `DCW_WALLET_ADDRESS` is configured
- `CIRCLE_API_KEY` and `CIRCLE_ENTITY_SECRET` are present
- Identity store parent directory exists
- RPC chain ID matches `CHAIN_ID` (5042002)
- IdentityRegistry contract has bytecode at the configured address
- Latest block >= `ERC8004_FROM_BLOCK`

### x402 checks (when `X402_ENABLED=true`):
- Sidecar files exist: `scripts/x402_batching.mjs`, `scripts/x402_nano.mjs`
- If `X402_MODE=batching`: `@circle-fin/x402-batching` package is importable
- `X402_GATEWAY_API_URL` is configured
- `X402_LEDGER_PATH` directory is writable

### Buyer exposure checks (when buyer tools exposed):
- `X402_DEFAULT_BUYER_WALLET_ID` is non-empty
- `X402_ALLOWED_HOSTS` is non-empty

### Seller exposure checks (when seller tools exposed):
- `X402_DEFAULT_SELLER_WALLET_ADDRESS` is non-empty

---

## Local Lightweight Checks (No Docker)

```bash
# Compile check
python -m compileall -q src

# Virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .

# Node dependencies
npm ci --omit=dev

# Verify Circle SDK imports
node --input-type=module -e "import('@circle-fin/developer-controlled-wallets').then(()=>console.log('dcw import ok'))"
node --input-type=module -e "import('@circle-fin/x402-batching/server').then(()=>console.log('x402 batching import ok'))"

# Print config (no secrets)
erc8004-deepagent config

# Full validation (no tx, no signing)
erc8004-deepagent doctor
```

---

## Live Validation Checklist

```txt
[ ] python -m compileall -q src       — all files compile
[ ] npm ci --omit=dev                 — node dependencies installed
[ ] erc8004-deepagent config          — prints config without secrets
[ ] erc8004-deepagent doctor          — all checks pass, ok=true
[ ] erc8004-deepagent doctor          — chain_id=5042002 verified
[ ] erc8004-deepagent doctor          — bytecode at IdentityRegistry verified
[ ] erc8004-deepagent status          — works with configured DCW wallet
[ ] erc8004-deepagent register        — first register: status=registered, real tx_hash
[ ] tx on https://testnet.arcscan.app — targets IdentityRegistry contract
[ ] tx method is register(string)     — ERC-721 Transfer mint to DCW wallet
[ ] SQLite has exactly one identity row
[ ] erc8004-deepagent register        — second register: already_registered (NO new tx)
[ ] if X402_ENABLED=true:
    [ ] doctor checks sidecar files exist
    [ ] doctor checks @circle-fin/x402-batching importable (if batching mode)
    [ ] doctor checks X402_GATEWAY_API_URL configured
    [ ] doctor checks X402_LEDGER_PATH writable
    [ ] if buyer exposed: doctor requires X402_DEFAULT_BUYER_WALLET_ID + X402_ALLOWED_HOSTS
    [ ] if seller exposed: doctor requires X402_DEFAULT_SELLER_WALLET_ADDRESS
```

---

## Docker

```bash
# Build
docker compose build

# Validate (no tx)
docker compose run --rm erc8004-live doctor

# Check status
docker compose run --rm erc8004-live status

# Register identity
docker compose run --rm erc8004-live register

# Second register (should return already_registered, no new tx)
docker compose run --rm erc8004-live register

# x402 batching mode
X402_ENABLED=true X402_MODE=batching docker compose run --rm erc8004-live doctor

# x402 nano mode
X402_ENABLED=true X402_MODE=nano docker compose run --rm erc8004-live doctor
```

---

## Tech Stack

```
Python 3.11+
LangChain / Deep Agents (deepagents 0.6.11)
LangChain Core 1.4.8
Web3.py 7.16.0
Circle Developer-Controlled Wallets SDK (Node.js)
@circle-fin/x402-batching (Node.js, for seller settle in batching mode)
SQLite (identity store + x402 spend ledger)
Arc Testnet (chain 5042002)
Docker (optional)
```

---

## Current Validation Status

Production-hardening blockers have been fixed and live validation passed.

ERC-8004 identity registration has been validated on Arc Testnet. The second registration run returned `already_registered` and created no new Circle transaction.

x402 batching and nano doctor checks passed. Live x402 payment execution still requires a real allowlisted x402 endpoint.

```txt
✅ python -m compileall -q src
✅ npm ci --omit=dev
✅ erc8004-deepagent doctor               — 12/12
✅ erc8004-deepagent status                — already_registered, agent_id=840724
✅ erc8004-deepagent register (2nd run)    — already_registered, no new tx
✅ X402_ENABLED=true X402_MODE=batching    — doctor 17/17
✅ X402_ENABLED=true X402_MODE=nano        — doctor 16/16
```

---

## License

MIT
