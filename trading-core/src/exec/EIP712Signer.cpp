#include "EIP712Signer.h"
#include "keccak256.hpp"
#include <secp256k1.h>
#include <secp256k1_recovery.h>
#include <stdexcept>
#include <iomanip>
#include <sstream>
#include <boost/multiprecision/cpp_int.hpp>

namespace trading {
namespace exec {

namespace {

std::array<uint8_t, 32> hex_to_bytes32(std::string hex) {
    if (hex.size() >= 2 && hex[0] == '0' && (hex[1] == 'x' || hex[1] == 'X')) {
        hex = hex.substr(2);
    }
    std::array<uint8_t, 32> bytes = {0};
    int offset = 64 - hex.length();
    if (offset < 0) throw std::invalid_argument("Hex string too long for bytes32");
    
    for (size_t i = 0; i < hex.length(); i += 2) {
        std::string byteString = hex.substr(i, 2);
        if (byteString.length() == 1) byteString = "0" + byteString;
        bytes[(offset + i) / 2] = static_cast<uint8_t>(strtol(byteString.c_str(), nullptr, 16));
    }
    return bytes;
}

std::array<uint8_t, 32> uint256_to_bytes32(const std::string& dec_or_hex) {
    boost::multiprecision::uint256_t val(dec_or_hex);
    std::array<uint8_t, 32> bytes = {0};
    export_bits(val, bytes.rbegin(), 8, false); // export bits in big-endian
    return bytes;
}

std::string bytes_to_hex(const uint8_t* data, size_t len) {
    std::stringstream ss;
    ss << std::hex << std::setfill('0');
    for (size_t i = 0; i < len; ++i) {
        ss << std::setw(2) << static_cast<int>(data[i]);
    }
    return "0x" + ss.str();
}

} // namespace

EIP712Signer::EIP712Signer(uint64_t chain_id, const std::string& verifying_contract, const std::string& private_key_hex)
    : chain_id_(chain_id), verifying_contract_(verifying_contract) {
    private_key_ = hex_to_bytes32(private_key_hex);
    compute_domain_separator();
}

EIP712Signer::~EIP712Signer() {}

void EIP712Signer::compute_domain_separator() {
    // EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)
    const std::string domain_type = "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)";
    auto type_hash = crypto::Keccak256::hash(domain_type);
    auto name_hash = crypto::Keccak256::hash("Polymarket CTF Exchange");
    auto version_hash = crypto::Keccak256::hash("2");
    auto chain_id_bytes = uint256_to_bytes32(std::to_string(chain_id_));
    auto contract_bytes = hex_to_bytes32(verifying_contract_);

    std::vector<uint8_t> payload;
    payload.insert(payload.end(), type_hash.begin(), type_hash.end());
    payload.insert(payload.end(), name_hash.begin(), name_hash.end());
    payload.insert(payload.end(), version_hash.begin(), version_hash.end());
    payload.insert(payload.end(), chain_id_bytes.begin(), chain_id_bytes.end());
    payload.insert(payload.end(), contract_bytes.begin(), contract_bytes.end());

    domain_separator_ = crypto::Keccak256::hash(payload);
}

std::array<uint8_t, 32> EIP712Signer::hash_order(const Order& order) const {
    const std::string order_type = "Order(uint256 salt,address maker,address signer,uint256 tokenId,uint256 makerAmount,uint256 takerAmount,uint8 side,uint8 signatureType,uint256 timestamp,bytes32 metadata,bytes32 builder)";
    auto type_hash = crypto::Keccak256::hash(order_type);

    std::vector<uint8_t> payload;
    payload.insert(payload.end(), type_hash.begin(), type_hash.end());
    
    auto push_bytes = [&payload](const std::array<uint8_t, 32>& b) {
        payload.insert(payload.end(), b.begin(), b.end());
    };

    push_bytes(uint256_to_bytes32(order.salt));
    push_bytes(hex_to_bytes32(order.maker));
    push_bytes(hex_to_bytes32(order.signer));
    push_bytes(uint256_to_bytes32(order.tokenId));
    push_bytes(uint256_to_bytes32(order.makerAmount));
    push_bytes(uint256_to_bytes32(order.takerAmount));
    push_bytes(uint256_to_bytes32(std::to_string(order.side)));
    push_bytes(uint256_to_bytes32(std::to_string(order.signatureType)));
    push_bytes(uint256_to_bytes32(order.timestamp));
    push_bytes(hex_to_bytes32(order.metadata));
    push_bytes(hex_to_bytes32(order.builder));

    auto struct_hash = crypto::Keccak256::hash(payload);

    std::vector<uint8_t> encode_data = {0x19, 0x01};
    encode_data.insert(encode_data.end(), domain_separator_.begin(), domain_separator_.end());
    encode_data.insert(encode_data.end(), struct_hash.begin(), struct_hash.end());

    return crypto::Keccak256::hash(encode_data);
}

Signature EIP712Signer::sign_order(const Order& order) const {
    auto digest = hash_order(order);

    secp256k1_context* ctx = secp256k1_context_create(SECP256K1_CONTEXT_SIGN);
    secp256k1_ecdsa_recoverable_signature sig;
    
    if (!secp256k1_ecdsa_sign_recoverable(ctx, &sig, digest.data(), private_key_.data(), secp256k1_nonce_function_rfc6979, nullptr)) {
        secp256k1_context_destroy(ctx);
        throw std::runtime_error("Failed to sign order");
    }

    int recid;
    std::array<uint8_t, 64> sig64;
    secp256k1_ecdsa_recoverable_signature_serialize_compact(ctx, sig64.data(), &recid, &sig);
    secp256k1_context_destroy(ctx);

    Signature result;
    std::copy(sig64.begin(), sig64.begin() + 32, result.r.begin());
    std::copy(sig64.begin() + 32, sig64.begin() + 64, result.s.begin());
    result.v = recid + 27; // Ethereum v is 27 or 28

    std::vector<uint8_t> rsv;
    rsv.insert(rsv.end(), result.r.begin(), result.r.end());
    rsv.insert(rsv.end(), result.s.begin(), result.s.end());
    rsv.push_back(result.v);
    
    result.rsv_hex = bytes_to_hex(rsv.data(), rsv.size());
    return result;
}

} // namespace exec
} // namespace trading
