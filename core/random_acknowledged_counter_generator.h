#ifndef YCSB_C_RANDOM_ACKNOWLEDGED_COUNTER_GENERATOR_H_
#define YCSB_C_RANDOM_ACKNOWLEDGED_COUNTER_GENERATOR_H_

#include "generator.h"
#include <vector>
#include <random>
#include <mutex>
#include <unordered_set>
#include <atomic>

namespace ycsbc {

class RandomAcknowledgedCounterGenerator : public Generator<uint64_t> {
 public:
  RandomAcknowledgedCounterGenerator(uint64_t start, uint64_t max_count);
  uint64_t Next();
  uint64_t Last();
  void Acknowledge(uint64_t value);
  
 private:
  std::vector<uint64_t> keys_;
  std::atomic<size_t> index_;
  uint64_t start_;
  uint64_t last_returned_;
  std::unordered_set<uint64_t> acknowledged_;
  std::mutex mutex_;
};

} // ycsbc

#endif // YCSB_C_RANDOM_ACKNOWLEDGED_COUNTER_GENERATOR_H_
