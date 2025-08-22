
const hre = require('hardhat');

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  console.log('Deploying contracts with:', deployer.address);

  // 1. Деплой ERC20 OSP
  const initialSupply = hre.ethers.utils.parseUnits('10000000', 18);
  const OSP     = await hre.ethers.getContractFactory('OSPToken');
  const osp     = await OSP.deploy(initialSupply);
  await osp.deployed();
  console.log('OSPToken at:', osp.address);

  // 2. Деплой Escrow, передаём адрес токена
  const Escrow  = await hre.ethers.getContractFactory('Escrow');
  const escrow  = await Escrow.deploy(osp.address);
  await escrow.deployed();
  console.log('Escrow at:', escrow.address);

  // 3. Верификация, если есть API‑ключ
  if (process.env.ETHERSCAN_API_KEY) {
    await hre.run('verify:verify', {
      address: osp.address,
      constructorArguments: [initialSupply],
    });
    await hre.run('verify:verify', {
      address: escrow.address,
      constructorArguments: [osp.address],
    });
    console.log('Verified on Etherscan');
  }
}

main()
  .then(() => process.exit(0))
  .catch(e => { console.error(e); process.exit(1); });
