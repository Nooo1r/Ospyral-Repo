import { ethers } from 'ethers';
import EscrowABI from './EscrowABI.json';

async function init() {
  if (!window.ethereum) throw "Metamask not found";
  await window.ethereum.request({ method: 'eth_requestAccounts' });
  const provider = new ethers.providers.Web3Provider(window.ethereum);
  const signer   = provider.getSigner();
  return { signer };
}

export async function buyArtwork(artworkId, sellerAddress, priceWei, backendApiUrl) {
  const { signer } = await init();
  const contract   = new ethers.Contract("0xEscrowAddress", EscrowABI, signer);
  const tx      = await contract.createOrder(sellerAddress, { value: priceWei });
  const receipt = await tx.wait();
  const event   = receipt.events.find(e => e.event === 'OrderCreated');
  const [orderId] = event.args;

  await fetch(`${backendApiUrl}/api/purchase/`, {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({
      artwork_id: artworkId,
      order_id:   orderId.toString(),
      tx_hash:    receipt.transactionHash,
      amount:     priceWei
    })
  });

  return orderId;
}
