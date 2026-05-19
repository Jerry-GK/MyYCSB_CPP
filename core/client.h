//
//  client.h
//  YCSB-cpp
//
//  Copyright (c) 2020 Youngjae Lee <ls4154.lee@gmail.com>.
//  Copyright (c) 2014 Jinglei Ren <jinglei@ren.systems>.
//

#ifndef YCSB_C_CLIENT_H_
#define YCSB_C_CLIENT_H_

#include <iostream>
#include <string>
#include <atomic>
#include <memory>
#include <thread>
#include <unistd.h>

#include "db.h"
#include "core_workload.h"
#include "utils/countdown_latch.h"
#include "perf_counters.h"
#include "utils/rate_limit.h"
#include "utils/timer.h"
#include "utils/utils.h"

namespace ycsbc {

inline int ClientThread(ycsbc::DB *db, ycsbc::CoreWorkload *wl, const int num_ops, bool is_loading,
                        bool init_db, bool cleanup_db, utils::CountDownLatch *latch, utils::RateLimiter *rlim) {

  try {
    if (init_db) {
      db->Init();
    }

    int ops = 0;
    for (int i = 0; i < num_ops; ++i) {
      if (rlim) {
        rlim->Consume(1);
      }

      if (is_loading) {
        wl->DoInsert(*db);
      } else {
        wl->DoTransaction(*db);
      }
      ops++;
    }

    if (cleanup_db) {
      db->Cleanup();
    }

    latch->CountDown();
    return ops;
  } catch (const utils::Exception &e) {
    std::cerr << "Caught exception: " << e.what() << std::endl;
    exit(1);
  }
}

inline int ClientThreadWithWarmup(ycsbc::DB *db, ycsbc::CoreWorkload *wl, const int num_ops, bool is_loading,
                                  bool init_db, bool cleanup_db, utils::CountDownLatch *latch, 
                                  utils::CountDownLatch *warmup_latch, std::atomic<bool> *measurement_started,
                                  utils::Timer<double> *measurement_timer,
                                  utils::CountDownLatch *measurement_latch,
                                  const int warmup_ops, utils::RateLimiter *rlim,
                                  bool enable_l1d_perf, int thread_id,
                                  int sleep_after_warmup_sec) {

  try {
    if (init_db) {
      db->Init();
    }

    std::unique_ptr<utils::L1DPerfCounters> l1d_counters;
    if (enable_l1d_perf) {
      l1d_counters = std::make_unique<utils::L1DPerfCounters>();
    }

    int ops = 0;
    for (int i = 0; i < num_ops; ++i) {
      if (rlim) {
        rlim->Consume(1);
      }

      // Check if we've completed warmup operations
      if (i == warmup_ops) {
        warmup_latch->CountDown();
        warmup_latch->Await(); // Wait for all threads to complete warmup
        
        // Only one thread should start the measurement timer
        bool expected = false;
        if (measurement_started->compare_exchange_strong(expected, true)) {
          if (sleep_after_warmup_sec > 0) {
            std::cout << "Measurement starts after "
                      << sleep_after_warmup_sec
                      << " sec sleep; pid=" << getpid() << std::endl;
            std::this_thread::sleep_for(
                std::chrono::seconds(sleep_after_warmup_sec));
          }
          measurement_timer->Start();
        }
        if (l1d_counters) {
          l1d_counters->Start();
        }
      }

      if (is_loading) {
        wl->DoInsert(*db);
      } else {
        // Check if we are in warmup period
        bool in_warmup = (i < warmup_ops);
        wl->DoTransaction(*db, in_warmup, i+1);
      }
      ops++;
    }

    if (l1d_counters) {
      l1d_counters->Stop();
      l1d_counters->Print("thread" + std::to_string(thread_id));
    }

    measurement_latch->CountDown();

    if (cleanup_db) {
      db->Cleanup();
    }

    latch->CountDown();
    return ops;
  } catch (const utils::Exception &e) {
    std::cerr << "Caught exception: " << e.what() << std::endl;
    exit(1);
  }
}

} // ycsbc

#endif // YCSB_C_CLIENT_H_
