// Controlled in-memory probe for range-cache representation locality.
//
// This is not a reimplementation of Range Cache. It isolates one mechanism:
// when a queried range is already fully cached, an entry-granular ordered cache
// traverses many independently allocated skip-list nodes, while LORC's physical
// segment scans a compact ordered payload. We measure L1D load counters only
// around the warmed scan loop, excluding construction and query generation.

#include <algorithm>
#include <array>
#include <chrono>
#include <cerrno>
#include <cstdint>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <linux/perf_event.h>
#include <numeric>
#include <random>
#include <sstream>
#include <stdexcept>
#include <string>
#include <sys/ioctl.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <utility>
#include <vector>

namespace {

volatile uint64_t g_sink = 0;

uint64_t CacheConfig(uint64_t cache_id, uint64_t op_id, uint64_t result_id) {
  return cache_id | (op_id << 8) | (result_id << 16);
}

int PerfOpen(perf_event_attr* attr, int group_fd) {
  return static_cast<int>(
      syscall(SYS_perf_event_open, attr, 0, -1, group_fd, 0));
}

class PerfCounters {
 public:
  PerfCounters() {
    perf_event_attr refs{};
    refs.type = PERF_TYPE_HW_CACHE;
    refs.size = sizeof(perf_event_attr);
    refs.config = CacheConfig(PERF_COUNT_HW_CACHE_L1D,
                              PERF_COUNT_HW_CACHE_OP_READ,
                              PERF_COUNT_HW_CACHE_RESULT_ACCESS);
    refs.disabled = 1;
    refs.exclude_kernel = 1;
    refs.exclude_hv = 1;
    refs.read_format = PERF_FORMAT_GROUP;

    refs_fd_ = PerfOpen(&refs, -1);
    if (refs_fd_ < 0) {
      throw std::runtime_error(std::string("perf_event_open refs failed: ") +
                               std::strerror(errno));
    }

    perf_event_attr misses{};
    misses.type = PERF_TYPE_HW_CACHE;
    misses.size = sizeof(perf_event_attr);
    misses.config = CacheConfig(PERF_COUNT_HW_CACHE_L1D,
                                PERF_COUNT_HW_CACHE_OP_READ,
                                PERF_COUNT_HW_CACHE_RESULT_MISS);
    misses.disabled = 0;
    misses.exclude_kernel = 1;
    misses.exclude_hv = 1;
    misses.read_format = PERF_FORMAT_GROUP;

    misses_fd_ = PerfOpen(&misses, refs_fd_);
    if (misses_fd_ < 0) {
      const std::string err = std::strerror(errno);
      close(refs_fd_);
      refs_fd_ = -1;
      throw std::runtime_error("perf_event_open misses failed: " + err);
    }
  }

  ~PerfCounters() {
    if (misses_fd_ >= 0) close(misses_fd_);
    if (refs_fd_ >= 0) close(refs_fd_);
  }

  void Start() {
    ioctl(refs_fd_, PERF_EVENT_IOC_RESET, PERF_IOC_FLAG_GROUP);
    ioctl(refs_fd_, PERF_EVENT_IOC_ENABLE, PERF_IOC_FLAG_GROUP);
  }

  std::pair<uint64_t, uint64_t> Stop() {
    ioctl(refs_fd_, PERF_EVENT_IOC_DISABLE, PERF_IOC_FLAG_GROUP);
    struct ReadFormat {
      uint64_t nr;
      uint64_t values[2];
    } data{};
    const ssize_t n = read(refs_fd_, &data, sizeof(data));
    if (n < static_cast<ssize_t>(sizeof(data)) || data.nr != 2) {
      throw std::runtime_error("failed to read perf counter group");
    }
    return {data.values[0], data.values[1]};
  }

 private:
  int refs_fd_ = -1;
  int misses_fd_ = -1;
};

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
  std::mt19937_64 rng(17);
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

class EntryOrderedSkipListCache {
 public:
  EntryOrderedSkipListCache(uint64_t record_count, size_t value_size)
      : head_(new Node(kMaxHeight, "", "")), height_(1), rng_(23) {
    std::vector<uint64_t> ids(record_count);
    std::iota(ids.begin(), ids.end(), 0);
    std::shuffle(ids.begin(), ids.end(), std::mt19937_64(41));
    for (uint64_t id : ids) {
      Insert(Key(id), Value(id, value_size));
    }
  }

  ~EntryOrderedSkipListCache() {
    Node* node = head_;
    while (node != nullptr) {
      Node* next = node->next[0];
      delete node;
      node = next;
    }
  }

  uint64_t Scan(uint64_t start, size_t len) const {
    uint64_t checksum = 0;
    Node* node = LowerBound(Key(start));
    for (size_t i = 0; i < len && node != nullptr; ++i, node = node->next[0]) {
      checksum += static_cast<unsigned char>(node->value[0]);
      checksum +=
          static_cast<unsigned char>(node->value[node->value.size() - 1]);
    }
    return checksum;
  }

