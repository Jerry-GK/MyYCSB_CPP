#ifndef YCSB_C_PERF_COUNTERS_H_
#define YCSB_C_PERF_COUNTERS_H_

#include <cerrno>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <linux/perf_event.h>
#include <stdexcept>
#include <string>
#include <sys/ioctl.h>
#include <sys/syscall.h>
#include <unistd.h>

namespace ycsbc {
namespace utils {

class L1DPerfCounters {
 public:
  L1DPerfCounters() {
    perf_event_attr loads{};
    loads.type = PERF_TYPE_HW_CACHE;
    loads.size = sizeof(perf_event_attr);
    loads.config = CacheConfig(PERF_COUNT_HW_CACHE_L1D,
                               PERF_COUNT_HW_CACHE_OP_READ,
                               PERF_COUNT_HW_CACHE_RESULT_ACCESS);
    loads.disabled = 1;
    loads.exclude_kernel = 1;
    loads.exclude_hv = 1;
    loads.read_format = PERF_FORMAT_GROUP;

    loads_fd_ = PerfOpen(&loads, -1);
    if (loads_fd_ < 0) {
      throw std::runtime_error(std::string("perf_event_open l1d loads failed: ") +
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

    misses_fd_ = PerfOpen(&misses, loads_fd_);
    if (misses_fd_ < 0) {
      const std::string err = std::strerror(errno);
      close(loads_fd_);
      loads_fd_ = -1;
      throw std::runtime_error("perf_event_open l1d misses failed: " + err);
    }
  }

  ~L1DPerfCounters() {
    if (misses_fd_ >= 0) close(misses_fd_);
    if (loads_fd_ >= 0) close(loads_fd_);
  }

  void Start() {
    ioctl(loads_fd_, PERF_EVENT_IOC_RESET, PERF_IOC_FLAG_GROUP);
    ioctl(loads_fd_, PERF_EVENT_IOC_ENABLE, PERF_IOC_FLAG_GROUP);
  }

  void Stop() {
    ioctl(loads_fd_, PERF_EVENT_IOC_DISABLE, PERF_IOC_FLAG_GROUP);
    struct ReadFormat {
      uint64_t nr;
      uint64_t values[2];
    } data{};
    const ssize_t n = read(loads_fd_, &data, sizeof(data));
    if (n < static_cast<ssize_t>(sizeof(data)) || data.nr != 2) {
      throw std::runtime_error("failed to read L1D perf counter group");
    }
    l1d_loads_ = data.values[0];
    l1d_misses_ = data.values[1];
  }

  void Print(const std::string& label) const {
    const double hit_rate =
        l1d_loads_ == 0 || l1d_misses_ > l1d_loads_
            ? 0.0
            : 1.0 - static_cast<double>(l1d_misses_) /
                        static_cast<double>(l1d_loads_);
    std::cout << "[PERF_L1D] label=" << label
              << " l1d_loads=" << l1d_loads_
              << " l1d_load_misses=" << l1d_misses_
              << " l1d_load_hit_rate=" << hit_rate << std::endl;
  }

 private:
  static uint64_t CacheConfig(uint64_t cache_id, uint64_t op_id,
                              uint64_t result_id) {
    return cache_id | (op_id << 8) | (result_id << 16);
  }

  static int PerfOpen(perf_event_attr* attr, int group_fd) {
    return static_cast<int>(
        syscall(SYS_perf_event_open, attr, 0, -1, group_fd, 0));
  }

  int loads_fd_ = -1;
  int misses_fd_ = -1;
  uint64_t l1d_loads_ = 0;
  uint64_t l1d_misses_ = 0;
};

}  // namespace utils
}  // namespace ycsbc

#endif  // YCSB_C_PERF_COUNTERS_H_
