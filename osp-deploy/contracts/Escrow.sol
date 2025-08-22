// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";

contract Escrow {
    IERC20 public token;
    address public owner;

    event Deposit(address indexed from, uint256 amount, string referenceId);
    event Released(address indexed to, uint256 amount, string referenceId);
    event Refunded(address indexed to, uint256 amount, string referenceId);

    modifier onlyOwner() {
        require(msg.sender == owner, "Only owner");
        _;
    }

    constructor(address tokenAddress) {
        token = IERC20(tokenAddress);
        owner = msg.sender;
    }

    function deposit(uint256 amount, string calldata referenceId) external {
        require(amount > 0, "Amount>0");
        bool ok = token.transferFrom(msg.sender, address(this), amount);
        require(ok, "Transfer failed");
        emit Deposit(msg.sender, amount, referenceId);
    }

    function release(
        address to,
        uint256 amount,
        string calldata referenceId
    ) external onlyOwner {
        require(amount > 0, "Amount>0");
        bool ok = token.transfer(to, amount);
        require(ok, "Transfer failed");
        emit Released(to, amount, referenceId);
    }

    function refund(
        address to,
        uint256 amount,
        string calldata referenceId
    ) external onlyOwner {
        require(amount > 0, "Amount>0");
        bool ok = token.transfer(to, amount);
        require(ok, "Transfer failed");
        emit Refunded(to, amount, referenceId);
    }
}
