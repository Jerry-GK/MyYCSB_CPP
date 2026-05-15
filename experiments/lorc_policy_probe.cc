// Synthetic replacement-policy probe for LORC.
//
// The probe exercises the actual RBTreeLogicalOrderedRangeCache implementation
// without opening RocksDB. It constructs one large logical range from many
// physical segments, creates an interior hot island, then inserts new hot
// ranges until eviction is required. The output is a compact CSV row per
// victim policy.

#include <rocksdb/cache.h>
#include <rocksdb/rbtree_lorc.h>
#include <rocksdb/ref_range.h>
#include <rocksdb/status.h>

#include <algorithm>
#include <iomanip>
#include <iostream>
#include <memory>
#include <sstream>
#include <string>
#include <vector>

namespace {

std::string Key(uint64_t n) {
  std::ostringstream os;
  os << "user" << std::setw(24) << std::setfill('0') << n;
  return os.str();
}

rocksdb::RangeCacheVictimPolicy ParsePolicy(const std::string& policy) {
  if (policy == "boundary_lru") {
    return rocksdb::RangeCacheVictimPolicy::BOUNDARY_LRU;
  }
  if (policy == "physical_lru") {
    return rocksdb::RangeCacheVictimPolicy::PHYSICAL_LRU;
  }
  if (policy == "shortest_range") {
    return rocksdb::RangeCacheVictimPolicy::SHORTEST_RANGE;
  }
  throw std::invalid_argument("unknown policy: " + policy);
}

void AddChunk(rocksdb::LogicalOrderedRangeCache* cache, uint64_t start,
              uint64_t len, size_t value_size, bool left_concat,
              bool right_concat) {
  std::vector<std::string> keys;
  std::vector<std::string> values;
  keys.reserve(len);
  values.reserve(len);
  rocksdb::ReferringRange ref(true, 1);
  ref.reserve(len);
  for (uint64_t i = 0; i < len; ++i) {
    keys.push_back(Key(start + i));
    values.emplace_back(value_size, static_cast<char>('a' + ((start + i) % 23)));
    ref.emplace(rocksdb::Slice(keys.back()), rocksdb::Slice(values.back()));
  }
  cache->putGapPhysicalRange(std::move(ref), left_concat, right_concat, false, "", "");
}

void TrimToBudget(rocksdb::LogicalOrderedRangeCache* cache) {
  while (cache->getCurrentSize() > cache->getCapacity()) {
    size_t before = cache->getCurrentSize();
    cache->victim();
    if (cache->getCurrentSize() >= before) {
      break;
    }
  }
}

std::string InternalKey(uint64_t key_num) {
  std::string out = Key(key_num);
  uint64_t tag = (uint64_t{1} << 8) | uint64_t{0x18};
  for (int i = 0; i < 8; ++i) {
    out.push_back(static_cast<char>((tag >> (8 * i)) & 0xff));
  }
  return out;
}

bool Contains(rocksdb::LogicalOrderedRangeCache* cache, uint64_t key_num) {
  std::string internal = InternalKey(key_num);
  std::string value;
  rocksdb::Status s;
  return cache->Get(rocksdb::Slice(internal), &value, &s);
}

struct Coverage {
  size_t hit_records = 0;
  size_t cached_parts = 0;
  size_t gap_parts = 0;
};

Coverage MeasureCoverage(rocksdb::LogicalOrderedRangeCache* cache, uint64_t start,
                         uint64_t len) {
  Coverage c;
  bool in_hit = false;
  bool in_gap = false;
  for (uint64_t i = 0; i < len; ++i) {
    bool hit = Contains(cache, start + i);
    if (hit) {
      c.hit_records++;
      if (!in_hit) {
        c.cached_parts++;
      }
    } else if (!in_gap) {
      c.gap_parts++;
    }
    in_hit = hit;
    in_gap = !hit;
  }
  return c;
}

struct Result {
  std::string policy;
  size_t hot_hit_records;
  size_t hot_cached_parts;
  size_t hot_gap_parts;
  size_t crossing_hit_records;
  size_t crossing_cached_parts;
  size_t crossing_gap_parts;
  size_t new_hit_records;
  size_t new_cached_parts;
  size_t new_gap_parts;
  size_t current_size;
  size_t total_records;
};

Result RunPolicy(const std::string& policy) {
  constexpr uint64_t kChunkLen = 128;
  constexpr size_t kValueSize = 512;
  constexpr size_t kApproxRecordBytes = 548;
  constexpr size_t kCapacity = kChunkLen * kApproxRecordBytes * 14;

  auto cache = std::make_shared<rocksdb::RBTreeLogicalOrderedRangeCache>(
      kCapacity, rocksdb::LorcLogger::Level::DISABLE,
      rocksdb::PhysicalRangeType::CONTINUOUS, ParsePolicy(policy));

  // Build one old contiguous logical range from 12 physical chunks.
  for (uint64_t chunk = 0; chunk < 12; ++chunk) {
    AddChunk(cache.get(), chunk * kChunkLen, kChunkLen, kValueSize,
             chunk > 0, false);
    TrimToBudget(cache.get());
  }

  // Make the middle of the old range hot while its surrounding interior remains
  // stale. This is the pattern that exposes whether replacement preserves a
  // useful logical island or creates many crossing holes.
  for (int round = 0; round < 40; ++round) {
    cache->pinRange(Key(5 * kChunkLen));
    cache->pinRange(Key(6 * kChunkLen));
  }

  // Insert a new, separate hot region. These chunks are recent and should not
  // immediately be discarded merely because the old range is longer.
  for (uint64_t chunk = 0; chunk < 9; ++chunk) {
    AddChunk(cache.get(), 100000 + chunk * kChunkLen, kChunkLen, kValueSize,
             chunk > 0, false);
    TrimToBudget(cache.get());
  }

  Coverage hot = MeasureCoverage(cache.get(), 5 * kChunkLen, 2 * kChunkLen);
  Coverage crossing = MeasureCoverage(cache.get(), 0, 12 * kChunkLen);
  Coverage newer = MeasureCoverage(cache.get(), 100000, 5 * kChunkLen);
  return Result{policy,
                hot.hit_records,
                hot.cached_parts,
                hot.gap_parts,
                crossing.hit_records,
                crossing.cached_parts,
                crossing.gap_parts,
                newer.hit_records,
                newer.cached_parts,
                newer.gap_parts,
                cache->getCurrentSize(),
                cache->getTotalRangeLength()};
}

}  // namespace

int main() {
  std::cout << "policy,hot_hit_records,hot_cached_parts,hot_gap_parts,"
               "crossing_hit_records,crossing_cached_parts,crossing_gap_parts,"
               "new_hit_records,new_cached_parts,new_gap_parts,"
               "current_size,total_records\n";
  for (const std::string policy : {"boundary_lru", "physical_lru", "shortest_range"}) {
    Result r = RunPolicy(policy);
    std::cout << r.policy << "," << r.hot_hit_records << ","
              << r.hot_cached_parts << "," << r.hot_gap_parts << ","
              << r.crossing_hit_records << "," << r.crossing_cached_parts << ","
              << r.crossing_gap_parts << "," << r.new_hit_records << ","
              << r.new_cached_parts << "," << r.new_gap_parts << ","
              << r.current_size << ","
              << r.total_records << "\n";
  }
  return 0;
}
