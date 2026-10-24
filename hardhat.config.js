require("@nomicfoundation/hardhat-toolbox");
// Private key and RPC URL come from .env and are never committed
// (CLAUDE.md sections 2.8 and 10.4). Optional, so that `hardhat test` works
// on a clean checkout with no .env: the in-process network needs no secrets,
// and only a live deployment does.
try {
  require("dotenv").config();
} catch {
  /* dotenv absent: fall back to the real environment */
}

const AMOY_RPC_URL =
  process.env.AMOY_RPC_URL ||
  // polygon.technology's own endpoint does not resolve on some
  // networks; publicnode is a working public alternative.
  "https://polygon-amoy-bor-rpc.publicnode.com";

// RPC endpoints. Either paste a full URL, or set ALCHEMY_API_KEY alone and
// let the per-network URL be built from it. publicnode is a keyless fallback
// so a clean checkout still works, but it rate-limits, so prefer Alchemy for
// anything that sends transactions.
const ALCHEMY_API_KEY = process.env.ALCHEMY_API_KEY || "";
const alchemy = (subdomain) =>
  ALCHEMY_API_KEY ? `https://${subdomain}.g.alchemy.com/v2/${ALCHEMY_API_KEY}` : "";

const SEPOLIA_RPC_URL =
  process.env.SEPOLIA_RPC_URL || alchemy("eth-sepolia") ||
  "https://ethereum-sepolia-rpc.publicnode.com";
const BASE_SEPOLIA_RPC_URL =
  process.env.BASE_SEPOLIA_RPC_URL || alchemy("base-sepolia") ||
  "https://base-sepolia-rpc.publicnode.com";
const MAINNET_RPC_URL =
  process.env.MAINNET_RPC_URL || alchemy("eth-mainnet") ||
  "https://ethereum-rpc.publicnode.com";
const BASE_RPC_URL =
  process.env.BASE_RPC_URL || alchemy("base-mainnet") ||
  "https://base-rpc.publicnode.com";
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
    // Ethereum Sepolia. Added because every Amoy faucet we tried requires an
    // existing mainnet balance, while Sepolia has a proof-of-work faucet
    // that asks for nothing but browser time. The contract is plain Solidity
    // and chain-agnostic, so it deploys identically on either.
    sepolia: {
      url: SEPOLIA_RPC_URL,
      chainId: 11155111,
      accounts: DEPLOYER_PRIVATE_KEY ? [DEPLOYER_PRIVATE_KEY] : [],
    },
    // Base Sepolia. The one testnet whose faucet (Coinbase's developer
    // portal) does not demand an existing mainnet balance, which is what
    // blocked every Amoy and Sepolia faucet we tried.
    baseSepolia: {
      url: BASE_SEPOLIA_RPC_URL,
      chainId: 84532,
      accounts: DEPLOYER_PRIVATE_KEY ? [DEPLOYER_PRIVATE_KEY] : [],
    },
    // Real networks. Deploying here spends real money, so CLAUDE.md section
    // 2.4 requires asking first; nothing in the repo deploys automatically.
    // The 'paris' EVM target is conservative but valid on all of them.
    mainnet: {
      url: MAINNET_RPC_URL,
      chainId: 1,
      accounts: DEPLOYER_PRIVATE_KEY ? [DEPLOYER_PRIVATE_KEY] : [],
    },
    base: {
      url: BASE_RPC_URL,
      chainId: 8453,
      accounts: DEPLOYER_PRIVATE_KEY ? [DEPLOYER_PRIVATE_KEY] : [],
    },
  },
  etherscan: {
    // Source verification, so judges read the contract rather than bytecode.
    // One free key from etherscan.io covers Etherscan and Basescan.
    apiKey: process.env.ETHERSCAN_API_KEY || "",
  },
};
