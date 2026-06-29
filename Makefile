.PHONY: build doctor status register register-again agent-register clear-expired-locks live-check update-circle-sidecar-hash verify-circle-sidecar-hash reputation-index-once reputation-index-status

CIRCLE_CONTRACT_SCRIPT := scripts/circle_execute_contract.mjs
CONTRACT_EXECUTOR := src/erc8004_deepagent_kit/wallet/contract_executor.py

build: verify-circle-sidecar-hash
	docker compose build

doctor: verify-circle-sidecar-hash
	docker compose run --rm erc8004-live doctor

status: verify-circle-sidecar-hash
	docker compose run --rm erc8004-live status

register: verify-circle-sidecar-hash
	docker compose run --rm erc8004-live register

register-again: verify-circle-sidecar-hash
	docker compose run --rm erc8004-live register

agent-register: verify-circle-sidecar-hash
	docker compose run --rm erc8004-live agent-register

clear-expired-locks:
	docker compose run --rm erc8004-live clear-expired-locks

update-circle-sidecar-hash:
	@python3 -c 'from pathlib import Path; import hashlib, re; script=Path("$(CIRCLE_CONTRACT_SCRIPT)"); executor=Path("$(CONTRACT_EXECUTOR)"); h=hashlib.sha256(script.read_bytes()).hexdigest(); text=executor.read_text(); new,count=re.subn(r"_EXPECTED_SCRIPT_HASH = \"[^\"]*\"", "_EXPECTED_SCRIPT_HASH = \"" + h + "\"", text, count=1); assert count == 1, "_EXPECTED_SCRIPT_HASH not found"; executor.write_text(new); print("updated _EXPECTED_SCRIPT_HASH=" + h)'

verify-circle-sidecar-hash:
	@python3 -c 'from pathlib import Path; import hashlib, re; script=Path("$(CIRCLE_CONTRACT_SCRIPT)"); executor=Path("$(CONTRACT_EXECUTOR)"); h=hashlib.sha256(script.read_bytes()).hexdigest(); text=executor.read_text(); m=re.search(r"_EXPECTED_SCRIPT_HASH = \"([^\"]*)\"", text); assert m, "_EXPECTED_SCRIPT_HASH not found"; expected=m.group(1); assert expected != "SKIP_CHECK", "SKIP_CHECK is not allowed for production verification"; assert h == expected, "Circle sidecar hash mismatch: expected " + expected + ", got " + h; print("circle sidecar hash ok: " + h)'

live-check: build doctor status register register-again

reputation-index-once:
	docker compose run --rm erc8004-live reputation-index-once

reputation-index-status:
	docker compose run --rm erc8004-live reputation-index-status
