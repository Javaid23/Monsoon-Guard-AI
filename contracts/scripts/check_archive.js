// Cross-check the Python Merkle tree against the Solidity one.
//
// The tree is built in Python (src/audit/merkle.py) and verified on chain
// (ForecastRegistry.verifyArchiveProof). Two implementations of the same
// structure in two languages is precisely where a silent mismatch hides, and
// one discovered after publishing is discovered too late. This deploys a
// throwaway registry, publishes the Python-computed root, and runs every
// Python-computed proof through the real contract.
//
// Reads contracts/.cache/archive_crosscheck.json, written by
// scripts/publish_archive.py. Costs nothing: it runs in-process unless a
// --network is given.

const fs = require("fs");
const path = require("path");
const { ethers } = require("hardhat");

async function main() {
  const file = path.join(__dirname, "..", ".cache", "archive_crosscheck.json");
  const { root, leaves, proofs, label } = JSON.parse(fs.readFileSync(file, "utf8"));

  const Registry = await ethers.getContractFactory("ForecastRegistry");
  const registry = await Registry.deploy();
  await registry.waitForDeployment();

  await (await registry.publishArchive(root, leaves.length, label)).wait();

  let ok = 0;
  for (let i = 0; i < leaves.length; i++) {
    if (await registry.verifyArchiveProof(0, leaves[i], proofs[i])) ok++;
    else console.error("leaf %d (%s) failed its proof on chain", i, leaves[i]);
  }

  // A tampered record must fail, or the check proves nothing.
  const forged = ethers.keccak256(ethers.toUtf8Bytes("tampered"));
  const forgedPasses = await registry.verifyArchiveProof(0, forged, proofs[0]);

  console.log("cross-check: %d/%d leaves verified in Solidity", ok, leaves.length);
  console.log("tampered leaf rejected: %s", !forgedPasses);

  if (ok !== leaves.length || forgedPasses) {
    throw new Error("Python and Solidity disagree about the tree");
  }
}

main().catch((err) => {
  console.error(err.message || err);
  process.exitCode = 1;
});
