// contracts/Escrow.sol
// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.0;

event DonationReceived(address indexed from, uint256 amount);
event DepositForOSP(address indexed from, uint256 amount);

import "@openzeppelin/contracts/security/ReentrancyGuard.sol";

contract Escrow is ReentrancyGuard {
    enum Status { Pending, Completed, Disputed, Released, Refunded }

    struct Order {
        address buyer;
        address seller;
        uint256 createdAt;
        uint256 amount;
        Status  status;
    }

    mapping(uint256 => Order) public orders;
    uint256 public nextOrderId;
    address public owner;

    uint256 public constant TIMEOUT = 2 days;

    event OrderCreated(uint256 indexed orderId, address buyer, address seller, uint256 amount);
    event OrderCompleted(uint256 indexed orderId);
    event OrderTimedOut(uint256 indexed orderId);
    event OrderDisputed(uint256 indexed orderId);
    event OrderReleased(uint256 indexed orderId);
    event OrderRefunded(uint256 indexed orderId);
    constructor() {
        owner = msg.sender;
    }

    constructor() {
        owner = msg.sender;
    }

    modifier onlyOwner() {
        require(msg.sender == owner, "Only admin");
        _;
    }

    // покупатель депонирует средства
    function createOrder(address seller) external payable returns (uint256) {
        require(msg.value > 0, "Amount > 0");
        orders[nextOrderId] = Order(msg.sender, seller, msg.value, Status.Pending);
        emit OrderCreated(nextOrderId, msg.sender, seller, msg.value);
        return nextOrderId++;
    }

    // покупатель подтверждает получение работы
    function confirm(uint256 orderId) external {
        Order storage o = orders[orderId];
        require(msg.sender == o.buyer,         "Only buyer");
        require(o.status == Status.Pending,    "Already done");
        o.status = Status.Released;
        payable(o.seller).transfer(o.amount);
        emit OrderReleased(orderId);
    }

    // Создать заказ (покупатель отправляет ETH)
    function createOrder(address seller) external payable {
        require(msg.value > 0, "Zero value");
        orders[orderCount] = Order(msg.sender, seller, msg.value, Status.Pending);
        emit OrderCreated(orderCount, msg.sender, seller, msg.value);
        orderCount++;
    }

    // Покупатель подтверждает, что всё ок — деньги продавцу
    function completeOrder(uint256 orderId) external nonReentrant {
        Order storage o = orders[orderId];
        require(msg.sender == o.buyer, "Only buyer");
        require(o.status == Status.Pending, "Order not pending");
        o.status = Status.Completed;
        payable(o.seller).transfer(o.amount);
        emit OrderCompleted(orderId);
    }

    // Покупатель открывает спор
    function raiseDispute(uint256 orderId) external {
        Order storage o = orders[orderId];
        require(msg.sender == o.buyer, "Only buyer");
        require(o.status == Status.Pending, "Order not pending");
        o.status = Status.Disputed;
        emit OrderDisputed(orderId);
    }

    // Админ возвращает деньги покупателю
    function refundByAdmin(uint256 orderId) external onlyOwner nonReentrant {
        Order storage o = orders[orderId];
        require(o.status == Status.Disputed, "Not disputed");
        o.status = Status.Refunded;
        payable(o.buyer).transfer(o.amount);
        emit OrderRefunded(orderId);
    }

    // Продавец (или любой) может забрать деньги, если прошло >2 дней и нет подтверждения/спора
    function claimTimeout(uint256 orderId) external nonReentrant {
        Order storage o = orders[orderId];
        require(o.status == Status.Pending, "Order not pending");
        require(block.timestamp >= o.createdAt + TIMEOUT, "Too early");
        
        o.status = Status.Completed;

        payable(o.seller).transfer(o.amount);
        emit OrderTimedOut(orderId);
    }

    // чистый донат
    function donate() external payable {
        require(msg.value > 0, "Zero value");
        emit DonationReceived(msg.sender, msg.value);
    }

    // депозит для OSP
    function depositForOSP() external payable {
        require(msg.value > 0, "Zero value");
        emit DepositForOSP(msg.sender, msg.value);
    }
}