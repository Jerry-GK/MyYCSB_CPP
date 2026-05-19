//
//  leveldb_db.cc
//  YCSB-cpp
//
//  Copyright (c) 2020 Youngjae Lee <ls4154.lee@gmail.com>.
//  Modifications Copyright 2023 Chengye YU <yuchengye2013 AT outlook.com>.
//

#include "lsbm_db.h"
#include "core/core_workload.h"
#include "core/db_factory.h"
#include "utils/utils.h"

#include <leveldb/options.h>
#include <leveldb/write_batch.h>

#include <atomic>
#include <iostream>
#include <sstream>

namespace leveldb {
namespace config {
extern bool run_compaction;
extern bool buffered_merge;
}  // namespace config
namespace runtime {
extern double compaction_min_score;
extern int compaction_buffer_trim_interval;
extern int compaction_buffer_use_length[];
}  // namespace runtime
}  // namespace leveldb

namespace {
  const std::string PROP_NAME = "leveldb.dbname";
  const std::string PROP_NAME_DEFAULT = "";

  const std::string PROP_FORMAT = "leveldb.format";
  const std::string PROP_FORMAT_DEFAULT = "single";

  const std::string PROP_DESTROY = "leveldb.destroy";
  const std::string PROP_DESTROY_DEFAULT = "false";

  const std::string PROP_COMPRESSION = "leveldb.compression";
  const std::string PROP_COMPRESSION_DEFAULT = "no";

  const std::string PROP_WRITE_BUFFER_SIZE = "leveldb.write_buffer_size";
  const std::string PROP_WRITE_BUFFER_SIZE_DEFAULT = "0";

  const std::string PROP_MAX_FILE_SIZE = "leveldb.max_file_size";
  const std::string PROP_MAX_FILE_SIZE_DEFAULT = "0";

  const std::string PROP_MAX_OPEN_FILES = "leveldb.max_open_files";
  const std::string PROP_MAX_OPEN_FILES_DEFAULT = "0";

  const std::string PROP_CACHE_SIZE = "leveldb.cache_size";
  const std::string PROP_CACHE_SIZE_DEFAULT = "0";

  const std::string PROP_FILTER_BITS = "leveldb.filter_bits";
  const std::string PROP_FILTER_BITS_DEFAULT = "0";

  const std::string PROP_BLOCK_SIZE = "leveldb.block_size";
  const std::string PROP_BLOCK_SIZE_DEFAULT = "0";

  const std::string PROP_BLOCK_RESTART_INTERVAL = "leveldb.block_restart_interval";
  const std::string PROP_BLOCK_RESTART_INTERVAL_DEFAULT = "0";

  const std::string PROP_RUN_COMPACTION = "leveldb.run_compaction";
  const std::string PROP_RUN_COMPACTION_DEFAULT = "true";

  const std::string PROP_BUFFERED_MERGE = "leveldb.buffered_merge";
  const std::string PROP_BUFFERED_MERGE_DEFAULT = "true";

  const std::string PROP_COMPACTION_MIN_SCORE = "leveldb.compaction_min_score";
  const std::string PROP_COMPACTION_MIN_SCORE_DEFAULT = "1.0";

  const std::string PROP_COMPACTION_BUFFER_TRIM_INTERVAL =
      "leveldb.compaction_buffer_trim_interval";
  const std::string PROP_COMPACTION_BUFFER_TRIM_INTERVAL_DEFAULT = "30";

  const std::string PROP_COMPACTION_BUFFER_USE_LENGTH =
      "leveldb.compaction_buffer_use_length";
  const std::string PROP_COMPACTION_BUFFER_USE_LENGTH_DEFAULT = "";

  const std::string PROP_SCAN_DIAGNOSTICS = "leveldb.scan_diagnostics";
  const std::string PROP_SCAN_DIAGNOSTICS_DEFAULT = "false";
  const std::string PROP_SCAN_DIAGNOSTIC_SAMPLES =
      "leveldb.scan_diagnostic_samples";
  const std::string PROP_SCAN_DIAGNOSTIC_SAMPLES_DEFAULT = "0";

  bool IsTrue(const std::string &value) {
    return value == "true" || value == "1" || value == "yes";
  }

} // anonymous

