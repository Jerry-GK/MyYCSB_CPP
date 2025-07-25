//
//  rocksdb_db.cc
//  YCSB-cpp
//
//  Copyright (c) 2020 Youngjae Lee <ls4154.lee@gmail.com>.
//  Modifications Copyright 2023 Chengye YU <yuchengye2013 AT outlook.com>.
//

#include "rocksdb_db.h"

#include "core/core_workload.h"
#include "core/db_factory.h"
#include "utils/utils.h"

#include <rocksdb/cache.h>
#include <rocksdb/filter_policy.h>
#include <rocksdb/merge_operator.h>
#include <rocksdb/status.h>
#include <rocksdb/utilities/options_util.h>
#include <rocksdb/write_batch.h>

namespace {
  const std::string PROP_NAME = "rocksdb.dbname";
  const std::string PROP_NAME_DEFAULT = "";

  const std::string PROP_FORMAT = "rocksdb.format";
  const std::string PROP_FORMAT_DEFAULT = "single";

  const std::string PROP_MERGEUPDATE = "rocksdb.mergeupdate";
  const std::string PROP_MERGEUPDATE_DEFAULT = "false";

  const std::string PROP_DESTROY = "rocksdb.destroy";
  const std::string PROP_DESTROY_DEFAULT = "false";

  const std::string PROP_CREATE_IF_MISSING = "rocksdb.create_if_missing";
  const std::string PROP_CREATE_IF_MISSING_DEFAULT = "false";

  const std::string PROP_DISABLE_AUTO_COMPACTIONS = "rocksdb.disable_auto_compactions";
  const std::string PROP_DISABLE_AUTO_COMPACTIONS_DEFAULT = "false";

  const std::string PROP_DISABLE_WAL = "rocksdb.disableWAL";
  const std::string PROP_DISABLE_WAL_DEFAULT = "true";

  const std::string PROP_ENABLE_BLOB_FILES = "rocksdb.enable_blob_files";
  const std::string PROP_ENABLE_BLOB_FILES_DEFAULT = "false";

  const std::string PROP_MIN_BLOB_SIZE = "rocksdb.min_blob_size";
  const std::string PROP_MIN_BLOB_SIZE_DEFAULT = "512";

  const std::string PROP_BLOB_FILE_SIZE = "rocksdb.blob_file_size";
  const std::string PROP_BLOB_FILE_SIZE_DEFAULT = "67108864";

  const std::string PROP_ENABLE_BLOB_GC = "rocksdb.enable_blob_garbage_collection";
  const std::string PROP_ENABLE_BLOB_GC_DEFAULT = "false";

  const std::string PROP_BLOB_GC_AGE_CUTOFF = "rocksdb.blob_garbage_collection_age_cutoff";
  const std::string PROP_BLOB_GC_AGE_CUTOFF_DEFAULT = "0.25";

  const std::string PROP_BLOB_CACHE_SIZE = "rocksdb.blob_cache_size";
  const std::string PROP_BLOB_CACHE_SIZE_DEFAULT = "0";

  const std::string PROP_RANGE_CACHE_SIZE = "rocksdb.range_cache_size";
  const std::string PROP_RANGE_CACHE_SIZE_DEFAULT = "0";

  const std::string PROP_COMPRESSION = "rocksdb.compression";
  const std::string PROP_COMPRESSION_DEFAULT = "no";

  const std::string PROP_MAX_BG_JOBS = "rocksdb.max_background_jobs";
  const std::string PROP_MAX_BG_JOBS_DEFAULT = "0";

  const std::string PROP_TARGET_FILE_SIZE_BASE = "rocksdb.target_file_size_base";
  const std::string PROP_TARGET_FILE_SIZE_BASE_DEFAULT = "0";

  const std::string PROP_TARGET_FILE_SIZE_MULT = "rocksdb.target_file_size_multiplier";
  const std::string PROP_TARGET_FILE_SIZE_MULT_DEFAULT = "0";

  const std::string PROP_MAX_BYTES_FOR_LEVEL_BASE = "rocksdb.max_bytes_for_level_base";
  const std::string PROP_MAX_BYTES_FOR_LEVEL_BASE_DEFAULT = "0";

  const std::string PROP_WRITE_BUFFER_SIZE = "rocksdb.write_buffer_size";
  const std::string PROP_WRITE_BUFFER_SIZE_DEFAULT = "0";

  const std::string PROP_MAX_WRITE_BUFFER = "rocksdb.max_write_buffer_number";
  const std::string PROP_MAX_WRITE_BUFFER_DEFAULT = "0";

  const std::string PROP_COMPACTION_PRI = "rocksdb.compaction_pri";
  const std::string PROP_COMPACTION_PRI_DEFAULT = "-1";

