#include "random_acknowledged_counter_generator.h"
#include <algorithm>

namespace ycsbc {

RandomAcknowledgedCounterGenerator::RandomAcknowledgedCounterGenerator(uint64_t start, uint64_t max_count) 
    : index_(0), start_(start), last_returned_(start - 1) {
  keys_.reserve(max_count);
  for (uint64_t i = 0; i < max_count; ++i) {
    keys_.push_back(start + i);
  }
  
  // Shuffle the keys for random order
  std::random_device rd;
  std::mt19937 gen(rd());
  std::shuffle(keys_.begin(), keys_.end(), gen);
}

uint64_t RandomAcknowledgedCounterGenerator::Next() {
  size_t current_index = index_.fetch_add(1);
  if (current_index >= keys_.size()) {
    return start_; // Fallback to start if exceeded
  }
  
  std::lock_guard<std::mutex> lock(mutex_);
  last_returned_ = keys_[current_index];
  return last_returned_;
}

uint64_t RandomAcknowledgedCounterGenerator::Last() {
  std::lock_guard<std::mutex> lock(mutex_);
  return last_returned_;
}

void RandomAcknowledgedCounterGenerator::Acknowledge(uint64_t value) {
  std::lock_guard<std::mutex> lock(mutex_);
  acknowledged_.insert(value);
}

} // ycsbc
