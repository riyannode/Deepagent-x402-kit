/**
 * x402 Nanopayment Standalone Sidecar
 *
 * 1 request = 1 payment authorization.
 * Simpler than batching — direct Gateway verify/settle without BatchFacilitatorClient.
 * Designed for single paid API calls, demos, lightweight endpoints.
 *
 * Modes: prefetch | pay | sell | balance
 */

import { createRequire } from "node:module";
import { randomBytes } from "node:crypto";
const require = createRequire(import.meta.url);

const TX_HASH_RE = /^0x[a-fA-F0-9]{64}$/;
const ADDRESS_RE = /^0x[a-fA-F0-9]{40}$/;

function readStdin() {
  return new Promise((resolve, reject) => {
    let d = ""; process.stdin.setEncoding("utf8");
    process.stdin.on("data", c => { d += c; });
    process.stdin.on("end", () => resolve(d));
    process.stdin.on("error", reject);
  });
}
function redact(v) {
  let s = String(v || "");
  for (const k of [process.env.CIRCLE_API_KEY, process.env.CIRCLE_ENTITY_SECRET])
    if (k) s = s.split(k).join("[redacted]");
  return s;
}
const MAX_BODY_BYTES = 1024 * 1024; // 1MB
async function readBodyLimited(resp) {
  const cl = resp.headers.get("content-length");
  if (cl && Number(cl) > MAX_BODY_BYTES) return "[truncated: content-length " + cl + "]";
  const reader = resp.body?.getReader?.();
  if (!reader) return (await resp.text()).substring(0, MAX_BODY_BYTES);
  const chunks = []; let total = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value); total += value.length;
    if (total > MAX_BODY_BYTES) { reader.cancel(); return new TextDecoder().decode(Buffer.concat(chunks)).substring(0, MAX_BODY_BYTES); }
  }
  return new TextDecoder().decode(Buffer.concat(chunks));
}
function ok(d) { process.stdout.write(JSON.stringify({ ok: true, ...d })); }
function fail(m, d = {}) { process.stdout.write(JSON.stringify({ ok: false, error: m, ...d })); }

async function checkBalance(addr) {
  const api = process.env.X402_GATEWAY_API_URL || "https://gateway-api-testnet.circle.com";
  const r = await fetch(`${api}/v1/balances`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token: "USDC", sources: [{ domain: 26, depositor: addr.toLowerCase() }] }),
  });
  if (!r.ok) throw new Error(`balance failed (${r.status})`);
  const d = await r.json();
  const raw = d.balances?.[0]?.balance || "0";
  return { balanceUsdc: (Number(raw) / 1e6).toFixed(6), balanceRaw: raw, depositor: addr };
}

/** Prefetch: fetch URL, get 402 challenge, return it without signing. */
async function prefetch(input) {
  const { url, method } = input;
  if (!url) throw new Error("url required");

  const resp = await fetch(url, { method: method || "GET", headers: { "Content-Type": "application/json" } });
  if (resp.status !== 402) {
    const body = await readBodyLimited(resp);
    return ok({ mode: "prefetch", paymentRequired: false, httpStatus: resp.status, body: body.substring(0, 4096) });
  }

  const header = resp.headers.get("payment-required");
  if (!header) throw new Error("402 but no PAYMENT-REQUIRED header");
  const challenge = JSON.parse(Buffer.from(header, "base64").toString("utf-8"));
  if (!challenge.accepts?.[0]) throw new Error("no accepts[] in challenge");

  return ok({ mode: "prefetch", paymentRequired: true, challenge });
}

