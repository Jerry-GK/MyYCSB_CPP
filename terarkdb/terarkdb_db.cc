//
//  terarkdb_db.cc
//  YCSB-cpp
//
//  Copyright (c) 2020 Youngjae Lee <ls4154.lee@gmail.com>.
//  Modifications Copyright 2023 Chengye YU <yuchengye2013 AT outlook.com>.
//

#include "terarkdb_db.h"

#include "core/core_workload.h"
#include "core/db_factory.h"
#include "utils/utils.h"

#include <rocksdb/cache.h>
#include <rocksdb/filter_policy.h>
#include <rocksdb/merge_operator.h>
#include <rocksdb/status.h>
#include <rocksdb/utilities/options_util.h>
#include <rocksdb/write_batch.h>
#include <rocksdb/table.h>

namespace {
  const std::string PROP_NAME = "terarkdb.dbname";
  const std::string PROP_NAME_DEFAULT = "";

  const std::string PROP_FORMAT = "terarkdb.format";
  const std::string PROP_FORMAT_DEFAULT = "single";

  const std::string PROP_MERGEUPDATE = "terarkdb.mergeupdate";
  const std::string PROP_MERGEUPDATE_DEFAULT = "false";

  const std::string PROP_DESTROY = "terarkdb.destroy";
  const std::string PROP_DESTROY_DEFAULT = "false";

  const std::string PROP_CREATE_IF_MISSING = "terarkdb.create_if_missing";
  const std::string PROP_CREATE_IF_MISSING_DEFAULT = "false";

  const std::string PROP_DISABLE_AUTO_COMPACTIONS = "terarkdb.disable_auto_compactions";
  const std::string PROP_DISABLE_AUTO_COMPACTIONS_DEFAULT = "false";

  const std::string PROP_DISABLE_WAL = "terarkdb.disableWAL";
  const std::string PROP_DISABLE_WAL_DEFAULT = "true";

  const std::string PROP_BLOB_SIZE = "terarkdb.blob_size";
  const std::string PROP_BLOB_SIZE_DEFAULT = "512";

  const std::string PROP_TARGET_BLOB_FILE_SIZE = "terarkdb.target_blob_file_size";
  const std::string PROP_TARGET_BLOB_FILE_SIZE_DEFAULT = "67108864";

  const std::string PROP_BLOB_GC_RATIO = "terarkdb.blob_gc_ratio";
  const std::string PROP_BLOB_GC_RATIO_DEFAULT = "0.25";

  const std::string PROP_COMPRESSION = "terarkdb.compression";
  const std::string PROP_COMPRESSION_DEFAULT = "no";

  const std::string PROP_MAX_BG_JOBS = "terarkdb.max_background_jobs";
  const std::string PROP_MAX_BG_JOBS_DEFAULT = "0";

  const std::string PROP_TARGET_FILE_SIZE_BASE = "terarkdb.target_file_size_base";
  const std::string PROP_TARGET_FILE_SIZE_BASE_DEFAULT = "0";

  const std::string PROP_TARGET_FILE_SIZE_MULT = "terarkdb.target_file_size_multiplier";
  const std::string PROP_TARGET_FILE_SIZE_MULT_DEFAULT = "0";

  const std::string PROP_MAX_BYTES_FOR_LEVEL_BASE = "terarkdb.max_bytes_for_level_base";
  const std::string PROP_MAX_BYTES_FOR_LEVEL_BASE_DEFAULT = "0";

  const std::string PROP_WRITE_BUFFER_SIZE = "terarkdb.write_buffer_size";
  const std::string PROP_WRITE_BUFFER_SIZE_DEFAULT = "0";

  const std::string PROP_MAX_WRITE_BUFFER = "terarkdb.max_write_buffer_number";
  const std::string PROP_MAX_WRITE_BUFFER_DEFAULT = "0";

