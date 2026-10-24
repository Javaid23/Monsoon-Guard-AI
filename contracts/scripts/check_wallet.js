// Is the deployer wallet ready, and what will deploying actually cost?
//
//   npx hardhat run contracts/scripts/check_wallet.js --network mainnet
//
// Run this before any live deployment. It reads the balance, prices the
// deploy and the archive publish at the current gas price, and says plainly
// whether there is enough. Nothing is sent; this is read-only.

const { ethers, network } = require("hardhat");

// Measured on a local run of the real scripts, not guessed.
const DEPLOY_GAS = 1280639n;
const ARCHIVE_GAS = 138102n;

async function main() {
  const [signer] = await ethers.getSigners();
  if (!signer) {
    throw new Error("No DEPLOYER_PRIVATE_KEY in .env, so there is no wallet " +
                    "to check. Add it and re-run.");
  }
  const chain = await ethers.provider.getNetwork();
  const balance = await ethers.provider.getBalance(signer.address);
  const fee = await ethers.provider.getFeeData();
  const price = fee.maxFeePerGas ?? fee.gasPrice;
  const needed = (DEPLOY_GAS + ARCHIVE_GAS) * price;

  console.log("network:   %s (chainId %s)", network.name, chain.chainId);
  console.log("wallet:    %s", signer.address);
  console.log("balance:   %s ETH", ethers.formatEther(balance));
  console.log("gas price: %s gwei", ethers.formatUnits(price, "gwei"));
  console.log("deploy  +  archive = %s gas", (DEPLOY_GAS + ARCHIVE_GAS).toString());
  console.log("estimated cost: %s ETH", ethers.formatEther(needed));

  // Ask for headroom: gas can move between this check and the transaction,
  // and a deploy that runs out halfway still burns everything it used.
  const target = needed * 3n;
  if (balance >= target) {
    console.log("\nREADY. Balance covers the cost with a 3x margin.");
  } else if (balance > 0n) {
    console.log("\nTIGHT. Top up to about %s ETH for a safe margin.",
                ethers.formatEther(target));
  } else {
    console.log("\nEMPTY. Send ETH to the address above before deploying.");
  }
}

main().catch((err) => {
  console.error(err.message || err);
  process.exitCode = 1;
});
