// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

/// A minimal account ledger. Only the operator (the payment switch) can write.
contract PaymentLedger {
    address public immutable operator;

    mapping(bytes32 => uint256) public balanceOf;   // account => balance in ore
    mapping(bytes32 => bool) public processed;      // transfer id => already done

    event Minted(bytes32 indexed account, uint256 amount);
    event Transferred(bytes32 indexed transferId, bytes32 indexed from, bytes32 indexed to, uint256 amount);

    constructor() {
        operator = msg.sender;
    }

    modifier onlyOperator() {
        require(msg.sender == operator, "not the operator");
        _;
    }

    function mint(bytes32 account, uint256 amount) external onlyOperator {
        balanceOf[account] += amount;
        emit Minted(account, amount);
    }

    function transfer(bytes32 transferId, bytes32 from, bytes32 to, uint256 amount) external onlyOperator {
        require(!processed[transferId], "duplicate transfer");
        require(amount > 0, "amount must be positive");
        require(balanceOf[from] >= amount, "insufficient funds");
        processed[transferId] = true;
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
        emit Transferred(transferId, from, to, amount);
    }
}