  const std::string PROP_COMPACTION_PRI = "terarkdb.compaction_pri";
  const std::string PROP_COMPACTION_PRI_DEFAULT = "-1";

  const std::string PROP_MAX_OPEN_FILES = "terarkdb.max_open_files";
  const std::string PROP_MAX_OPEN_FILES_DEFAULT = "-1";

  const std::string PROP_L0_COMPACTION_TRIGGER = "terarkdb.level0_file_num_compaction_trigger";
  const std::string PROP_L0_COMPACTION_TRIGGER_DEFAULT = "0";

  const std::string PROP_L0_SLOWDOWN_TRIGGER = "terarkdb.level0_slowdown_writes_trigger";
  const std::string PROP_L0_SLOWDOWN_TRIGGER_DEFAULT = "0";

  const std::string PROP_L0_STOP_TRIGGER = "terarkdb.level0_stop_writes_trigger";
  const std::string PROP_L0_STOP_TRIGGER_DEFAULT = "0";

  const std::string PROP_USE_DIRECT_WRITE = "terarkdb.use_direct_io_for_flush_compaction";
  const std::string PROP_USE_DIRECT_WRITE_DEFAULT = "false";

  const std::string PROP_USE_DIRECT_READ = "terarkdb.use_direct_reads";
  const std::string PROP_USE_DIRECT_READ_DEFAULT = "false";

  const std::string PROP_USE_MMAP_WRITE = "terarkdb.allow_mmap_writes";
  const std::string PROP_USE_MMAP_WRITE_DEFAULT = "false";

  const std::string PROP_USE_MMAP_READ = "terarkdb.allow_mmap_reads";
  const std::string PROP_USE_MMAP_READ_DEFAULT = "false";

  const std::string PROP_CACHE_SIZE = "terarkdb.cache_size";
  const std::string PROP_CACHE_SIZE_DEFAULT = "0";

  const std::string PROP_COMPRESSED_CACHE_SIZE = "terarkdb.compressed_cache_size";
  const std::string PROP_COMPRESSED_CACHE_SIZE_DEFAULT = "0";

  const std::string PROP_BLOOM_BITS = "terarkdb.bloom_bits";
  const std::string PROP_BLOOM_BITS_DEFAULT = "0";

  const std::string PROP_INCREASE_PARALLELISM = "terarkdb.increase_parallelism";
  const std::string PROP_INCREASE_PARALLELISM_DEFAULT = "false";

  const std::string PROP_OPTIMIZE_LEVELCOMP = "terarkdb.optimize_level_style_compaction";
  const std::string PROP_OPTIMIZE_LEVELCOMP_DEFAULT = "false";

  const std::string PROP_OPTIONS_FILE = "terarkdb.optionsfile";
  const std::string PROP_OPTIONS_FILE_DEFAULT = "";

  const std::string PROP_ENV_URI = "terarkdb.env_uri";
  const std::string PROP_ENV_URI_DEFAULT = "";

  const std::string PROP_FS_URI = "terarkdb.fs_uri";
  const std::string PROP_FS_URI_DEFAULT = "";

  const std::string PROP_DESERIALIZE_ON_READ = "terarkdb.deserialize_on_read";
  const std::string PROP_DESERIALIZE_ON_READ_DEFAULT = "false";

  static std::shared_ptr<terarkdb::Env> env_guard;
  static std::shared_ptr<terarkdb::Cache> block_cache;
#if ROCKSDB_MAJOR < 8
  static std::shared_ptr<terarkdb::Cache> block_cache_compressed;
#endif
} // anonymous

