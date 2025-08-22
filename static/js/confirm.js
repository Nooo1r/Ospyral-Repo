import { ethers } from 'ethers';
import EscrowABI from './EscrowABI.json';

async function initProvider() {
  if (!window.ethereum) {
    alert('Metamask не найден');
    throw new Error('Metamask not found');
  }
  await window.ethereum.request({ method: 'eth_requestAccounts' });
  const provider = new ethers.providers.Web3Provider(window.ethereum);
  return provider.getSigner();
}

async function confirmOrderOnChain(orderId) {
  const signer = await initProvider();
  const contract = new ethers.Contract(
    /* ваш адрес контракта */,
    EscrowABI,
    signer
  );
  const tx = await contract.confirm(orderId);
  return tx.wait();
}

document.querySelectorAll('.btn-confirm').forEach(btn => {
  btn.addEventListener('click', async () => {
    const orderId = btn.dataset.orderId;
    btn.disabled = true;
    btn.textContent = 'Подтверждение…';
    try {
      const receipt = await confirmOrderOnChain(orderId);
      // успех – меняем статус в таблице
      document
        .getElementById(`status-${orderId}`)
        .textContent = 'Выполнено';
      btn.remove();  // убираем кнопку
      alert('Заказ подтверждён в блокчейне');
    } catch (err) {
      console.error(err);
      alert('Ошибка при подтверждении: ' + err.message);
      btn.disabled = false;
      btn.textContent = 'Подтвердить получение';
    }
  });
});
