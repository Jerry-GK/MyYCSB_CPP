//
//  utils.h
//  YCSB-C
//
//  Created by Jinglei Ren on 12/5/14.
//  Copyright (c) 2014 Jinglei Ren <jinglei@ren.systems>.
//  Modifications Copyright 2023 Chengye YU <yuchengye2013 AT outlook.com>.
//

#ifndef YCSB_C_UTILS_H_
#define YCSB_C_UTILS_H_

#include <algorithm>
#include <atomic>
#include <cstdlib>
#include <cstdint>
#include <exception>
#include <functional>
#include <random>
#include <locale>
#include <thread>

#if defined(_MSC_VER)
#if _MSC_VER >= 1911
#define MAYBE_UNUSED [[maybe_unused]]
#else
#define MAYBE_UNUSED
#endif
#elif defined(__GNUC__)
#define MAYBE_UNUSED __attribute__ ((unused))
#endif

namespace ycsbc {

namespace utils {

const uint64_t kFNVOffsetBasis64 = 0xCBF29CE484222325ull;
const uint64_t kFNVPrime64 = 1099511628211ull;

inline uint64_t FNVHash64(uint64_t val) {
  uint64_t hash = kFNVOffsetBasis64;

  for (int i = 0; i < 8; i++) {
    uint64_t octet = val & 0x00ff;
    val = val >> 8;

    hash = hash ^ octet;
    hash = hash * kFNVPrime64;
  }
  return hash;
}

inline uint64_t Hash(uint64_t val) { return FNVHash64(val); }

inline uint32_t InitialRandomSeed() {
  const char *seed_env = std::getenv("YCSB_RANDOM_SEED");
  uint32_t seed = 0;
  if (seed_env != nullptr && seed_env[0] != '\0') {
    static std::atomic<uint32_t> next_thread_ordinal{0};
    const uint32_t ordinal =
        next_thread_ordinal.fetch_add(1, std::memory_order_relaxed);
    seed = static_cast<uint32_t>(std::stoul(seed_env));
    seed += 0x9e3779b9u * ordinal;
  } else {
    std::random_device rd;
    seed = rd();
    seed ^= static_cast<uint32_t>(
        std::hash<std::thread::id>{}(std::this_thread::get_id()));
  }
  return seed == 0 ? 1 : seed;
}

inline std::minstd_rand &ThreadLocalRandomEngine() {
  static thread_local std::minstd_rand rn(InitialRandomSeed());
  return rn;
}

inline uint32_t ThreadLocalRandomInt() {
  return ThreadLocalRandomEngine()();
}

inline double ThreadLocalRandomDouble(double min = 0.0, double max = 1.0) {
  static thread_local std::uniform_real_distribution<double> uniform(min, max);
  return uniform(ThreadLocalRandomEngine());
}

///
/// Returns an ASCII code that can be printed to desplay
///
inline char RandomPrintChar() {
  return rand() % 94 + 33;
}

class Exception : public std::exception {
 public:
  Exception(const std::string &message) : message_(message) { }
  const char* what() const noexcept {
    return message_.c_str();
  }
 private:
  std::string message_;
};

inline bool StrToBool(std::string str) {
  std::transform(str.begin(), str.end(), str.begin(), ::tolower);
  if (str == "true" || str == "1") {
    return true;
  } else if (str == "false" || str == "0") {
    return false;
  } else {
    throw Exception("Invalid bool string: " + str);
  }
}

inline std::string Trim(const std::string &str) {
  auto front = std::find_if_not(str.begin(), str.end(), [](int c){ return std::isspace(c); });
  return std::string(front, std::find_if_not(str.rbegin(), std::string::const_reverse_iterator(front),
      [](int c){ return std::isspace(c); }).base());
}

} // utils

} // ycsbc

#endif // YCSB_C_UTILS_H_