namespace ycsbc {

std::vector<terarkdb::ColumnFamilyHandle *> RocksdbDB::cf_handles_;
terarkdb::DB *RocksdbDB::db_ = nullptr;
int RocksdbDB::ref_cnt_ = 0;
std::mutex RocksdbDB::mu_;

void RocksdbDB::Init() {
// merge operator disabled by default due to link error
#ifdef USE_MERGEUPDATE
  class YCSBUpdateMerge : public terarkdb::AssociativeMergeOperator {
   public:
    virtual bool Merge(const terarkdb::Slice &key, const terarkdb::Slice *existing_value,
                       const terarkdb::Slice &value, std::string *new_value,
                       terarkdb::Logger *logger) const override {
      assert(existing_value);

      std::vector<Field> values;
      const char *p = existing_value->data();
      const char *lim = p + existing_value->size();
      DeserializeRow(values, p, lim);

      std::vector<Field> new_values;
      p = value.data();
      lim = p + value.size();
      DeserializeRow(new_values, p, lim);

      for (Field &new_field : new_values) {
        bool found = false;
        for (Field &field : values) {
          if (field.name == new_field.name) {
            found = true;
            field.value = new_field.value;
            break;
          }
        }
        if (!found) {
          values.push_back(new_field);
        }
      }

      SerializeRow(values, *new_value);
      return true;
    }

    virtual const char *Name() const override {
      return "YCSBUpdateMerge";
    }
  };
#endif
  const std::lock_guard<std::mutex> lock(mu_);

  const utils::Properties &props = *props_;
  const std::string format = props.GetProperty(PROP_FORMAT, PROP_FORMAT_DEFAULT);
  if (format == "single") {
    format_ = kSingleRow;
    method_read_ = &RocksdbDB::ReadSingle;
    method_scan_ = &RocksdbDB::ScanSingle;
    method_update_ = &RocksdbDB::UpdateSingle;
    method_insert_ = &RocksdbDB::InsertSingle;
    method_delete_ = &RocksdbDB::DeleteSingle;
#ifdef USE_MERGEUPDATE
    if (props.GetProperty(PROP_MERGEUPDATE, PROP_MERGEUPDATE_DEFAULT) == "true") {
      method_update_ = &RocksdbDB::MergeSingle;
    }
#endif
  } else {
    throw utils::Exception("unknown format");
  }
  fieldcount_ = std::stoi(props.GetProperty(CoreWorkload::FIELD_COUNT_PROPERTY,
                                            CoreWorkload::FIELD_COUNT_DEFAULT));
  disable_wal_ = (props.GetProperty(PROP_DISABLE_WAL, PROP_DISABLE_WAL_DEFAULT) == "true");
  deserialize_on_read_ = (props.GetProperty(PROP_DESERIALIZE_ON_READ, PROP_DESERIALIZE_ON_READ_DEFAULT) == "true");

  ref_cnt_++;
  if (db_) {
    return;
  }

  const std::string &db_path = props.GetProperty(PROP_NAME, PROP_NAME_DEFAULT);
  if (db_path == "") {
    throw utils::Exception("RocksDB db path is missing");
  }

  terarkdb::Options opt;
  opt.create_if_missing = true;
  std::vector<terarkdb::ColumnFamilyDescriptor> cf_descs;
  GetOptions(props, &opt, &cf_descs);
#ifdef USE_MERGEUPDATE
  opt.merge_operator.reset(new YCSBUpdateMerge);
#endif

  terarkdb::Status s;
  if (props.GetProperty(PROP_DESTROY, PROP_DESTROY_DEFAULT) == "true") {
    s = terarkdb::DestroyDB(db_path, opt);
    if (!s.ok()) {
      throw utils::Exception(std::string("RocksDB DestroyDB: ") + s.ToString());
    }
  }
  if (cf_descs.empty()) {
    s = terarkdb::DB::Open(opt, db_path, &db_);
  } else {
    s = terarkdb::DB::Open(opt, db_path, cf_descs, &cf_handles_, &db_);
  }
  if (!s.ok()) {
    throw utils::Exception(std::string("RocksDB Open: ") + s.ToString());
  }
}

void RocksdbDB::Cleanup() { 
  const std::lock_guard<std::mutex> lock(mu_);
  if (--ref_cnt_) {
    return;
  }
  for (size_t i = 0; i < cf_handles_.size(); i++) {
    if (cf_handles_[i] != nullptr) {
      db_->DestroyColumnFamilyHandle(cf_handles_[i]);
      cf_handles_[i] = nullptr;
    }
  }
  delete db_;
}

void RocksdbDB::GetOptions(const utils::Properties &props, terarkdb::Options *opt,
                           std::vector<terarkdb::ColumnFamilyDescriptor> *cf_descs) {
  // terarkdb does NOT support terarkdb::Env::CreateFromUri
  // std::string env_uri = props.GetProperty(PROP_ENV_URI, PROP_ENV_URI_DEFAULT);
  // std::string fs_uri = props.GetProperty(PROP_FS_URI, PROP_FS_URI_DEFAULT);
  // terarkdb::Env* env =  terarkdb::Env::Default();;
  // if (!env_uri.empty() || !fs_uri.empty()) {
  //   terarkdb::Status s = terarkdb::Env::CreateFromUri(terarkdb::ConfigOptions(),
  //                                                   env_uri, fs_uri, &env, &env_guard);
  //   if (!s.ok()) {
  //     throw utils::Exception(std::string("RocksDB CreateFromUri: ") + s.ToString());
  //   }
  //   opt->env = env;
  // }

  const std::string options_file = props.GetProperty(PROP_OPTIONS_FILE, PROP_OPTIONS_FILE_DEFAULT);
  if (options_file != "") {
    // terarkdb::ConfigOptions config_options;
    // config_options.ignore_unknown_options = false;
    // config_options.input_strings_escaped = true;
    // // config_options.env = env;
    // terarkdb::Status s = terarkdb::LoadOptionsFromFile(config_options, options_file, opt, cf_descs);
    // if (!s.ok()) {
    //   throw utils::Exception(std::string("RocksDB LoadOptionsFromFile: ") + s.ToString());
    // }

    // Terarkdb does not support LoadOptionsFromFile
    throw utils::Exception(std::string("RocksDB LoadOptionsFromFile: "));
  } else {
    const std::string compression_type = props.GetProperty(PROP_COMPRESSION,
                                                           PROP_COMPRESSION_DEFAULT);
    if (compression_type == "no") {
      opt->compression = terarkdb::kNoCompression;
    } else if (compression_type == "snappy") {
      opt->compression = terarkdb::kSnappyCompression;
    } else if (compression_type == "zlib") {
      opt->compression = terarkdb::kZlibCompression;
    } else if (compression_type == "bzip2") {
      opt->compression = terarkdb::kBZip2Compression;
    } else if (compression_type == "lz4") {
      opt->compression = terarkdb::kLZ4Compression;
    } else if (compression_type == "lz4hc") {
      opt->compression = terarkdb::kLZ4HCCompression;
    } else if (compression_type == "xpress") {
      opt->compression = terarkdb::kXpressCompression;
    } else if (compression_type == "zstd") {
      opt->compression = terarkdb::kZSTD;
    } else {
      throw utils::Exception("Unknown compression type");
    }

    // Basic options
    if (props.GetProperty(PROP_CREATE_IF_MISSING, PROP_CREATE_IF_MISSING_DEFAULT) == "true") {
      opt->create_if_missing = true;
    }
    if (props.GetProperty(PROP_DISABLE_AUTO_COMPACTIONS, PROP_DISABLE_AUTO_COMPACTIONS_DEFAULT) == "true") {
      opt->disable_auto_compactions = true;
    }

    // Blob storage options
    int blob_size = std::stoi(props.GetProperty(PROP_BLOB_SIZE, PROP_BLOB_SIZE_DEFAULT));
    opt->blob_size = blob_size;
    
    int target_blob_file_size = std::stoi(props.GetProperty(PROP_TARGET_BLOB_FILE_SIZE, PROP_TARGET_BLOB_FILE_SIZE_DEFAULT));
    opt->target_blob_file_size = target_blob_file_size;
    
    double blob_gc_ratio = std::stod(props.GetProperty(PROP_BLOB_GC_RATIO, PROP_BLOB_GC_RATIO_DEFAULT));
    opt->blob_gc_ratio = blob_gc_ratio;

    int val = std::stoi(props.GetProperty(PROP_MAX_BG_JOBS, PROP_MAX_BG_JOBS_DEFAULT));
    if (val != 0) {
      opt->max_background_jobs = val;
    }
    val = std::stoi(props.GetProperty(PROP_TARGET_FILE_SIZE_BASE, PROP_TARGET_FILE_SIZE_BASE_DEFAULT));
    if (val != 0) {
      opt->target_file_size_base = val;
    }
    val = std::stoi(props.GetProperty(PROP_TARGET_FILE_SIZE_MULT, PROP_TARGET_FILE_SIZE_MULT_DEFAULT));
    if (val != 0) {
      opt->target_file_size_multiplier = val;
    }
    val = std::stoi(props.GetProperty(PROP_MAX_BYTES_FOR_LEVEL_BASE, PROP_MAX_BYTES_FOR_LEVEL_BASE_DEFAULT));
    if (val != 0) {
      opt->max_bytes_for_level_base = val;
    }
    val = std::stoi(props.GetProperty(PROP_WRITE_BUFFER_SIZE, PROP_WRITE_BUFFER_SIZE_DEFAULT));
    if (val != 0) {
      opt->write_buffer_size = val;
    }
    val = std::stoi(props.GetProperty(PROP_MAX_WRITE_BUFFER, PROP_MAX_WRITE_BUFFER_DEFAULT));
    if (val != 0) {
      opt->max_write_buffer_number = val;
    }
    val = std::stoi(props.GetProperty(PROP_COMPACTION_PRI, PROP_COMPACTION_PRI_DEFAULT));
    if (val != -1) {
      opt->compaction_pri = static_cast<terarkdb::CompactionPri>(val);
    }
    val = std::stoi(props.GetProperty(PROP_MAX_OPEN_FILES, PROP_MAX_OPEN_FILES_DEFAULT));
    if (val != 0) {
      opt->max_open_files = val;
    }

    val = std::stoi(props.GetProperty(PROP_L0_COMPACTION_TRIGGER, PROP_L0_COMPACTION_TRIGGER_DEFAULT));
    if (val != 0) {
      opt->level0_file_num_compaction_trigger = val;
    }
    val = std::stoi(props.GetProperty(PROP_L0_SLOWDOWN_TRIGGER, PROP_L0_SLOWDOWN_TRIGGER_DEFAULT));
    if (val != 0) {
      opt->level0_slowdown_writes_trigger = val;
    }
    val = std::stoi(props.GetProperty(PROP_L0_STOP_TRIGGER, PROP_L0_STOP_TRIGGER_DEFAULT));
    if (val != 0) {
      opt->level0_stop_writes_trigger = val;
    }

    if (props.GetProperty(PROP_USE_DIRECT_WRITE, PROP_USE_DIRECT_WRITE_DEFAULT) == "true") {
      opt->use_direct_io_for_flush_and_compaction = true;
    }
    if (props.GetProperty(PROP_USE_DIRECT_READ, PROP_USE_DIRECT_READ_DEFAULT) == "true") {
      opt->use_direct_reads = true;
    }
    if (props.GetProperty(PROP_USE_MMAP_WRITE, PROP_USE_MMAP_WRITE_DEFAULT) == "true") {
      opt->allow_mmap_writes = true;
    }
    if (props.GetProperty(PROP_USE_MMAP_READ, PROP_USE_MMAP_READ_DEFAULT) == "true") {
      opt->allow_mmap_reads = true;
    }

    terarkdb::BlockBasedTableOptions table_options;
    size_t cache_size = std::stoul(props.GetProperty(PROP_CACHE_SIZE, PROP_CACHE_SIZE_DEFAULT));
    if (cache_size > 0) {
      block_cache = terarkdb::NewLRUCache(cache_size);
      table_options.block_cache = block_cache;
    } else {
      table_options.no_block_cache = true;
    }
#if ROCKSDB_MAJOR < 8
    size_t compressed_cache_size = std::stoul(props.GetProperty(PROP_COMPRESSED_CACHE_SIZE,
                                                                PROP_COMPRESSED_CACHE_SIZE_DEFAULT));
    if (compressed_cache_size > 0) {
      block_cache_compressed = terarkdb::NewLRUCache(compressed_cache_size);
      table_options.block_cache_compressed = block_cache_compressed;
    }
#endif
    int bloom_bits = std::stoul(props.GetProperty(PROP_BLOOM_BITS, PROP_BLOOM_BITS_DEFAULT));
    if (bloom_bits > 0) {
      table_options.filter_policy.reset(terarkdb::NewBloomFilterPolicy(bloom_bits));
    }
    opt->table_factory.reset(terarkdb::NewBlockBasedTableFactory(table_options));

    if (props.GetProperty(PROP_INCREASE_PARALLELISM, PROP_INCREASE_PARALLELISM_DEFAULT) == "true") {
      opt->IncreaseParallelism();
    }
    if (props.GetProperty(PROP_OPTIMIZE_LEVELCOMP, PROP_OPTIMIZE_LEVELCOMP_DEFAULT) == "true") {
      opt->OptimizeLevelStyleCompaction();
    }
  }
}

void RocksdbDB::SerializeRow(const std::vector<Field> &values, std::string &data) {
  for (const Field &field : values) {
    uint32_t len = field.name.size();
    data.append(reinterpret_cast<char *>(&len), sizeof(uint32_t));
    data.append(field.name.data(), field.name.size());
    len = field.value.size();
    data.append(reinterpret_cast<char *>(&len), sizeof(uint32_t));
    data.append(field.value.data(), field.value.size());
  }
}

void RocksdbDB::DeserializeRowFilter(std::vector<Field> &values, const char *p, const char *lim,
                                     const std::vector<std::string> &fields) {
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
      values.push_back({field, value});
      filter_iter++;
    }
  }
  assert(values.size() == fields.size());
}