async function pay(input) {
  const { url, walletId, maxAmountUsdc, method, challenge: preFetched } = input;
  if (!url) throw new Error("url required");
  if (!walletId) throw new Error("walletId required");

  // Defense-in-depth: pay mode MUST use prevalidated challenge from Python.
  // No fallback fetch — Python policy (assert_url_allowed + assert_challenge_valid)
  // must run before signing.
  if (!preFetched) throw new Error("pay requires prevalidated challenge from Python policy");
  const challenge = preFetched;
  const accept = challenge.accepts?.[0];
  if (!accept) throw new Error("prevalidated challenge has no accepts[]");

  const amountAtomic = accept.amount || "1";
  const maxAtomic = String(Math.floor(Number(maxAmountUsdc || "0.000001") * 1e6));
  if (Number(amountAtomic) > Number(maxAtomic)) throw new Error(`amount exceeds max`);

  const circleClient = require("@circle-fin/developer-controlled-wallets").initiateDeveloperControlledWalletsClient({
    apiKey: process.env.CIRCLE_API_KEY, entitySecret: process.env.CIRCLE_ENTITY_SECRET,
  });
  const w = await circleClient.getWallet({ id: walletId });
  const addr = w?.data?.wallet?.address;
  if (!addr) throw new Error(`wallet not found: ${walletId}`);

  const extra = accept.extra || {};
  const now = Math.floor(Date.now() / 1000);
  const nonce = "0x" + randomBytes(32).toString("hex");
  const authorization = {
    from: addr.toLowerCase(), to: accept.payTo, value: amountAtomic,
    validAfter: String(now - 60), validBefore: String(now + (accept.maxTimeoutSeconds || 604900)), nonce,
  };
  const domain = {
    name: extra.name || "GatewayWalletBatched", version: extra.version || "1",
    chainId: 5042002, verifyingContract: extra.verifyingContract || "0x0077777d7EBA4688BDeF3E311b846F25870A19B9",
  };
  const types = {
    EIP712Domain: [
      { name: "name", type: "string" }, { name: "version", type: "string" },
      { name: "chainId", type: "uint256" }, { name: "verifyingContract", type: "address" },
    ],
    TransferWithAuthorization: [
      { name: "from", type: "address" }, { name: "to", type: "address" },
      { name: "value", type: "uint256" }, { name: "validAfter", type: "uint256" },
      { name: "validBefore", type: "uint256" }, { name: "nonce", type: "bytes32" },
    ],
  };

  const signResult = await circleClient.signTypedData({
    walletId,
    data: JSON.stringify({ types, primaryType: "TransferWithAuthorization", domain, message: authorization },
      (k, v) => typeof v === "bigint" ? v.toString() : v),
  });
  const sig = signResult?.data?.signature || signResult?.data?.signatures?.[0];
  if (!sig) throw new Error("no signature from DCW");

  const payload = {
    x402Version: challenge.x402Version || 2,
    payload: { authorization, signature: sig },
    resource: challenge.resource || url,
    accepted: accept,
  };

  const retry = await fetch(url, {
    method: method || "GET",
    headers: { "Content-Type": "application/json", "payment-signature": Buffer.from(JSON.stringify(payload)).toString("base64") },
  });
  const body = await retry.text();
  let data; try { data = JSON.parse(body); } catch { data = body; }

  return ok({
    mode: "nano_pay", paymentRequired: true,
    amountUsdc: (Number(amountAtomic) / 1e6).toFixed(6), amountAtomic,
    payTo: accept.payTo, walletAddress: addr, signed: true,
    httpStatus: retry.status, body: typeof data === "string" ? data.substring(0, 4096) : data,
  });
}

async function settle(input) {
  const { paymentSignature, payTo, amountAtomic, network, resource } = input;
  if (!paymentSignature) throw new Error("paymentSignature required");
  if (!payTo || !ADDRESS_RE.test(payTo)) throw new Error("payTo must be EVM address");

  // Nanopayment standalone: verify via Gateway REST directly (no BatchFacilitatorClient)
  const api = process.env.X402_GATEWAY_API_URL || "https://gateway-api-testnet.circle.com";
  const paymentPayload = JSON.parse(Buffer.from(paymentSignature, "base64").toString("utf-8"));

  const verifyBody = {
    x402Version: paymentPayload.x402Version || 2,
    paymentPayload: {
      ...paymentPayload,
      resource: resource || paymentPayload.resource,
      accepted: paymentPayload.accepted || {
        scheme: "exact", network: network || "eip155:5042002",
        asset: "0x3600000000000000000000000000000000000000",
        amount: amountAtomic || "1", payTo,
        maxTimeoutSeconds: 604900,
        extra: { name: "GatewayWalletBatched", version: "1", verifyingContract: "0x0077777d7EBA4688BDeF3E311b846F25870A19B9" },
      },
    },
  };

  // Verify
  const vr = await fetch(`${api}/v1/x402/verify`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(verifyBody),
  });
  const vd = await vr.json();
  if (!vr.ok || vd?.isValid === false) {
    return fail(`verify failed: ${vd?.invalidReason || vd?.error || "unknown"}`, { verified: false });
  }

  // Settle
  const sr = await fetch(`${api}/v1/x402/settle`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(verifyBody),
  });
  const sd = await sr.json();
  const rawTx = sd?.transaction?.txHash || sd?.txHash || null;
  const txHash = rawTx && TX_HASH_RE.test(rawTx) ? rawTx : null;

  return ok({
    mode: "nano_sell", verified: true, settled: sd?.success !== false,
    payer: sd?.payer || vd?.payer || null, txHash,
    explorerUrl: txHash ? `https://testnet.arcscan.app/tx/${txHash}` : null,
    amountUsdc: (Number(amountAtomic || "1") / 1e6).toFixed(6), payTo,
  });
}

async function main() {
  const input = JSON.parse(await readStdin() || "{}");
  const { mode } = input;
  if (!mode) throw new Error("mode: prefetch | pay | sell | balance");
  if (mode === "prefetch") return await prefetch(input);
  if (mode === "pay") return await pay(input);
  if (mode === "sell") return await settle(input);
  if (mode === "balance") {
    if (!input.walletAddress || !ADDRESS_RE.test(input.walletAddress)) throw new Error("walletAddress required");
    return ok({ mode: "balance", ...(await checkBalance(input.walletAddress)) });
  }
  throw new Error(`unknown mode: ${mode}`);
}

main().catch(e => { fail(redact(e?.message || String(e))); process.exit(1); });
