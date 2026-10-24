# Deploying ForecastRegistry to Polygon Amoy

Everything is wired. The only missing input is a funded testnet key, which
must come from you — generating or handling a private key is not something to
delegate.

Current state: contract written, **24 Hardhat tests passing**, Amoy network
configured (chainId 80002, `evmVersion: "paris"` pinned because Amoy does not
support the PUSH0 opcode that newer targets emit). Nothing deployed yet.

---

## 1. Create a throwaway wallet

Use a **fresh wallet that holds nothing else**. This key goes in a file on a
development machine; treat it as compromised from the moment it exists.

Any wallet works (MetaMask → create account → export private key). Copy the
address and the private key.

## 2. Fund it

Polygon Amoy faucets (free testnet POL, no real money):

- https://faucet.polygon.technology — select **Amoy**, paste the address
- https://www.alchemy.com/faucets/polygon-amoy — needs an Alchemy login

A fraction of one POL is plenty; a deployment costs very little.

Check it arrived at `https://amoy.polygonscan.com/address/<your address>`.

## 3. Add the key to `.env`

```
DEPLOYER_PRIVATE_KEY=0x...
AMOY_RPC_URL=https://rpc-amoy.polygon.technology
```

`AMOY_RPC_URL` is optional — that public endpoint is already the default. If
it is rate-limiting, an Alchemy or Infura Amoy URL drops in here instead.

`.env` is gitignored. Confirm with `git check-ignore -v .env` before
committing anything.

## 4. Deploy

```bash
npx hardhat test                        # confirm 24 passing first
npx hardhat run contracts/scripts/deploy.js --network amoy
```

The script prints the deployed address. Record it in `.env`:

```
FORECAST_REGISTRY_ADDRESS=0x...
```

## 5. Verify it exists on chain

Open `https://amoy.polygonscan.com/address/<contract address>` and confirm
the contract creation transaction. **Put that URL in the Devpost submission
and the README** — a judge should not have to take your word for it.

## 6. Commit real records (optional, higher value)

With the contract live, commits become real transactions rather than mock
entries. Note the constraint from CLAUDE.md section 10.3 before trying:

> the contract requires `block.timestamp < windowStart`

Archived 2022 forecasts have windows that opened years ago, so **they cannot
be committed to a live chain** — the contract will correctly reject them.
This is the rule working, not a bug.

Two honest options:

- **Leave the archive on the mock registry** (where a simulated clock makes
  the replay meaningful) and deploy the contract as a working, tested
  artefact with its rules demonstrated by the Hardhat suite. Say exactly that
  in the submission.
- **Commit one forward-dated record** — build a record whose window opens
  tomorrow, commit it live, and show the transaction. It proves the live path
  end to end without pretending a historical forecast was committed in 2022.

The second is more convincing and takes a few minutes. It is also the only
version that is strictly true: anything else would imply we pre-committed
forecasts we did not.

---

## What to update after deploying

| File | Change |
|---|---|
| `README.md` | contract address + explorer link |
| `docs/submission.md` | replace "a testnet contract was written but not deployed" |
| `reports/report.md` | Section 5, same correction |
| `src/app/streamlit_app.py` | ledger page currently says the registry is local |

Those four all currently state, correctly, that no deployment exists. If one
does, they become wrong and must be corrected in the same pass.