void RocksdbDB::DeserializeRowFilter(std::vector<Field> &values, const std::string &data,
                                     const std::vector<std::string> &fields) {
  const char *p = data.data();
  const char *lim = p + data.size();
  DeserializeRowFilter(values, p, lim, fields);
}

void RocksdbDB::DeserializeRow(std::vector<Field> &values, const char *p, const char *lim) {
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
    values.push_back({field, value});
  }
}

void RocksdbDB::DeserializeRow(std::vector<Field> &values, const std::string &data) {
  const char *p = data.data();
  const char *lim = p + data.size();
  DeserializeRow(values, p, lim);
}

DB::Status RocksdbDB::ReadSingle(const std::string &table, const std::string &key,
                                 const std::vector<std::string> *fields,
                                 std::vector<Field> &result) {
  std::string data;
  terarkdb::Status s = db_->Get(terarkdb::ReadOptions(), key, &data);
  if (s.IsNotFound()) {
    return kNotFound;
  } else if (!s.ok()) {
    throw utils::Exception(std::string("RocksDB Get: ") + s.ToString());
  }

  // Only deserialize if enabled
  if (deserialize_on_read_) {
    if (fields != nullptr) {
      DeserializeRowFilter(result, data, *fields);
    } else {
      DeserializeRow(result, data);
      assert(result.size() == static_cast<size_t>(fieldcount_));
    }
  }

  return kOK;
}

