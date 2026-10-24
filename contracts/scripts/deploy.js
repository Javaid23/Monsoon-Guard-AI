// Deploy ForecastRegistry.
//
// Usage:
//   npx hardhat run contracts/scripts/deploy.js                  # in-process
//   npx hardhat run contracts/scripts/deploy.js --network amoy   # testnet
//
// Needs DEPLOYER_PRIVATE_KEY and optionally AMOY_RPC_URL in .env. The key is
// never committed (CLAUDE.md sections 2.8, 10.4).
//
// Deploying costs testnet gas, so this script reports the address, the
// deploying account and the chain id, and writes them to
// records/registry_deployment.json for the verifier and the app to read.
// CLAUDE.md section 10.4 also requires checking the network's EVM version
// before deploying; hardhat.config.js pins solc to the 'paris' target because
// Polygon Amoy does not support PUSH0.

const fs = require("fs");
const path = require("path");
const { ethers, network } = require("hardhat");

async function main() {
  const [deployer] = await ethers.getSigners();
  if (!deployer) {
    throw new Error(
      "No signer available. Set DEPLOYER_PRIVATE_KEY in .env for a live network."
    );
  }

  const chain = await ethers.provider.getNetwork();
  const balance = await ethers.provider.getBalance(deployer.address);

  console.log("network:   %s (chainId %s)", network.name, chain.chainId);
  console.log("deployer:  %s", deployer.address);
  console.log("balance:   %s ETH", ethers.formatEther(balance));

  if (balance === 0n) {
    throw new Error(
      "Deployer has no balance. Fund it from a faucet before deploying."
    );
  }

  const Registry = await ethers.getContractFactory("ForecastRegistry");
  const registry = await Registry.deploy();
  console.log("tx sent:   %s", registry.deploymentTransaction().hash);

  await registry.waitForDeployment();
  const address = await registry.getAddress();
  const receipt = await registry.deploymentTransaction().wait();

  console.log("deployed:  %s", address);
  console.log("block:     %s", receipt.blockNumber);
  console.log("gas used:  %s", receipt.gasUsed.toString());

  // Sanity check: the owner must be the deployer, since every write depends
  // on it and the owner cannot be changed afterwards.
  const owner = await registry.owner();
  if (owner.toLowerCase() !== deployer.address.toLowerCase()) {
    throw new Error(`owner is ${owner}, expected ${deployer.address}`);
  }
  console.log("owner ok:  %s", owner);

  const out = {
    contract: "ForecastRegistry",
    address,
    owner,
    network: network.name,
    chain_id: Number(chain.chainId),
    deploy_block: receipt.blockNumber,
    deploy_tx: receipt.hash,
    deployed_utc: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"),
    solc: "0.8.24",
    evm_version: "paris",
  };

  const target = path.join(__dirname, "..", "..", "records",
                           "registry_deployment.json");
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, JSON.stringify(out, null, 2) + "\n");
  console.log("wrote:     %s", path.relative(process.cwd(), target));
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