  const std::string PROP_MAX_OPEN_FILES = "rocksdb.max_open_files";
  const std::string PROP_MAX_OPEN_FILES_DEFAULT = "-1";

  const std::string PROP_L0_COMPACTION_TRIGGER = "rocksdb.level0_file_num_compaction_trigger";
  const std::string PROP_L0_COMPACTION_TRIGGER_DEFAULT = "0";

  const std::string PROP_L0_SLOWDOWN_TRIGGER = "rocksdb.level0_slowdown_writes_trigger";
  const std::string PROP_L0_SLOWDOWN_TRIGGER_DEFAULT = "0";

  const std::string PROP_L0_STOP_TRIGGER = "rocksdb.level0_stop_writes_trigger";
  const std::string PROP_L0_STOP_TRIGGER_DEFAULT = "0";

  const std::string PROP_USE_DIRECT_WRITE = "rocksdb.use_direct_io_for_flush_compaction";
  const std::string PROP_USE_DIRECT_WRITE_DEFAULT = "false";

  const std::string PROP_USE_DIRECT_READ = "rocksdb.use_direct_reads";
  const std::string PROP_USE_DIRECT_READ_DEFAULT = "false";

  const std::string PROP_USE_MMAP_WRITE = "rocksdb.allow_mmap_writes";
  const std::string PROP_USE_MMAP_WRITE_DEFAULT = "false";

  const std::string PROP_USE_MMAP_READ = "rocksdb.allow_mmap_reads";
  const std::string PROP_USE_MMAP_READ_DEFAULT = "false";

  const std::string PROP_CACHE_SIZE = "rocksdb.block_cache_size";
  const std::string PROP_CACHE_SIZE_DEFAULT = "0";

  const std::string PROP_COMPRESSED_CACHE_SIZE = "rocksdb.compressed_cache_size";
  const std::string PROP_COMPRESSED_CACHE_SIZE_DEFAULT = "0";

  const std::string PROP_BLOOM_BITS = "rocksdb.bloom_bits";
  const std::string PROP_BLOOM_BITS_DEFAULT = "0";

  const std::string PROP_INCREASE_PARALLELISM = "rocksdb.increase_parallelism";
  const std::string PROP_INCREASE_PARALLELISM_DEFAULT = "false";

  const std::string PROP_OPTIMIZE_LEVELCOMP = "rocksdb.optimize_level_style_compaction";
  const std::string PROP_OPTIMIZE_LEVELCOMP_DEFAULT = "false";

  const std::string PROP_OPTIONS_FILE = "rocksdb.optionsfile";
  const std::string PROP_OPTIONS_FILE_DEFAULT = "";

  const std::string PROP_ENV_URI = "rocksdb.env_uri";
  const std::string PROP_ENV_URI_DEFAULT = "";

  const std::string PROP_FS_URI = "rocksdb.fs_uri";
  const std::string PROP_FS_URI_DEFAULT = "";

  const std::string PROP_DESERIALIZE_ON_READ = "rocksdb.deserialize_on_read";
  const std::string PROP_DESERIALIZE_ON_READ_DEFAULT = "false";

  static std::shared_ptr<rocksdb::Env> env_guard;
  static std::shared_ptr<rocksdb::Cache> block_cache;
  static std::shared_ptr<rocksdb::Cache> blob_cache;
  static std::shared_ptr<rocksdb::LogicalOrderedRangeCache> range_cache;
#if ROCKSDB_MAJOR < 8
  static std::shared_ptr<rocksdb::Cache> block_cache_compressed;
#endif
} // anonymous