namespace ycsbc {

namespace {
std::atomic<bool> g_scan_diagnostics{false};
std::atomic<uint64_t> g_scan_calls{0};
std::atomic<uint64_t> g_scan_rows{0};
std::atomic<uint64_t> g_scan_value_bytes{0};
std::atomic<uint64_t> g_scan_fields{0};
std::atomic<uint64_t> g_scan_short_results{0};
std::atomic<uint64_t> g_scan_first_key_mismatches{0};
std::atomic<uint64_t> g_scan_nonconsecutive_keys{0};
std::atomic<uint64_t> g_scan_diagnostic_samples_left{0};

bool ParseYcsbUserKey(const std::string& key, uint64_t* parsed) {
  static constexpr const char* kPrefix = "user";
  static constexpr size_t kPrefixLen = 4;
  if (key.size() <= kPrefixLen || key.compare(0, kPrefixLen, kPrefix) != 0) {
    return false;
  }
  uint64_t value = 0;
  for (size_t i = kPrefixLen; i < key.size(); ++i) {
    const char c = key[i];
    if (c < '0' || c > '9') {
      return false;
    }
    value = value * 10 + static_cast<uint64_t>(c - '0');
  }
  *parsed = value;
  return true;
}

void ApplyLsbmRuntimeOptions(const utils::Properties &props) {
  leveldb::config::run_compaction =
      IsTrue(props.GetProperty(PROP_RUN_COMPACTION,
                               PROP_RUN_COMPACTION_DEFAULT));
  leveldb::config::buffered_merge =
      IsTrue(props.GetProperty(PROP_BUFFERED_MERGE,
                               PROP_BUFFERED_MERGE_DEFAULT));
  leveldb::runtime::compaction_min_score =
      std::stod(props.GetProperty(PROP_COMPACTION_MIN_SCORE,
                                  PROP_COMPACTION_MIN_SCORE_DEFAULT));
  leveldb::runtime::compaction_buffer_trim_interval =
      std::stoi(props.GetProperty(PROP_COMPACTION_BUFFER_TRIM_INTERVAL,
                                  PROP_COMPACTION_BUFFER_TRIM_INTERVAL_DEFAULT));

  const std::string use_lengths =
      props.GetProperty(PROP_COMPACTION_BUFFER_USE_LENGTH,
                        PROP_COMPACTION_BUFFER_USE_LENGTH_DEFAULT);
  if (!use_lengths.empty()) {
    std::stringstream ss(use_lengths);
    std::string token;
    int level = 1;
    while (std::getline(ss, token, ',') && level < 4) {
      if (!token.empty()) {
        leveldb::runtime::compaction_buffer_use_length[level] =
            std::stoi(token);
      }
      ++level;
    }
  }
}
}  // namespace

leveldb::DB *LeveldbDB::db_ = nullptr;
int LeveldbDB::ref_cnt_ = 0;
std::mutex LeveldbDB::mu_;

void LeveldbDB::Init() {
  const std::lock_guard<std::mutex> lock(mu_);

  const utils::Properties &props = *props_;
  const std::string &format = props.GetProperty(PROP_FORMAT, PROP_FORMAT_DEFAULT);
  if (format == "single") {
    format_ = kSingleEntry;
    method_read_ = &LeveldbDB::ReadSingleEntry;
    method_scan_ = &LeveldbDB::ScanSingleEntry;
    method_update_ = &LeveldbDB::UpdateSingleEntry;
    method_insert_ = &LeveldbDB::InsertSingleEntry;
    method_delete_ = &LeveldbDB::DeleteSingleEntry;
  } else if (format == "row") {
    format_ = kRowMajor;
    method_read_ = &LeveldbDB::ReadCompKeyRM;
    method_scan_ = &LeveldbDB::ScanCompKeyRM;
    method_update_ = &LeveldbDB::InsertCompKey;
    method_insert_ = &LeveldbDB::InsertCompKey;
    method_delete_ = &LeveldbDB::DeleteCompKey;
  } else if (format == "column") {
    format_ = kColumnMajor;
    method_read_ = &LeveldbDB::ReadCompKeyCM;
    method_scan_ = &LeveldbDB::ScanCompKeyCM;
    method_update_ = &LeveldbDB::InsertCompKey;
    method_insert_ = &LeveldbDB::InsertCompKey;
    method_delete_ = &LeveldbDB::DeleteCompKey;
  } else {
    throw utils::Exception("unknown format");
  }
  fieldcount_ = std::stoi(props.GetProperty(CoreWorkload::FIELD_COUNT_PROPERTY,
                                            CoreWorkload::FIELD_COUNT_DEFAULT));
  field_prefix_ = props.GetProperty(CoreWorkload::FIELD_NAME_PREFIX,
                                    CoreWorkload::FIELD_NAME_PREFIX_DEFAULT);
  g_scan_diagnostics.store(
      IsTrue(props.GetProperty(PROP_SCAN_DIAGNOSTICS,
                               PROP_SCAN_DIAGNOSTICS_DEFAULT)),
      std::memory_order_relaxed);
  if (g_scan_diagnostics.load(std::memory_order_relaxed)) {
    g_scan_calls.store(0, std::memory_order_relaxed);
    g_scan_rows.store(0, std::memory_order_relaxed);
    g_scan_value_bytes.store(0, std::memory_order_relaxed);
    g_scan_fields.store(0, std::memory_order_relaxed);
    g_scan_short_results.store(0, std::memory_order_relaxed);
    g_scan_first_key_mismatches.store(0, std::memory_order_relaxed);
    g_scan_nonconsecutive_keys.store(0, std::memory_order_relaxed);
    g_scan_diagnostic_samples_left.store(
        static_cast<uint64_t>(std::stoull(
            props.GetProperty(PROP_SCAN_DIAGNOSTIC_SAMPLES,
                              PROP_SCAN_DIAGNOSTIC_SAMPLES_DEFAULT))),
        std::memory_order_relaxed);
  }

  ref_cnt_++;
  if (db_) {
    return;
  }

  const std::string &db_path = props.GetProperty(PROP_NAME, PROP_NAME_DEFAULT);
  if (db_path == "") {
    throw utils::Exception("LevelDB db path is missing");
  }

  leveldb::Options opt;
  opt.create_if_missing = true;
  GetOptions(props, &opt);
  ApplyLsbmRuntimeOptions(props);

  leveldb::Status s;

  if (props.GetProperty(PROP_DESTROY, PROP_DESTROY_DEFAULT) == "true") {
    s = leveldb::DestroyDB(db_path, opt);
    if (!s.ok()) {
      throw utils::Exception(std::string("LevelDB DestroyDB: ") + s.ToString());
    }
  }
  s = leveldb::DB::Open(opt, db_path, &db_);
  if (!s.ok()) {
    throw utils::Exception(std::string("LevelDB Open: ") + s.ToString());
  }
}

void LeveldbDB::Cleanup() {
  const std::lock_guard<std::mutex> lock(mu_);
  if (--ref_cnt_) {
    return;
  }
  if (g_scan_diagnostics.load(std::memory_order_relaxed)) {
    std::cerr << "[LSBM_SCAN_DIAGNOSTICS]"
              << " scan_calls=" << g_scan_calls.load(std::memory_order_relaxed)
              << " rows=" << g_scan_rows.load(std::memory_order_relaxed)
              << " value_bytes=" << g_scan_value_bytes.load(std::memory_order_relaxed)
              << " fields=" << g_scan_fields.load(std::memory_order_relaxed)
              << " short_results=" << g_scan_short_results.load(std::memory_order_relaxed)
              << " first_key_mismatches=" << g_scan_first_key_mismatches.load(std::memory_order_relaxed)
              << " nonconsecutive_keys=" << g_scan_nonconsecutive_keys.load(std::memory_order_relaxed)
              << std::endl;
  }
  delete db_;
  db_ = nullptr;
}

void LeveldbDB::GetOptions(const utils::Properties &props, leveldb::Options *opt) {
  size_t writer_buffer_size = std::stol(props.GetProperty(PROP_WRITE_BUFFER_SIZE,
                                                          PROP_WRITE_BUFFER_SIZE_DEFAULT));
  if (writer_buffer_size > 0) {
    opt->write_buffer_size = writer_buffer_size;
  }
  size_t max_file_size = std::stol(props.GetProperty(PROP_MAX_FILE_SIZE,
                                                     PROP_MAX_FILE_SIZE_DEFAULT));
  if (max_file_size > 0) {
    // opt->max_file_size = max_file_size;
  }
  size_t cache_size = std::stol(props.GetProperty(PROP_CACHE_SIZE,
                                                  PROP_CACHE_SIZE_DEFAULT));
  if (cache_size > 0) {
    opt->block_cache = leveldb::NewLRUCache(cache_size);
  }
  int max_open_files = std::stoi(props.GetProperty(PROP_MAX_OPEN_FILES,
                                                   PROP_MAX_OPEN_FILES_DEFAULT));
  if (max_open_files > 0) {
    opt->max_open_files = max_open_files;
  }
  std::string compression = props.GetProperty(PROP_COMPRESSION,
                                              PROP_COMPRESSION_DEFAULT);
  if (compression == "snappy") {
    opt->compression = leveldb::kSnappyCompression;
  } else {
    opt->compression = leveldb::kNoCompression;
  }
  int filter_bits = std::stoi(props.GetProperty(PROP_FILTER_BITS,
                                                PROP_FILTER_BITS_DEFAULT));
  if (filter_bits > 0) {
    opt->filter_policy = leveldb::NewBloomFilterPolicy(filter_bits);
  }
  int block_size = std::stoi(props.GetProperty(PROP_BLOCK_SIZE,
                                               PROP_BLOCK_SIZE_DEFAULT)); 
  if (block_size > 0) {
    opt->block_size = block_size;
  }
  int block_restart_interval = std::stoi(props.GetProperty(PROP_BLOCK_RESTART_INTERVAL,
                                                PROP_BLOCK_RESTART_INTERVAL_DEFAULT));
  if (block_restart_interval > 0) {
    opt->block_restart_interval = block_restart_interval;
  }
}

void LeveldbDB::SerializeRow(const std::vector<Field> &values, std::string *data) {
  for (const Field &field : values) {
    uint32_t len = field.name.size();
    data->append(reinterpret_cast<char *>(&len), sizeof(uint32_t));
    data->append(field.name.data(), field.name.size());
    len = field.value.size();
    data->append(reinterpret_cast<char *>(&len), sizeof(uint32_t));
    data->append(field.value.data(), field.value.size());
  }
}

void LeveldbDB::DeserializeRowFilter(std::vector<Field> *values, const std::string &data,
                                     const std::vector<std::string> &fields) {
  const char *p = data.data();
  const char *lim = p + data.size();

  std::vector<std::string>::const_iterator filter_iter = fields.begin();
  while (p != lim && filter_iter != fields.end()) {
    assert(p < lim);
    uint32_t len = *reinterpret_cast<const uint32_t *>(p);
    p += sizeof(uint32_t);
    std::string field(p, static_cast<const size_t>(len));
    p += len;
    len = *reinterpret_cast<const uint32_t *>(p);
    p += sizeof(uint32_t);
    std::string value(p, static_cast<const size_t>(len));
    p += len;
    if (*filter_iter == field) {
      values->push_back({field, value});
      filter_iter++;
    }
  }
  assert(values->size() == fields.size());
}

void LeveldbDB::DeserializeRow(std::vector<Field> *values, const std::string &data) {
  const char *p = data.data();
  const char *lim = p + data.size();
  while (p != lim) {
    assert(p < lim);
    uint32_t len = *reinterpret_cast<const uint32_t *>(p);
    p += sizeof(uint32_t);
    std::string field(p, static_cast<const size_t>(len));
    p += len;
    len = *reinterpret_cast<const uint32_t *>(p);
    p += sizeof(uint32_t);
    std::string value(p, static_cast<const size_t>(len));
    p += len;
    values->push_back({field, value});
  }
  assert(values->size() == fieldcount_);
}

std::string LeveldbDB::BuildCompKey(const std::string &key, const std::string &field_name) {
  switch (format_) {
    case kRowMajor:
      return key + ":" + field_name;
      break;
    case kColumnMajor:
      return field_name + ":" + key;
      break;
    default:
      throw utils::Exception("wrong format");
  }
}

std::string LeveldbDB::KeyFromCompKey(const std::string &comp_key) {
  size_t idx = comp_key.find(":");
  assert(idx != std::string::npos);
  return comp_key.substr(0, idx);
}

std::string LeveldbDB::FieldFromCompKey(const std::string &comp_key) {
  size_t idx = comp_key.find(":");
  assert(idx != std::string::npos);
  return comp_key.substr(idx + 1);
}

DB::Status LeveldbDB::ReadSingleEntry(const std::string &table, const std::string &key,
                                      const std::vector<std::string> *fields,
                                      std::vector<Field> &result) {
  std::string data;
  leveldb::Status s = db_->Get(leveldb::ReadOptions(), key, &data);
  if (s.IsNotFound()) {
    return kNotFound;
  } else if (!s.ok()) {
    throw utils::Exception(std::string("LevelDB Get: ") + s.ToString());
  }
  if (fields != nullptr) {
    DeserializeRowFilter(&result, data, *fields);
  } else {
    DeserializeRow(&result, data);
  }
  return kOK;
}

DB::Status LeveldbDB::ScanSingleEntry(const std::string &table, const std::string &key, int len,
                                      const std::vector<std::string> *fields,
                                      std::vector<std::vector<Field>> &result) {
  leveldb::Iterator *db_iter = db_->NewIterator(leveldb::ReadOptions());
  db_iter->Seek(key);
  uint64_t returned_rows = 0;
  uint64_t returned_bytes = 0;
  uint64_t returned_fields = 0;
  uint64_t requested_key_id = 0;
  uint64_t previous_key_id = 0;
  bool can_check_keys = g_scan_diagnostics.load(std::memory_order_relaxed) &&
                        ParseYcsbUserKey(key, &requested_key_id);
  bool first_row = true;
  bool first_key_mismatch = false;
  bool nonconsecutive_key = false;
  std::string mismatch_detail;
  for (int i = 0; db_iter->Valid() && i < len; i++) {
    if (can_check_keys) {
      uint64_t current_key_id = 0;
      const std::string current_key = db_iter->key().ToString();
      if (!ParseYcsbUserKey(current_key, &current_key_id)) {
        nonconsecutive_key = true;
        if (mismatch_detail.empty()) {
          mismatch_detail = "unparseable_key=" + current_key;
        }
      } else if (first_row) {
        if (current_key_id != requested_key_id) {
          first_key_mismatch = true;
          if (mismatch_detail.empty()) {
            mismatch_detail = "first requested=" + std::to_string(requested_key_id) +
                              " actual=" + std::to_string(current_key_id);
          }
        }
        previous_key_id = current_key_id;
      } else {
        if (current_key_id != previous_key_id + 1) {
          nonconsecutive_key = true;
          if (mismatch_detail.empty()) {
            mismatch_detail = "gap previous=" + std::to_string(previous_key_id) +
                              " actual=" + std::to_string(current_key_id) +
                              " requested=" + std::to_string(requested_key_id) +
                              " row=" + std::to_string(i);
          }
        }
        previous_key_id = current_key_id;
      }
      first_row = false;
    }
    std::string data = db_iter->value().ToString();
    returned_bytes += data.size();
    result.push_back(std::vector<Field>());
    std::vector<Field> &values = result.back();
    if (fields != nullptr) {
      DeserializeRowFilter(&values, data, *fields);
    } else {
      DeserializeRow(&values, data);
    }
    returned_fields += values.size();
    ++returned_rows;
    db_iter->Next();
  }
  if (g_scan_diagnostics.load(std::memory_order_relaxed)) {
    g_scan_calls.fetch_add(1, std::memory_order_relaxed);
    g_scan_rows.fetch_add(returned_rows, std::memory_order_relaxed);
    g_scan_value_bytes.fetch_add(returned_bytes, std::memory_order_relaxed);
    g_scan_fields.fetch_add(returned_fields, std::memory_order_relaxed);
    if (returned_rows < static_cast<uint64_t>(len)) {
      g_scan_short_results.fetch_add(1, std::memory_order_relaxed);
    }
    if (first_key_mismatch) {
      g_scan_first_key_mismatches.fetch_add(1, std::memory_order_relaxed);
    }
    if (nonconsecutive_key) {
      g_scan_nonconsecutive_keys.fetch_add(1, std::memory_order_relaxed);
    }
    if (!mismatch_detail.empty()) {
      uint64_t samples_left =
          g_scan_diagnostic_samples_left.load(std::memory_order_relaxed);
      while (samples_left > 0) {
        if (g_scan_diagnostic_samples_left.compare_exchange_weak(
                samples_left, samples_left - 1, std::memory_order_relaxed)) {
          std::cerr << "[LSBM_SCAN_DIAGNOSTIC_SAMPLE] "
                    << mismatch_detail << " len=" << len
                    << " rows=" << returned_rows << std::endl;
          break;
        }
      }
    }
  }
  delete db_iter;
  return kOK;
}

DB::Status LeveldbDB::UpdateSingleEntry(const std::string &table, const std::string &key,
                                        std::vector<Field> &values) {
  // Put directly without GET if write all fields
  if (values.size() == static_cast<size_t>(fieldcount_)) {
    return UpdateAllFieldsSingle(table, key, values);
  }

  std::string data;
  leveldb::Status s = db_->Get(leveldb::ReadOptions(), key, &data);
  if (s.IsNotFound()) {
    return kNotFound;
  } else if (!s.ok()) {
    throw utils::Exception(std::string("LevelDB Get: ") + s.ToString());
  }
  std::vector<Field> current_values;
  DeserializeRow(&current_values, data);
  for (Field &new_field : values) {
    bool found MAYBE_UNUSED = false;
    for (Field &cur_field : current_values) {
      if (cur_field.name == new_field.name) {
        found = true;
        cur_field.value = new_field.value;
        break;
      }
    }
    assert(found);
  }
  leveldb::WriteOptions wopt;

  data.clear();
  SerializeRow(current_values, &data);
  s = db_->Put(wopt, key, data);
  if (!s.ok()) {
    throw utils::Exception(std::string("LevelDB Put: ") + s.ToString());
  }
  return kOK;
}

DB::Status LeveldbDB::UpdateAllFieldsSingle(const std::string &table, const std::string &key,
                                        std::vector<Field> &values) {
  if (values.size() != static_cast<size_t>(fieldcount_)) {
    assert(false);
    return kError;
  }

  std::string data;
  leveldb::WriteOptions wopt;
  SerializeRow(values, &data);
  leveldb::Status s = db_->Put(wopt, key, data);
  if (!s.ok()) {
    throw utils::Exception(std::string("LevelDB Put: ") + s.ToString());
  }
  return kOK;
}

DB::Status LeveldbDB::InsertSingleEntry(const std::string &table, const std::string &key,
                                        std::vector<Field> &values) {
  std::string data;
  SerializeRow(values, &data);
  leveldb::WriteOptions wopt;
  leveldb::Status s = db_->Put(wopt, key, data);
  if (!s.ok()) {
    throw utils::Exception(std::string("LevelDB Put: ") + s.ToString());
  }
  return kOK;
}

DB::Status LeveldbDB::DeleteSingleEntry(const std::string &table, const std::string &key) {
  leveldb::WriteOptions wopt;
  leveldb::Status s = db_->Delete(wopt, key);
  if (!s.ok()) {
    throw utils::Exception(std::string("LevelDB Delete: ") + s.ToString());
  }
  return kOK;
}

DB::Status LeveldbDB::ReadCompKeyRM(const std::string &table, const std::string &key,
                                    const std::vector<std::string> *fields,
                                    std::vector<Field> &result) {
  leveldb::Iterator *db_iter = db_->NewIterator(leveldb::ReadOptions());
  db_iter->Seek(key);
  if (!db_iter->Valid() || KeyFromCompKey(db_iter->key().ToString()) != key) {
    return kNotFound;
  }
  if (fields != nullptr) {
    std::vector<std::string>::const_iterator filter_iter = fields->begin();
    for (int i = 0; i < fieldcount_ && filter_iter != fields->end() && db_iter->Valid(); i++) {
      std::string comp_key = db_iter->key().ToString();
      std::string cur_val = db_iter->value().ToString();
      std::string cur_key = KeyFromCompKey(comp_key);
      std::string cur_field = FieldFromCompKey(comp_key);
      assert(cur_key == key);
      assert(cur_field == field_prefix_ + std::to_string(i));

      if (cur_field == *filter_iter) {
        result.push_back({cur_field, cur_val});
        filter_iter++;
      }
      db_iter->Next();
    }
    assert(result.size() == fields->size());
  } else {
    for (int i = 0; i < fieldcount_ && db_iter->Valid(); i++) {
      std::string comp_key = db_iter->key().ToString();
      std::string cur_val = db_iter->value().ToString();
      std::string cur_key = KeyFromCompKey(comp_key);
      std::string cur_field = FieldFromCompKey(comp_key);
      assert(cur_key == key);
      assert(cur_field == field_prefix_ + std::to_string(i));

      result.push_back({cur_field, cur_val});
      db_iter->Next();
    }
    assert(result.size() == fieldcount_);
  }
  delete db_iter;
  return kOK;
}

DB::Status LeveldbDB::ScanCompKeyRM(const std::string &table, const std::string &key, int len,
                                    const std::vector<std::string> *fields,
                                    std::vector<std::vector<Field>> &result) {
  leveldb::Iterator *db_iter = db_->NewIterator(leveldb::ReadOptions());
  db_iter->Seek(key);
  assert(db_iter->Valid() && KeyFromCompKey(db_iter->key().ToString()) == key);
  for (int i = 0; i < len && db_iter->Valid(); i++) {
    result.push_back(std::vector<Field>());
    std::vector<Field> &values = result.back();
    if (fields != nullptr) {
      std::vector<std::string>::const_iterator filter_iter = fields->begin();
      for (int j = 0; j < fieldcount_ && filter_iter != fields->end() && db_iter->Valid(); j++) {
        std::string comp_key = db_iter->key().ToString();
        std::string cur_val = db_iter->value().ToString();
        std::string cur_key = KeyFromCompKey(comp_key);
        std::string cur_field = FieldFromCompKey(comp_key);
        assert(cur_field == field_prefix_ + std::to_string(j));

        if (cur_field == *filter_iter) {
          values.push_back({cur_field, cur_val});
          filter_iter++;
        }
        db_iter->Next();
      }
      assert(values.size() == fields->size());
    } else {
      for (int j = 0; j < fieldcount_ && db_iter->Valid(); j++) {
        std::string comp_key = db_iter->key().ToString();
        std::string cur_val = db_iter->value().ToString();
        std::string cur_key = KeyFromCompKey(comp_key);
        std::string cur_field = FieldFromCompKey(comp_key);
        assert(cur_field == field_prefix_ + std::to_string(j));

        values.push_back({cur_field, cur_val});
        db_iter->Next();
      }
      assert(values.size() == fieldcount_);
    }
  }
  delete db_iter;
  return kOK;
}

DB::Status LeveldbDB::ReadCompKeyCM(const std::string &table, const std::string &key,
                                    const std::vector<std::string> *fields,
                                    std::vector<Field> &result) {
  return kNotImplemented;
}

DB::Status LeveldbDB::ScanCompKeyCM(const std::string &table, const std::string &key, int len,
                                    const std::vector<std::string> *fields,
                                    std::vector<std::vector<Field>> &result) {
  return kNotImplemented;
}

DB::Status LeveldbDB::InsertCompKey(const std::string &table, const std::string &key,
                                    std::vector<Field> &values) {
  leveldb::WriteOptions wopt;
  leveldb::WriteBatch batch;

  std::string comp_key;
  for (Field &field : values) {
    comp_key = BuildCompKey(key, field.name);
    batch.Put(comp_key, field.value);
  }

  leveldb::Status s = db_->Write(wopt, &batch);
  if (!s.ok()) {
    throw utils::Exception(std::string("LevelDB Write: ") + s.ToString());
  }
  return kOK;
}

DB::Status LeveldbDB::DeleteCompKey(const std::string &table, const std::string &key) {
  leveldb::WriteOptions wopt;
  leveldb::WriteBatch batch;

  std::string comp_key;
  for (int i = 0; i < fieldcount_; i++) {
    comp_key = BuildCompKey(key, field_prefix_ + std::to_string(i));
    batch.Delete(comp_key);
  }

  leveldb::Status s = db_->Write(wopt, &batch);
  if (!s.ok()) {
    throw utils::Exception(std::string("LevelDB Write: ") + s.ToString());
  }
  return kOK;
}

DB *NewLeveldbDB() {
  return new LeveldbDB;
}

const bool registered = DBFactory::RegisterDB("lsbm", NewLeveldbDB);

} // ycsbc
