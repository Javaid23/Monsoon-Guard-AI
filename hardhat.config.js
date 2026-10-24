require("@nomicfoundation/hardhat-toolbox");
// Private key and RPC URL come from .env and are never committed
// (CLAUDE.md sections 2.8 and 10.4).
require("dotenv").config();

const AMOY_RPC_URL =
  process.env.AMOY_RPC_URL || "https://rpc-amoy.polygon.technology";
const DEPLOYER_PRIVATE_KEY = process.env.DEPLOYER_PRIVATE_KEY || "";

/** @type import('hardhat/config').HardhatUserConfig */
module.exports = {
  solidity: {
    version: "0.8.24",
    settings: {
      optimizer: { enabled: true, runs: 200 },
      // Polygon Amoy does not support the PUSH0 opcode that the default
      // 'cancun'/'shanghai' targets emit, so pin the EVM version. CLAUDE.md
      // section 10.4 says to check the network's EVM version before
      // deploying; 'paris' is the safe target for Amoy.
      evmVersion: "paris",
    },
  },
  paths: {
    sources: "contracts",
    tests: "contracts/test",
    cache: "contracts/.cache",
    artifacts: "contracts/artifacts",
  },
  networks: {
    hardhat: {},
    amoy: {
      url: AMOY_RPC_URL,
      chainId: 80002,
      accounts: DEPLOYER_PRIVATE_KEY ? [DEPLOYER_PRIVATE_KEY] : [],
    },
  },
};