 private:
  static constexpr int kMaxHeight = 16;

  struct Node {
    Node(int height, std::string k, std::string v)
        : key(std::move(k)), value(std::move(v)), height(height) {
      next.fill(nullptr);
    }

    std::string key;
    std::string value;
    int height;
    std::array<Node*, kMaxHeight> next;
  };

  int RandomHeight() {
    int height = 1;
    while (height < kMaxHeight && (rng_() & 3) == 0) {
      ++height;
    }
    return height;
  }

  void Insert(std::string key, std::string value) {
    Node* update[kMaxHeight];
    Node* x = head_;
    for (int level = height_ - 1; level >= 0; --level) {
      while (x->next[level] != nullptr && x->next[level]->key < key) {
        x = x->next[level];
      }
      update[level] = x;
    }

    const int node_height = RandomHeight();
    if (node_height > height_) {
      for (int level = height_; level < node_height; ++level) {
        update[level] = head_;
      }
      height_ = node_height;
    }

    Node* node = new Node(node_height, std::move(key), std::move(value));
    for (int level = 0; level < node_height; ++level) {
      node->next[level] = update[level]->next[level];
      update[level]->next[level] = node;
    }
  }

  Node* LowerBound(const std::string& key) const {
    Node* x = head_;
    for (int level = height_ - 1; level >= 0; --level) {
      while (x->next[level] != nullptr && x->next[level]->key < key) {
        x = x->next[level];
      }
    }
    return x->next[0];
  }

  Node* head_;
  int height_;
  std::mt19937_64 rng_;
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
std::pair<uint64_t, uint64_t> MeasureCacheCounters(
    const Cache& cache, const std::vector<Query>& queries, int repeats) {
  uint64_t checksum = 0;

  // One unmeasured pass gives both layouts a comparable warm CPU state while
  // leaving the hot interval far larger than private CPU caches.
  for (const Query& q : queries) {
    checksum += cache.Scan(q.start, q.len);
  }

  PerfCounters counters;
  counters.Start();
  for (int r = 0; r < repeats; ++r) {
    for (const Query& q : queries) {
      checksum += cache.Scan(q.start, q.len);
    }
  }
  auto values = counters.Stop();
  g_sink += checksum;
  return values;
}

template <class Cache>
double MeasureScansPerSecond(const Cache& cache,
                             const std::vector<Query>& queries, int repeats) {
  uint64_t checksum = 0;

  for (const Query& q : queries) {
    checksum += cache.Scan(q.start, q.len);
  }

  const auto begin = std::chrono::steady_clock::now();
  for (int r = 0; r < repeats; ++r) {
    for (const Query& q : queries) {
      checksum += cache.Scan(q.start, q.len);
    }
  }
  const auto end = std::chrono::steady_clock::now();
  g_sink += checksum;
  const double seconds = std::chrono::duration<double>(end - begin).count();
  return static_cast<double>(queries.size()) * repeats / seconds;
}

double HitRate(uint64_t refs, uint64_t misses) {
  if (refs == 0 || misses > refs) return 0.0;
  return 1.0 - static_cast<double>(misses) / static_cast<double>(refs);
}

}  // namespace

int main() {
  constexpr uint64_t kRecordCount = 200000;
  constexpr size_t kValueSize = 1024;
  constexpr size_t kQueryCount = 20000;
  constexpr int kRepeats = 16;
  const std::vector<size_t> scan_lengths = {5, 10, 20, 50, 100};

  try {
    EntryOrderedSkipListCache entry_cache(kRecordCount, kValueSize);
    ContinuousSegmentCache continuous_segment(kRecordCount, kValueSize);

    std::cout
        << "scan_length,entry_l1d_loads,entry_l1d_load_misses,"
           "entry_l1d_load_hit_rate,continuous_l1d_loads,"
           "continuous_l1d_load_misses,continuous_l1d_load_hit_rate,"
           "entry_scans_per_sec,continuous_scans_per_sec\n";
    for (size_t len : scan_lengths) {
      auto queries = MakeQueries(kQueryCount, kRecordCount, len);
      const auto entry = MeasureCacheCounters(entry_cache, queries, kRepeats);
      const auto continuous =
          MeasureCacheCounters(continuous_segment, queries, kRepeats);
      const double entry_scans_per_sec =
          MeasureScansPerSecond(entry_cache, queries, kRepeats);
      const double continuous_scans_per_sec =
          MeasureScansPerSecond(continuous_segment, queries, kRepeats);
      std::cout << len << "," << entry.first << "," << entry.second << ","
                << HitRate(entry.first, entry.second) << ","
                << continuous.first << "," << continuous.second << ","
                << HitRate(continuous.first, continuous.second) << ","
                << entry_scans_per_sec << "," << continuous_scans_per_sec
                << "\n";
    }
    if (g_sink == 0) {
      std::cerr << "unexpected zero checksum\n";
      return 1;
    }
    return 0;
  } catch (const std::exception& e) {
    std::cerr << e.what() << "\n";
    return 2;
  }
}
