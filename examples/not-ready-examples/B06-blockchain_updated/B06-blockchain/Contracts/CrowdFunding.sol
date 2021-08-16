pragma solidity ^0.8.0;

contract Crowdfunding {
    uint256 amount;
    address payable owner;
    uint256 goal;
    uint256 deadline;
    mapping(address => uint256) pledgeOf;

    function setIntitalValue(uint256 numberOfDays, uint256 _goal, address payable _owner) public {
        owner = _owner;
        deadline = block.timestamp + (numberOfDays * 1 days);
        goal = _goal;
    }

    receive() external payable {
    	require(block.timestamp < deadline, "deadline has expired");
    	require(goal < amount, "required goal amount is met");
    	pledgeOf[msg.sender] += msg.value;
    	amount += msg.value;
    }

    function claimFunds() public payable {
    	require(goal > amount, "required goal amount is not yet met");
    	require(block.timestamp >= deadline, "deadline has expired");
        owner.transfer(amount);
    }

    function getRefund(address payable _to) public payable {
    	require(block.timestamp > deadline, "deadline has not yet expired");
    	uint256 amountDeposited = pledgeOf[_to];
    	_to.transfer(amountDeposited);
    }
}