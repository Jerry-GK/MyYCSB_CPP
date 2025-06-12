#ifndef YCSB_C_RANDOM_COUNTER_GENERATOR_H_
#define YCSB_C_RANDOM_COUNTER_GENERATOR_H_

#include "generator.h"
#include <vector>
#include <random>
#include <mutex>
#include <atomic>

namespace ycsbc {

class RandomCounterGenerator : public Generator<uint64_t> {
 public:
  RandomCounterGenerator(uint64_t start, uint64_t count);
  uint64_t Next();
  uint64_t Last();
  
 private:
  std::vector<uint64_t> keys_;
  std::atomic<size_t> index_;
  uint64_t start_;
  uint64_t last_returned_;
  std::mutex mutex_;
};

} // ycsbc

#endif // YCSB_C_RANDOM_COUNTER_GENERATOR_H_
