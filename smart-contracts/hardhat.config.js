require('dotenv').config();
require('@nomiclabs/hardhat-ethers');

const { SEPOLIA_RPC_URL, PRIVATE_KEY } = process.env;

module.exports = {
  solidity: '0.8.17',
  networks: {
    sepolia: {
      url: SEPOLIA_RPC_URL || '',
      accounts: PRIVATE_KEY ? [PRIVATE_KEY] : []
    }
  }
};
