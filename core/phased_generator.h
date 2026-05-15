//
//  phased_generator.h
//  YCSB-cpp
//
//  Deterministic phased key generator for cache-policy stress tests.
//

#ifndef YCSB_C_PHASED_GENERATOR_H_
#define YCSB_C_PHASED_GENERATOR_H_

#include "generator.h"

#include <cstdint>
#include <random>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace ycsbc {

class PhasedGenerator : public Generator<uint64_t> {
 public:
  // spec format: "start:end:ops;start:end:ops;...".
  // start/end are inclusive key numbers, ops is the number of generated keys
  // before moving to the next phase. The last phase repeats after its budget.
  explicit PhasedGenerator(const std::string& spec) : cursor_(0), last_int_(0) {
    std::stringstream ss(spec);
    std::string item;
    while (std::getline(ss, item, ';')) {
      if (item.empty()) {
        continue;
      }
      std::stringstream is(item);
      std::string start_s;
      std::string end_s;
      std::string ops_s;
      if (!std::getline(is, start_s, ':') || !std::getline(is, end_s, ':') ||
          !std::getline(is, ops_s, ':')) {
        throw std::invalid_argument("Invalid phased generator item: " + item);
      }
      uint64_t start = std::stoull(start_s);
      uint64_t end = std::stoull(end_s);
      uint64_t ops = std::stoull(ops_s);
      if (end < start || ops == 0) {
        throw std::invalid_argument("Invalid phased generator item: " + item);
      }
      phases_.push_back({start, end, ops});
    }
    if (phases_.empty()) {
      throw std::invalid_argument("phased_ranges must contain at least one phase");
    }
    Next();
  }

  uint64_t Next() override {
    const Phase& phase = current_phase();
    std::uniform_int_distribution<uint64_t> dist(phase.start, phase.end);
    last_int_ = dist(generator_);
    cursor_++;
    return last_int_;
  }

  uint64_t Last() override { return last_int_; }

 private:
  struct Phase {
    uint64_t start;
    uint64_t end;
    uint64_t ops;
  };

  const Phase& current_phase() const {
    uint64_t remaining = cursor_;
    for (const auto& phase : phases_) {
      if (remaining < phase.ops) {
        return phase;
      }
      remaining -= phase.ops;
    }
    return phases_.back();
  }

  std::mt19937_64 generator_;
  std::vector<Phase> phases_;
  uint64_t cursor_;
  uint64_t last_int_;
};

}  // namespace ycsbc

#endif  // YCSB_C_PHASED_GENERATOR_H_
