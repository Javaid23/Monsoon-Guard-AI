// Publish the Merkle root of the finished record set to a deployed registry.
//
//   npx hardhat run contracts/scripts/publish_archive.js --network sepolia
//
// Reads records/archive_manifest.json (written by scripts/publish_archive.py)
// and records/registry_deployment.json for the contract address. Run the
// Python script with --check first: it proves the Python tree and the
// Solidity verifier agree before any gas is spent.
//
// This is one transaction for the whole set. It asserts only that these
// records existed unaltered at this block - not that they were forecast
// before the rain, which for a 2022 replay would be untrue and which
// `commit` correctly refuses.

const fs = require("fs");
const path = require("path");
const { ethers, network } = require("hardhat");

const ROOT = path.join(__dirname, "..", "..");

async function main() {
  const manifest = JSON.parse(fs.readFileSync(
    path.join(ROOT, "records", "archive_manifest.json"), "utf8"));
  const deployment = JSON.parse(fs.readFileSync(
    path.join(ROOT, "records", "registry_deployment.json"), "utf8"));

  const chain = await ethers.provider.getNetwork();
  if (Number(chain.chainId) !== deployment.chain_id) {
    throw new Error(
      `deployment record is for chain ${deployment.chain_id}, connected to ` +
      `${chain.chainId}. Deploy to this network first.`);
  }

  const [signer] = await ethers.getSigners();
  const registry = await ethers.getContractAt(
    "ForecastRegistry", deployment.address, signer);

  const owner = await registry.owner();
  if (owner.toLowerCase() !== signer.address.toLowerCase()) {
    throw new Error(`signer ${signer.address} is not the owner ${owner}`);
  }

  console.log("network:   %s (chainId %s)", network.name, chain.chainId);
  console.log("registry:  %s", deployment.address);
  console.log("root:      %s", manifest.merkle_root);
  console.log("records:   %d", manifest.leaf_count);

  // Price the transaction before sending it, so the cost is known rather
  // than discovered.
  const gas = await registry.publishArchive.estimateGas(
    manifest.merkle_root, manifest.leaf_count, manifest.label);
  const fee = await ethers.provider.getFeeData();
  const price = fee.maxFeePerGas ?? fee.gasPrice;
  console.log("gas:       %s at %s gwei = %s ETH", gas.toString(),
              ethers.formatUnits(price, "gwei"),
              ethers.formatEther(gas * price));

  const tx = await registry.publishArchive(
    manifest.merkle_root, manifest.leaf_count, manifest.label);
  console.log("tx sent:   %s", tx.hash);
  const receipt = await tx.wait();
  console.log("mined in block %s, gas used %s",
              receipt.blockNumber, receipt.gasUsed.toString());

  const index = Number(await registry.archiveCount()) - 1;

  // Prove the published root accepts a real record before declaring success.
  const sample = manifest.records[0];
  const ok = await registry.verifyArchiveProof(
    index, "0x" + sample.sha256, sample.proof);
  if (!ok) throw new Error("published root does not verify its own record");
  console.log("verified:  %s proves against the published root", sample.forecast_id);

  const out = {
    ...deployment,
    archive: {
      index,
      label: manifest.label,
      merkle_root: manifest.merkle_root,
      leaf_count: manifest.leaf_count,
      publish_tx: receipt.hash,
      publish_block: receipt.blockNumber,
      published_utc: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"),
    },
  };
  fs.writeFileSync(path.join(ROOT, "records", "registry_deployment.json"),
                   JSON.stringify(out, null, 2) + "\n");
  console.log("wrote:     records/registry_deployment.json");
}

main().catch((err) => {
  console.error(err.message || err);
  process.exitCode = 1;
});
