#pragma once

#include <cstdint>
#include <string>
#include <vector>
#include <array>
#include <cstring>

namespace trading {
namespace crypto {

class Keccak256 {
public:
    static constexpr size_t HASH_LEN = 32;

    Keccak256() {
        reset();
    }

    void reset() {
        std::memset(state_, 0, sizeof(state_));
        data_len_ = 0;
    }

    void update(const void* data, size_t length) {
        const uint8_t* d = static_cast<const uint8_t*>(data);
        size_t rate_bytes = 1088 / 8; // 136 bytes for Keccak-256

        while (length > 0) {
            size_t take = std::min(length, rate_bytes - data_len_);
            std::memcpy(buffer_ + data_len_, d, take);
            data_len_ += take;
            d += take;
            length -= take;

            if (data_len_ == rate_bytes) {
                process_block(buffer_);
                data_len_ = 0;
            }
        }
    }

    std::array<uint8_t, HASH_LEN> finalize() {
        std::array<uint8_t, HASH_LEN> hash;
        size_t rate_bytes = 1088 / 8; // 136 bytes for Keccak-256

        // Padding: 0x01 for Ethereum Keccak (NOT 0x06 for SHA-3)
        buffer_[data_len_++] = 0x01;
        std::memset(buffer_ + data_len_, 0, rate_bytes - data_len_);
        buffer_[rate_bytes - 1] |= 0x80;

        process_block(buffer_);

        for (size_t i = 0; i < HASH_LEN / 8; ++i) {
            for (size_t j = 0; j < 8; ++j) {
                hash[i * 8 + j] = static_cast<uint8_t>(state_[i] >> (8 * j));
            }
        }
        
        return hash;
    }

    static std::array<uint8_t, HASH_LEN> hash(const void* data, size_t length) {
        Keccak256 ctx;
        ctx.update(data, length);
        return ctx.finalize();
    }

    static std::array<uint8_t, HASH_LEN> hash(const std::string& data) {
        return hash(data.data(), data.size());
    }

    static std::array<uint8_t, HASH_LEN> hash(const std::vector<uint8_t>& data) {
        return hash(data.data(), data.size());
    }

private:
    uint64_t state_[25];
    uint8_t buffer_[136];
    size_t data_len_;

    static uint64_t rotl64(uint64_t x, int n) {
        return (x << n) | (x >> (64 - n));
    }

    void process_block(const uint8_t* block) {
        for (int i = 0; i < 17; ++i) { // 136 / 8 = 17
            uint64_t v = 0;
            for (int j = 0; j < 8; ++j) {
                v |= static_cast<uint64_t>(block[i * 8 + j]) << (8 * j);
            }
            state_[i] ^= v;
        }

        static const uint64_t RC[24] = {
            0x0000000000000001ULL, 0x0000000000008082ULL, 0x800000000000808aULL,
            0x8000000080008000ULL, 0x000000000000808bULL, 0x0000000080000001ULL,
            0x8000000080008081ULL, 0x8000000000008009ULL, 0x000000000000008aULL,
            0x0000000000000088ULL, 0x0000000080008009ULL, 0x000000008000000aULL,
            0x000000008000808bULL, 0x800000000000008bULL, 0x8000000000008089ULL,
            0x8000000000008003ULL, 0x8000000000008002ULL, 0x8000000000000080ULL,
            0x000000000000800aULL, 0x800000008000000aULL, 0x8000000080008081ULL,
            0x8000000000008080ULL, 0x0000000080000001ULL, 0x8000000080008008ULL
        };

        for (int r = 0; r < 24; ++r) {
            uint64_t C[5], D[5];
            for (int i = 0; i < 5; ++i) {
                C[i] = state_[i] ^ state_[i + 5] ^ state_[i + 10] ^ state_[i + 15] ^ state_[i + 20];
            }
            for (int i = 0; i < 5; ++i) {
                D[i] = C[(i + 4) % 5] ^ rotl64(C[(i + 1) % 5], 1);
            }
            for (int i = 0; i < 25; ++i) {
                state_[i] ^= D[i % 5];
            }

            uint64_t x = 1, y = 0;
            uint64_t current = state_[1];
            for (int t = 0; t < 24; ++t) {
                int nextX = y;
                int nextY = (2 * x + 3 * y) % 5;
                uint64_t temp = state_[nextX + 5 * nextY];
                state_[nextX + 5 * nextY] = rotl64(current, (t + 1) * (t + 2) / 2);
                current = temp;
                x = nextX;
                y = nextY;
            }

            for (int j = 0; j < 25; j += 5) {
                for (int i = 0; i < 5; ++i) {
                    C[i] = state_[j + i];
                }
                for (int i = 0; i < 5; ++i) {
                    state_[j + i] ^= (~C[(i + 1) % 5]) & C[(i + 2) % 5];
                }
            }

            state_[0] ^= RC[r];
        }
    }
};

} // namespace crypto
} // namespace trading
