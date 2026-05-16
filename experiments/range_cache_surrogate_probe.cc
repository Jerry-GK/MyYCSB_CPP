// Controlled in-memory probe for range-cache representation cost.
//
// This is not a reimplementation of any published system. It isolates one
// question that is otherwise hard to see in end-to-end RocksDB numbers: when a
// range is already fully cached, how much traversal work is paid by an
// entry-granular ordered cache versus a segment-granular cache?

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <map>
#include <numeric>
#include <random>
#include <sstream>
#include <string>
#include <vector>

namespace {

volatile uint64_t g_sink = 0;

std::string Key(uint64_t n) {
  std::ostringstream os;
  os << "user" << std::setw(24) << std::setfill('0') << n;
  return os.str();
}

std::string Value(uint64_t n, size_t value_size) {
  std::string value(value_size, static_cast<char>('a' + (n % 23)));
  if (!value.empty()) {
    value[0] = static_cast<char>(n & 0xff);
    value[value.size() - 1] = static_cast<char>((n >> 8) & 0xff);
  }
  return value;
}

struct Query {
  uint64_t start;
  size_t len;
};

std::vector<Query> MakeQueries(size_t count, uint64_t record_count,
                               size_t scan_len) {
  std::mt19937_64 rng(17 + scan_len);
  const uint64_t hot_start = record_count / 3;
  const uint64_t hot_end = hot_start + record_count / 12;
  std::uniform_int_distribution<uint64_t> hot_dist(hot_start,
                                                   hot_end - scan_len - 1);
  std::vector<Query> queries;
  queries.reserve(count);
  for (size_t i = 0; i < count; ++i) {
    queries.push_back({hot_dist(rng), scan_len});
  }
  return queries;
}

class EntryOrderedCache {
 public:
  EntryOrderedCache(uint64_t record_count, size_t value_size) {
    for (uint64_t i = 0; i < record_count; ++i) {
      entries_.emplace(Key(i), Value(i, value_size));
    }
  }

  uint64_t Scan(uint64_t start, size_t len) const {
    uint64_t checksum = 0;
    auto it = entries_.lower_bound(Key(start));
    for (size_t i = 0; i < len && it != entries_.end(); ++i, ++it) {
      checksum += static_cast<unsigned char>(it->second[0]);
      checksum += static_cast<unsigned char>(it->second[it->second.size() - 1]);
    }
    return checksum;
  }

 private:
  std::map<std::string, std::string> entries_;
};

class VecSegmentCache {
 public:
  VecSegmentCache(uint64_t record_count, size_t value_size) {
    keys_.reserve(record_count);
    values_.reserve(record_count);
    for (uint64_t i = 0; i < record_count; ++i) {
      keys_.push_back(Key(i));
      values_.push_back(Value(i, value_size));
    }
  }

  uint64_t Scan(uint64_t start, size_t len) const {
    uint64_t checksum = 0;
    auto it = std::lower_bound(keys_.begin(), keys_.end(), Key(start));
    size_t pos = static_cast<size_t>(it - keys_.begin());
    for (size_t i = 0; i < len && pos + i < values_.size(); ++i) {
      const std::string& value = values_[pos + i];
      checksum += static_cast<unsigned char>(value[0]);
      checksum += static_cast<unsigned char>(value[value.size() - 1]);
    }
    return checksum;
  }

 private:
  std::vector<std::string> keys_;
  std::vector<std::string> values_;
};

class ContinuousSegmentCache {
 public:
  ContinuousSegmentCache(uint64_t record_count, size_t value_size)
      : value_size_(value_size) {
    keys_.reserve(record_count);
    payload_.reserve(record_count * value_size);
    for (uint64_t i = 0; i < record_count; ++i) {
      keys_.push_back(Key(i));
      std::string value = Value(i, value_size);
      payload_.append(value.data(), value.size());
    }
  }

  uint64_t Scan(uint64_t start, size_t len) const {
    uint64_t checksum = 0;
    auto it = std::lower_bound(keys_.begin(), keys_.end(), Key(start));
    size_t pos = static_cast<size_t>(it - keys_.begin());
    const size_t end = std::min(pos + len, keys_.size());
    for (; pos < end; ++pos) {
      const char* value = payload_.data() + pos * value_size_;
      checksum += static_cast<unsigned char>(value[0]);
      checksum += static_cast<unsigned char>(value[value_size_ - 1]);
    }
    return checksum;
  }

 private:
  size_t value_size_;
  std::vector<std::string> keys_;
  std::string payload_;
};

template <class Cache>
double TimeNsPerScan(const Cache& cache, const std::vector<Query>& queries,
                     int repeats) {
  uint64_t checksum = 0;
  auto start = std::chrono::steady_clock::now();
  for (int r = 0; r < repeats; ++r) {
    for (const Query& q : queries) {
      checksum += cache.Scan(q.start, q.len);
    }
  }
  auto end = std::chrono::steady_clock::now();
  g_sink += checksum;
  const double ns =
      std::chrono::duration_cast<std::chrono::nanoseconds>(end - start).count();
  return ns / static_cast<double>(queries.size() * repeats);
}

}  // namespace

int main() {
  constexpr uint64_t kRecordCount = 200000;
  constexpr size_t kValueSize = 1024;
  constexpr size_t kQueryCount = 20000;
  constexpr int kRepeats = 5;
  const std::vector<size_t> scan_lengths = {5, 20, 100, 400};

  EntryOrderedCache entry_cache(kRecordCount, kValueSize);
  VecSegmentCache vec_segment(kRecordCount, kValueSize);
  ContinuousSegmentCache continuous_segment(kRecordCount, kValueSize);

  std::cout << "scan_length,entry_ordered_ns,vec_segment_ns,"
               "continuous_segment_ns,continuous_vs_entry_speedup,"
               "continuous_vs_vec_speedup\n";
  for (size_t len : scan_lengths) {
    auto queries = MakeQueries(kQueryCount, kRecordCount, len);
    const double entry_ns = TimeNsPerScan(entry_cache, queries, kRepeats);
    const double vec_ns = TimeNsPerScan(vec_segment, queries, kRepeats);
    const double cont_ns = TimeNsPerScan(continuous_segment, queries, kRepeats);
    std::cout << len << "," << entry_ns << "," << vec_ns << "," << cont_ns
              << "," << (entry_ns / cont_ns) << "," << (vec_ns / cont_ns)
              << "\n";
  }
  if (g_sink == 0) {
    std::cerr << "unexpected zero checksum\n";
    return 1;
  }
  return 0;
}
