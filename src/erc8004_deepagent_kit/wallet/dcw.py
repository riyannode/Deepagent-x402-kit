from __future__ import annotations

from dataclasses import dataclass

from web3 import Web3

from ..config import load_config


@dataclass(frozen=True)
class DcwWallet:
    address: str


def get_configured_wallet() -> DcwWallet:
    cfg = load_config()
    if not cfg.dcw_wallet_address:
        raise RuntimeError("DCW_WALLET_ADDRESS is required")
    return DcwWallet(address=Web3.to_checksum_address(cfg.dcw_wallet_address))


def get_reputation_writer_wallet() -> DcwWallet:
    cfg = load_config()
    if not cfg.reputation_writer_wallet_address:
        raise RuntimeError("REPUTATION_WRITER_WALLET_ADDRESS is required for reputation writes")
    return DcwWallet(address=Web3.to_checksum_address(cfg.reputation_writer_wallet_address))
