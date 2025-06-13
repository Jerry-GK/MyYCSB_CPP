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

#include "db.h"
#include "core_workload.h"
#include "utils/countdown_latch.h"
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
                                  utils::Timer<double> *measurement_timer, const int warmup_ops, utils::RateLimiter *rlim) {

  try {
    if (init_db) {
      db->Init();
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
          measurement_timer->Start();
        }
      }

      if (is_loading) {
        wl->DoInsert(*db);
      } else {
        // Check if we are in warmup period
        bool in_warmup = (i < warmup_ops);
        wl->DoTransaction(*db, in_warmup);
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

} // ycsbc

#endif // YCSB_C_CLIENT_H_
