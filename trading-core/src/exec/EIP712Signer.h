#pragma once

#include <string>
#include <vector>
#include <cstdint>
#include <array>

namespace trading {
namespace exec {

struct Order {
    std::string salt;          // uint256 as decimal or hex string
    std::string maker;         // address 0x...
    std::string signer;        // address 0x...
    std::string tokenId;       // uint256
    std::string makerAmount;   // uint256
    std::string takerAmount;   // uint256
    uint8_t side;              // 0 = BUY, 1 = SELL
    uint8_t signatureType;     // 0 = EOA
    std::string timestamp;     // uint256 — milliseconds since epoch
    std::string metadata;      // bytes32 — default to 32 zero bytes
    std::string builder;       // bytes32 — default to 32 zero bytes
    
    // For REST API JSON payload but NOT signed via EIP-712:
    std::string taker;         
    std::string expiration;    
};

struct Signature {
    uint8_t v;
    std::array<uint8_t, 32> r;
    std::array<uint8_t, 32> s;
    std::string rsv_hex;
};

class EIP712Signer {
public:
    EIP712Signer(uint64_t chain_id, const std::string& verifying_contract, const std::string& private_key_hex);
    ~EIP712Signer();

    Signature sign_order(const Order& order) const;

    // Helper: Build the EIP-712 payload for debugging/inspection
    std::array<uint8_t, 32> hash_order(const Order& order) const;

private:
    uint64_t chain_id_;
    std::string verifying_contract_;
    std::array<uint8_t, 32> private_key_;
    std::array<uint8_t, 32> domain_separator_;

    void compute_domain_separator();
};

} // namespace exec
} // namespace trading
