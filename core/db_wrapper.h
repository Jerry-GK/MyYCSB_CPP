//
//  db_wrapper.h
//  YCSB-cpp
//
//  Copyright (c) 2020 Youngjae Lee <ls4154.lee@gmail.com>.
//

#ifndef YCSB_C_DB_WRAPPER_H_
#define YCSB_C_DB_WRAPPER_H_

#include <string>
#include <vector>
#include <atomic>

#include "db.h"
#include "measurements.h"
#include "utils/timer.h"
#include "utils/utils.h"

namespace ycsbc {

class DBWrapper : public DB {
 public:
  DBWrapper(DB *db, Measurements *measurements, int warmup_ops = 0) 
    : db_(db), measurements_(measurements), warmup_ops_(warmup_ops), operation_count_(0) {}
  ~DBWrapper() {
    delete db_;
  }
  void Init() {
    db_->Init();
  }
  void Cleanup() {
    db_->Cleanup();
  }
  
 private:  
  void ReportOperation(Operation op, uint64_t latency) {
    int current_op = operation_count_.fetch_add(1, std::memory_order_relaxed);
    if (current_op < warmup_ops_) {
      measurements_->ReportWarmup(op);
    } else {
      measurements_->Report(op, latency);
    }
  }
  
 public:
  Status Read(const std::string &table, const std::string &key,
              const std::vector<std::string> *fields, std::vector<Field> &result) {
    timer_.Start();
    Status s = db_->Read(table, key, fields, result);
    uint64_t elapsed = timer_.End();
    
    if (s == kOK) {
      ReportOperation(READ, elapsed);
    } else {
      ReportOperation(READ_FAILED, elapsed);
    }
    return s;
  }
  Status Scan(const std::string &table, const std::string &key, int record_count,
              const std::vector<std::string> *fields, std::vector<std::vector<Field>> &result) {
    timer_.Start();
    Status s = db_->Scan(table, key, record_count, fields, result);
    uint64_t elapsed = timer_.End();
    
    if (s == kOK) {
      ReportOperation(SCAN, elapsed);
    } else {
      ReportOperation(SCAN_FAILED, elapsed);
    }
    return s;
  }
  Status Update(const std::string &table, const std::string &key, std::vector<Field> &values) {
    timer_.Start();
    Status s = db_->Update(table, key, values);
    uint64_t elapsed = timer_.End();
    
    if (s == kOK) {
      ReportOperation(UPDATE, elapsed);
    } else {
      ReportOperation(UPDATE_FAILED, elapsed);
    }
    return s;
  }
  Status Insert(const std::string &table, const std::string &key, std::vector<Field> &values) {
    timer_.Start();
    Status s = db_->Insert(table, key, values);
    uint64_t elapsed = timer_.End();
    
    if (s == kOK) {
      ReportOperation(INSERT, elapsed);
    } else {
      ReportOperation(INSERT_FAILED, elapsed);
    }
    return s;
  }
  Status Delete(const std::string &table, const std::string &key) {
    timer_.Start();
    Status s = db_->Delete(table, key);
    uint64_t elapsed = timer_.End();
    
    if (s == kOK) {
      ReportOperation(DELETE, elapsed);
    } else {
      ReportOperation(DELETE_FAILED, elapsed);
    }
    return s;
  }
 private:
  DB *db_;
  Measurements *measurements_;
  utils::Timer<uint64_t, std::nano> timer_;
  int warmup_ops_;
  std::atomic<int> operation_count_;
};

} // ycsbc

#endif // YCSB_C_DB_WRAPPER_H_