namespace ycsbc {

std::vector<rocksdb::ColumnFamilyHandle *> RocksdbDB::cf_handles_;
rocksdb::DB *RocksdbDB::db_ = nullptr;
int RocksdbDB::ref_cnt_ = 0;
std::mutex RocksdbDB::mu_;

void RocksdbDB::Init() {
// merge operator disabled by default due to link error
#ifdef USE_MERGEUPDATE
  class YCSBUpdateMerge : public rocksdb::AssociativeMergeOperator {
   public:
    virtual bool Merge(const rocksdb::Slice &key, const rocksdb::Slice *existing_value,
                       const rocksdb::Slice &value, std::string *new_value,
                       rocksdb::Logger *logger) const override {
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

  rocksdb::Options opt;
  std::vector<rocksdb::ColumnFamilyDescriptor> cf_descs;
  GetOptions(props, &opt, &cf_descs);
#ifdef USE_MERGEUPDATE
  opt.merge_operator.reset(new YCSBUpdateMerge);
#endif

  rocksdb::Status s;
  if (props.GetProperty(PROP_DESTROY, PROP_DESTROY_DEFAULT) == "true") {
    s = rocksdb::DestroyDB(db_path, opt);
    if (!s.ok()) {
      throw utils::Exception(std::string("RocksDB DestroyDB: ") + s.ToString());
    }
  }
  if (cf_descs.empty()) {
    s = rocksdb::DB::Open(opt, db_path, &db_);
  } else {
    s = rocksdb::DB::Open(opt, db_path, cf_descs, &cf_handles_, &db_);
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

void RocksdbDB::GetOptions(const utils::Properties &props, rocksdb::Options *opt,
                           std::vector<rocksdb::ColumnFamilyDescriptor> *cf_descs) {
  std::string env_uri = props.GetProperty(PROP_ENV_URI, PROP_ENV_URI_DEFAULT);
  std::string fs_uri = props.GetProperty(PROP_FS_URI, PROP_FS_URI_DEFAULT);
  rocksdb::Env* env =  rocksdb::Env::Default();;
  if (!env_uri.empty() || !fs_uri.empty()) {
    rocksdb::Status s = rocksdb::Env::CreateFromUri(rocksdb::ConfigOptions(),
                                                    env_uri, fs_uri, &env, &env_guard);
    if (!s.ok()) {
      throw utils::Exception(std::string("RocksDB CreateFromUri: ") + s.ToString());
    }
    opt->env = env;
  }

  const std::string options_file = props.GetProperty(PROP_OPTIONS_FILE, PROP_OPTIONS_FILE_DEFAULT);
  if (options_file != "") {
    rocksdb::ConfigOptions config_options;
    config_options.ignore_unknown_options = false;
    config_options.input_strings_escaped = true;
    config_options.env = env;
    rocksdb::Status s = rocksdb::LoadOptionsFromFile(config_options, options_file, opt, cf_descs);
    if (!s.ok()) {
      throw utils::Exception(std::string("RocksDB LoadOptionsFromFile: ") + s.ToString());
    }
  } else {
    const std::string compression_type = props.GetProperty(PROP_COMPRESSION,
                                                           PROP_COMPRESSION_DEFAULT);
    if (compression_type == "no") {
      opt->compression = rocksdb::kNoCompression;
    } else if (compression_type == "snappy") {
      opt->compression = rocksdb::kSnappyCompression;
    } else if (compression_type == "zlib") {
      opt->compression = rocksdb::kZlibCompression;
    } else if (compression_type == "bzip2") {
      opt->compression = rocksdb::kBZip2Compression;
    } else if (compression_type == "lz4") {
      opt->compression = rocksdb::kLZ4Compression;
    } else if (compression_type == "lz4hc") {
      opt->compression = rocksdb::kLZ4HCCompression;
    } else if (compression_type == "xpress") {
      opt->compression = rocksdb::kXpressCompression;
    } else if (compression_type == "zstd") {
      opt->compression = rocksdb::kZSTD;
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
    if (props.GetProperty(PROP_ENABLE_BLOB_FILES, PROP_ENABLE_BLOB_FILES_DEFAULT) == "true") {
      opt->enable_blob_files = true;
      
      int blob_size = std::stoi(props.GetProperty(PROP_MIN_BLOB_SIZE, PROP_MIN_BLOB_SIZE_DEFAULT));
      opt->min_blob_size = blob_size;
      
      int blob_file_size = std::stoi(props.GetProperty(PROP_BLOB_FILE_SIZE, PROP_BLOB_FILE_SIZE_DEFAULT));
      opt->blob_file_size = blob_file_size;
      
      if (props.GetProperty(PROP_ENABLE_BLOB_GC, PROP_ENABLE_BLOB_GC_DEFAULT) == "true") {
        opt->enable_blob_garbage_collection = true;
      }
      
      double gc_age_cutoff = std::stod(props.GetProperty(PROP_BLOB_GC_AGE_CUTOFF, PROP_BLOB_GC_AGE_CUTOFF_DEFAULT));
      opt->blob_garbage_collection_age_cutoff = gc_age_cutoff;
      
      size_t blob_cache_size = std::stoul(props.GetProperty(PROP_BLOB_CACHE_SIZE, PROP_BLOB_CACHE_SIZE_DEFAULT));
      if (blob_cache_size > 0) {
        blob_cache = rocksdb::NewLRUCache(blob_cache_size);
        opt->blob_cache = blob_cache;
      }
    }

    size_t range_cache_size = std::stoul(props.GetProperty(PROP_RANGE_CACHE_SIZE, PROP_RANGE_CACHE_SIZE_DEFAULT));
    if (range_cache_size > 0) {
      range_cache = rocksdb::NewRBTreeLogicalOrderedRangeCache(range_cache_size, rocksdb::LorcLogger::Level::WARN, rocksdb::PhysicalRangeType::VEC);
      opt->range_cache = range_cache;
    }

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
      opt->compaction_pri = static_cast<rocksdb::CompactionPri>(val);
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

    rocksdb::BlockBasedTableOptions table_options;
    size_t block_cache_size = std::stoul(props.GetProperty(PROP_CACHE_SIZE, PROP_CACHE_SIZE_DEFAULT));
    if (block_cache_size > 0) {
      block_cache = rocksdb::NewLRUCache(block_cache_size);
      table_options.block_cache = block_cache;
    } else {
      table_options.no_block_cache = true;
    }
#if ROCKSDB_MAJOR < 8
    size_t compressed_cache_size = std::stoul(props.GetProperty(PROP_COMPRESSED_CACHE_SIZE,
                                                                PROP_COMPRESSED_CACHE_SIZE_DEFAULT));
    if (compressed_cache_size > 0) {
      block_cache_compressed = rocksdb::NewLRUCache(compressed_cache_size);
      table_options.block_cache_compressed = block_cache_compressed;
    }
#endif
    int bloom_bits = std::stoul(props.GetProperty(PROP_BLOOM_BITS, PROP_BLOOM_BITS_DEFAULT));
    if (bloom_bits > 0) {
      table_options.filter_policy.reset(rocksdb::NewBloomFilterPolicy(bloom_bits));
    }
    opt->table_factory.reset(rocksdb::NewBlockBasedTableFactory(table_options));

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
  rocksdb::Status s = db_->Get(rocksdb::ReadOptions(), key, &data);
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
  std::vector<std::string> keys;
  std::vector<std::string> values;
  rocksdb::Status s = db_->Scan(rocksdb::ReadOptions(), db_->DefaultColumnFamily(), rocksdb::Slice(key), len, &keys, &values);

  if (deserialize_on_read_) {
    for (size_t i = 0; i < keys.size(); i++) {
      if (!s.ok()) {
        throw utils::Exception(std::string("RocksDB Scan: ") + s.ToString());
      }
      std::string &data = values[i];
      result.push_back(std::vector<Field>());
      std::vector<Field> &field_value = result.back();
      if (fields != nullptr) {
        DeserializeRowFilter(field_value, data, *fields);
      } else {
        DeserializeRow(field_value, data);
        assert(values.size() == static_cast<size_t>(fieldcount_));
      }
    }
  }

  return kOK;
}

DB::Status RocksdbDB::UpdateSingle(const std::string &table, const std::string &key,
                                   std::vector<Field> &values) {
  // Put directly without GET if write all fields
  if (values.size() == static_cast<size_t>(fieldcount_)) {
    return UpdateAllFieldsSingle(table, key, values);
  }

  std::string data;
  rocksdb::Status s = db_->Get(rocksdb::ReadOptions(), key, &data);
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
  rocksdb::WriteOptions wopt;
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

  rocksdb::WriteOptions wopt;
  wopt.disableWAL = disable_wal_;

  SerializeRow(values, data);
  rocksdb::Status s = db_->Put(wopt, key, data);
  if (!s.ok()) {
    throw utils::Exception(std::string("RocksDB Put: ") + s.ToString());
  }
  return kOK;
}

DB::Status RocksdbDB::MergeSingle(const std::string &table, const std::string &key,
                                  std::vector<Field> &values) {
  std::string data;
  SerializeRow(values, data);
  rocksdb::WriteOptions wopt;
  wopt.disableWAL = disable_wal_;
  rocksdb::Status s = db_->Merge(wopt, key, data);
  if (!s.ok()) {
    throw utils::Exception(std::string("RocksDB Merge: ") + s.ToString());
  }
  return kOK;
}

DB::Status RocksdbDB::InsertSingle(const std::string &table, const std::string &key,
                                   std::vector<Field> &values) {
  std::string data;
  SerializeRow(values, data);
  rocksdb::WriteOptions wopt;
  wopt.disableWAL = disable_wal_;
  rocksdb::Status s = db_->Put(wopt, key, data);
  if (!s.ok()) {
    throw utils::Exception(std::string("RocksDB Put: ") + s.ToString());
  }
  return kOK;
}

DB::Status RocksdbDB::DeleteSingle(const std::string &table, const std::string &key) {
  rocksdb::WriteOptions wopt;
  wopt.disableWAL = disable_wal_;
  rocksdb::Status s = db_->Delete(wopt, key);
  if (!s.ok()) {
    throw utils::Exception(std::string("RocksDB Delete: ") + s.ToString());
  }
  return kOK;
}

DB *NewRocksdbDB() {
  return new RocksdbDB;
}

const bool registered = DBFactory::RegisterDB("rocksdb_lorc", NewRocksdbDB);

} // ycsbc