DB::Status RocksdbDB::ScanSingle(const std::string &table, const std::string &key, int len,
                                 const std::vector<std::string> *fields,
                                 std::vector<std::vector<Field>> &result) {
  terarkdb::Iterator *db_iter = db_->NewIterator(terarkdb::ReadOptions());
  db_iter->Seek(key);
  for (int i = 0; db_iter->Valid() && i < len; i++) {
    std::string data = db_iter->value().ToString();
    result.push_back(std::vector<Field>());
    std::vector<Field> &values = result.back();
    if (deserialize_on_read_) {
      if (fields != nullptr) {
        DeserializeRowFilter(values, data, *fields);
      } else {
        DeserializeRow(values, data);
        assert(values.size() == static_cast<size_t>(fieldcount_));
      }
    }
    db_iter->Next();
  }
  delete db_iter;
  return kOK;
}

DB::Status RocksdbDB::UpdateSingle(const std::string &table, const std::string &key,
                                   std::vector<Field> &values) {
  // Put directly without GET if write all fields
  if (values.size() == static_cast<size_t>(fieldcount_)) {
    return UpdateAllFieldsSingle(table, key, values);
  }

  std::string data;
  terarkdb::Status s = db_->Get(terarkdb::ReadOptions(), key, &data);
  if (s.IsNotFound()) {
    return kNotFound;
  } else if (!s.ok()) {
    throw utils::Exception(std::string("RocksDB Get: ") + s.ToString());
  }
  std::vector<Field> current_values;
  DeserializeRow(current_values, data);
  assert(current_values.size() == static_cast<size_t>(fieldcount_));
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
  terarkdb::WriteOptions wopt;
  wopt.disableWAL = disable_wal_;

  data.clear();
  SerializeRow(current_values, data);
  s = db_->Put(wopt, key, data);
  if (!s.ok()) {
    throw utils::Exception(std::string("RocksDB Put: ") + s.ToString());
  }
  return kOK;
}

