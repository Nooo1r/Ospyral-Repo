
async function main() {
  const [deployer] = await ethers.getSigners();
  console.log("Deploying with:", deployer.address);

  const Escrow = await ethers.getContractFactory("Escrow");
  const escrow  = await Escrow.deploy();
  await escrow.deployed();

  console.log("Escrow deployed to:", escrow.address);
}
main()
  .catch(err => {
    console.error(err);
    process.exit(1);
  });
