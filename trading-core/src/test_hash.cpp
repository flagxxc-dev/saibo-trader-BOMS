#include <iostream>
#include "EIP712Signer.h"

int main() {
    try {
        EIP712Signer signer(
            137, 
            "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E", 
            "6c3a37e4f57c8658b230573d657de86d6e6942584732e7e7b65fb5c66a0a91eb"
        );

        Order order;
        order.salt = "164821168";
        order.maker = "0x22da54A4C672a491F9E5aA80Cf7f6D78b01876C2";
        order.signer = "0x976db925aA5674d42fd249Cb9bab15699617091B";
        order.taker = "0x0000000000000000000000000000000000000000";
        order.tokenId = "74709108512487296077939520431788121726592592304942187437872636022334018784629";
        order.makerAmount = "2300000";
        order.takerAmount = "5000000";
        order.expiration = "0";
        order.nonce = "0";
        order.feeRateBps = "1000";
        order.side = 0; // BUY
        order.signatureType = 1;

        Signature sig = signer.sign_order(order);

        std::cout << "C++ Signature: " << sig.signature << std::endl;
    } catch (const std::exception& e) {
        std::cerr << "Error: " << e.what() << std::endl;
    }
    return 0;
}