DB::Status RocksdbDB::UpdateAllFieldsSingle(const std::string &table, const std::string &key,
                                   std::vector<Field> &values) {
  if (values.size() != static_cast<size_t>(fieldcount_)) {
    assert(false);
    return kError;
  }

  std::string data;

  terarkdb::WriteOptions wopt;
  wopt.disableWAL = disable_wal_;

  SerializeRow(values, data);
  terarkdb::Status s = db_->Put(wopt, key, data);
  if (!s.ok()) {
    throw utils::Exception(std::string("RocksDB Put: ") + s.ToString());
  }
  return kOK;
}

DB::Status RocksdbDB::MergeSingle(const std::string &table, const std::string &key,
                                  std::vector<Field> &values) {
  std::string data;
  SerializeRow(values, data);
  terarkdb::WriteOptions wopt;
  wopt.disableWAL = disable_wal_;
  terarkdb::Status s = db_->Merge(wopt, key, data);
  if (!s.ok()) {
    throw utils::Exception(std::string("RocksDB Merge: ") + s.ToString());
  }
  return kOK;
}

DB::Status RocksdbDB::InsertSingle(const std::string &table, const std::string &key,
                                   std::vector<Field> &values) {
  std::string data;
  SerializeRow(values, data);
  terarkdb::WriteOptions wopt;
  wopt.disableWAL = disable_wal_;
  terarkdb::Status s = db_->Put(wopt, key, data);
  if (!s.ok()) {
    throw utils::Exception(std::string("RocksDB Put: ") + s.ToString());
  }
  return kOK;
}

DB::Status RocksdbDB::DeleteSingle(const std::string &table, const std::string &key) {
  terarkdb::WriteOptions wopt;
  wopt.disableWAL = disable_wal_;
  terarkdb::Status s = db_->Delete(wopt, key);
  if (!s.ok()) {
    throw utils::Exception(std::string("RocksDB Delete: ") + s.ToString());
  }
  return kOK;
}

DB *NewRocksdbDB() {
  return new RocksdbDB;
}

const bool registered = DBFactory::RegisterDB("terarkdb", NewRocksdbDB);

} // ycsbc
