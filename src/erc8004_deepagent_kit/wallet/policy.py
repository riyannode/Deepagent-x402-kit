from __future__ import annotations

from dataclasses import dataclass
from web3 import Web3


@dataclass(frozen=True)
class ContractCallIntent:
    wallet_address: str
    blockchain: str
    contract_address: str
    abi_function_signature: str
    abi_parameters: list


class WalletPolicy:
    def __init__(self, *, identity_registry: str, reputation_registry: str, validation_registry: str, enable_reputation_writes: bool, enable_validation_writes: bool):
        self.identity_registry = Web3.to_checksum_address(identity_registry)
        self.reputation_registry = Web3.to_checksum_address(reputation_registry)
        self.validation_registry = Web3.to_checksum_address(validation_registry)
        self.enable_reputation_writes = enable_reputation_writes
        self.enable_validation_writes = enable_validation_writes

    def assert_allowed(self, intent: ContractCallIntent) -> None:
        contract = Web3.to_checksum_address(intent.contract_address)
        sig = intent.abi_function_signature.strip()

        if contract == self.identity_registry and sig == "register(string)":
            # M5: Validate agent_uri parameter format
            if intent.abi_parameters and isinstance(intent.abi_parameters[0], str):
                uri = intent.abi_parameters[0]
                if not uri.startswith("data:application/json;base64,"):
                    raise PermissionError(f"register(string) agent_uri must be a data: URI, got: {uri[:50]}...")
            return

        if contract == self.reputation_registry and sig == "giveFeedback(uint256,int128,uint8,string,string,string,string,bytes32)":
            if not self.enable_reputation_writes:
                raise PermissionError("reputation writes are disabled by policy")
            return

        if contract == self.validation_registry and sig in {
            "validationRequest(address,uint256,string,bytes32)",
            "validationResponse(bytes32,uint8,string,bytes32,string)",
        }:
            if not self.enable_validation_writes:
                raise PermissionError("validation writes are disabled by policy")
            return

        raise PermissionError(f"contract call not allowed: {contract} {sig}")
