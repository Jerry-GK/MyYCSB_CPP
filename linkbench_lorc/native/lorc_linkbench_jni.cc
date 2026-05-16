#include <jni.h>

#include <algorithm>
#include <cstdint>
#include <exception>
#include <iostream>
#include <memory>
#include <sstream>
#include <string>
#include <vector>

#include "leveldb/cache.h"
#include "leveldb/db.h"
#include "leveldb/options.h"
#include "port/port.h"
#include "leveldb/params.h"
#include "leveldb/write_batch.h"

#include "rocksdb/cache.h"
#include "rocksdb/db.h"
#include "rocksdb/lorc.h"
#include "rocksdb/options.h"
#include "rocksdb/rbtree_lorc.h"
#include "rocksdb/slice.h"
#include "rocksdb/statistics.h"
#include "rocksdb/table.h"
#include "rocksdb/write_batch.h"

namespace {

enum class Backend {
  kRocks,
  kLsbm,
};

struct LinkBenchHandle {
  Backend backend = Backend::kRocks;
  rocksdb::DB* rocks_db = nullptr;
  leveldb::DB* lsbm_db = nullptr;
  std::shared_ptr<rocksdb::Cache> block_cache;
  std::shared_ptr<rocksdb::Cache> blob_cache;
  std::shared_ptr<rocksdb::LogicalOrderedRangeCache> range_cache;
  std::shared_ptr<rocksdb::Statistics> statistics;
  leveldb::Cache* lsbm_block_cache = nullptr;
};

void Throw(JNIEnv* env, const std::string& message) {
  jclass cls = env->FindClass("java/lang/RuntimeException");
  if (cls != nullptr) {
    env->ThrowNew(cls, message.c_str());
  }
}

std::string JStringToString(JNIEnv* env, jstring value) {
  if (value == nullptr) {
    return std::string();
  }
  const char* chars = env->GetStringUTFChars(value, nullptr);
  if (chars == nullptr) {
    return std::string();
  }
  std::string result(chars);
  env->ReleaseStringUTFChars(value, chars);
  return result;
}

std::string JByteArrayToString(JNIEnv* env, jbyteArray array) {
  if (array == nullptr) {
    return std::string();
  }
  const jsize len = env->GetArrayLength(array);
  std::string result(static_cast<size_t>(len), '\0');
  if (len > 0) {
    env->GetByteArrayRegion(array, 0, len,
                            reinterpret_cast<jbyte*>(&result[0]));
  }
  return result;
}

jbyteArray StringToJByteArray(JNIEnv* env, const std::string& value) {
  jbyteArray array = env->NewByteArray(static_cast<jsize>(value.size()));
  if (array == nullptr) {
    return nullptr;
  }
  if (!value.empty()) {
    env->SetByteArrayRegion(
        array, 0, static_cast<jsize>(value.size()),
        reinterpret_cast<const jbyte*>(value.data()));
  }
  return array;
}

LinkBenchHandle* ToHandle(jlong handle) {
  return reinterpret_cast<LinkBenchHandle*>(static_cast<intptr_t>(handle));
}

int BytewiseCompare(const std::string& a, const std::string& b) {
  const size_t min_len = std::min(a.size(), b.size());
  for (size_t i = 0; i < min_len; ++i) {
    const unsigned char ca = static_cast<unsigned char>(a[i]);
    const unsigned char cb = static_cast<unsigned char>(b[i]);
    if (ca < cb) {
      return -1;
    }
    if (ca > cb) {
      return 1;
    }
  }
  if (a.size() < b.size()) {
    return -1;
  }
  if (a.size() > b.size()) {
    return 1;
  }
  return 0;
}

bool BytewiseGreaterOrEqual(const std::string& a, const std::string& b) {
  return BytewiseCompare(a, b) >= 0;
}

void CheckStatus(JNIEnv* env, const rocksdb::Status& status,
                 const char* operation) {
  if (!status.ok()) {
    std::ostringstream oss;
    oss << operation << ": " << status.ToString();
    Throw(env, oss.str());
  }
}

void CheckStatus(JNIEnv* env, const leveldb::Status& status,
                 const char* operation) {
  if (!status.ok()) {
    std::ostringstream oss;
    oss << operation << ": " << status.ToString();
    Throw(env, oss.str());
  }
}

void AppendLevelDbScan(leveldb::DB* db, const std::string& start_key,
                       const std::string& end_key, jint limit,
                       std::vector<std::string>* values) {
  leveldb::ReadOptions read_options;
  std::unique_ptr<leveldb::Iterator> it(db->NewIterator(read_options));
  for (it->Seek(start_key);
       it->Valid() &&
       (limit <= 0 || values->size() < static_cast<size_t>(limit));
       it->Next()) {
    std::string key = it->key().ToString();
    if (!end_key.empty() && BytewiseGreaterOrEqual(key, end_key)) {
      break;
    }
    values->emplace_back(it->value().ToString());
  }
  // The caller checks iterator status because it needs JNIEnv for exceptions.
}

}  // namespace

extern "C" JNIEXPORT jlong JNICALL
Java_com_facebook_LinkBench_LinkStoreLorcKV_nativeOpen(
    JNIEnv* env, jclass, jstring jengine, jstring jpath, jboolean destroy,
    jboolean create_if_missing, jboolean enable_blob_files, jint min_blob_size,
    jlong blob_file_size, jlong block_cache_size, jlong blob_cache_size,
    jlong range_cache_size, jboolean value_separation_aware,
    jboolean bypass_lower_cache_on_refill, jboolean index_only_on_refill,
    jlong min_materialized_value_bytes, jlong min_materialized_range_entries,
    jlong min_materialized_range_bytes, jlong max_materialized_range_entries,
    jlong max_materialized_range_bytes, jlong short_range_expansion_entries,
    jboolean short_range_probe_admission, jlong short_range_probe_capacity,
    jboolean disable_auto_compactions, jboolean enable_statistics) {
  try {
    std::unique_ptr<LinkBenchHandle> handle(new LinkBenchHandle());
    const std::string engine = JStringToString(env, jengine);
    const std::string path = JStringToString(env, jpath);

    if (engine == "lsbm") {
      handle->backend = Backend::kLsbm;
      leveldb::config::run_compaction = !disable_auto_compactions;
      leveldb::config::buffered_merge = true;
      leveldb::runtime::compaction_min_score = 1.0;
      leveldb::runtime::compaction_buffer_trim_interval = 30;

      leveldb::Options options;
      options.create_if_missing = create_if_missing;
      options.compression = leveldb::kNoCompression;
      options.key_cache_ = nullptr;
      if (block_cache_size > 0) {
        handle->lsbm_block_cache =
            leveldb::NewLRUCache(static_cast<size_t>(block_cache_size));
        options.block_cache = handle->lsbm_block_cache;
      }

      if (destroy) {
        leveldb::Status s = leveldb::DestroyDB(path, options);
        CheckStatus(env, s, "LSbM DestroyDB");
        if (env->ExceptionCheck()) {
          return 0;
        }
      }

      leveldb::DB* db = nullptr;
      leveldb::Status s = leveldb::DB::Open(options, path, &db);
      CheckStatus(env, s, "LSbM DB::Open");
      if (env->ExceptionCheck()) {
        return 0;
      }
      handle->lsbm_db = db;
      return static_cast<jlong>(reinterpret_cast<intptr_t>(handle.release()));
    }

    if (!engine.empty() && engine != "rocksdb") {
      Throw(env, "unknown lorckv.engine: " + engine);
      return 0;
    }

    rocksdb::Options options;
    options.create_if_missing = create_if_missing;
    options.disable_auto_compactions = disable_auto_compactions;
    options.compression = rocksdb::kNoCompression;
    options.IncreaseParallelism();
    options.OptimizeLevelStyleCompaction();

    if (enable_statistics) {
      handle->statistics = rocksdb::CreateDBStatistics();
      options.statistics = handle->statistics;
    }

    if (enable_blob_files) {
      options.enable_blob_files = true;
      options.min_blob_size = static_cast<uint64_t>(min_blob_size);
      options.blob_file_size = static_cast<uint64_t>(blob_file_size);
      options.enable_blob_garbage_collection = false;
      if (blob_cache_size > 0) {
        handle->blob_cache =
            rocksdb::NewLRUCache(static_cast<size_t>(blob_cache_size));
        options.blob_cache = handle->blob_cache;
      }
    }

    rocksdb::BlockBasedTableOptions table_options;
    if (block_cache_size > 0) {
      handle->block_cache =
          rocksdb::NewLRUCache(static_cast<size_t>(block_cache_size));
      table_options.block_cache = handle->block_cache;
    } else {
      table_options.no_block_cache = true;
    }
    options.table_factory.reset(
        rocksdb::NewBlockBasedTableFactory(table_options));

    if (range_cache_size > 0) {
      handle->range_cache = rocksdb::NewRBTreeLogicalOrderedRangeCache(
          static_cast<size_t>(range_cache_size),
          rocksdb::LorcLogger::Level::WARN,
          rocksdb::PhysicalRangeType::CONTINUOUS,
          rocksdb::RangeCacheVictimPolicy::BOUNDARY_LRU);
      handle->range_cache->setEnableStatistic(enable_statistics);
      handle->range_cache->setValueSeparationAware(value_separation_aware);
      handle->range_cache->setBypassLowerCacheOnRefill(
          bypass_lower_cache_on_refill);
      handle->range_cache->setIndexOnlyOnRefill(index_only_on_refill);
      handle->range_cache->setMinMaterializedValueBytes(
          static_cast<size_t>(min_materialized_value_bytes));
      handle->range_cache->setMinMaterializedRangeEntries(
          static_cast<size_t>(min_materialized_range_entries));
      handle->range_cache->setMinMaterializedRangeBytes(
          static_cast<size_t>(min_materialized_range_bytes));
      handle->range_cache->setMaxMaterializedRangeEntries(
          static_cast<size_t>(max_materialized_range_entries));
      handle->range_cache->setMaxMaterializedRangeBytes(
          static_cast<size_t>(max_materialized_range_bytes));
      handle->range_cache->setShortRangeExpansionEntries(
          static_cast<size_t>(short_range_expansion_entries));
      handle->range_cache->setShortRangeProbeAdmission(
          static_cast<bool>(short_range_probe_admission));
      handle->range_cache->setShortRangeProbeCapacity(
          static_cast<size_t>(short_range_probe_capacity));
      options.range_cache = handle->range_cache;
    }

    if (destroy) {
      rocksdb::Status s = rocksdb::DestroyDB(path, options);
      CheckStatus(env, s, "DestroyDB");
      if (env->ExceptionCheck()) {
        return 0;
      }
    }

    rocksdb::DB* db = nullptr;
    rocksdb::Status s = rocksdb::DB::Open(options, path, &db);
    CheckStatus(env, s, "DB::Open");
    if (env->ExceptionCheck()) {
      return 0;
    }
    handle->rocks_db = db;
    return static_cast<jlong>(reinterpret_cast<intptr_t>(handle.release()));
  } catch (const std::exception& e) {
    Throw(env, e.what());
    return 0;
  }
}

extern "C" JNIEXPORT void JNICALL
Java_com_facebook_LinkBench_LinkStoreLorcKV_nativeClose(JNIEnv* env, jclass,
                                                        jlong native_handle) {
  try {
    std::unique_ptr<LinkBenchHandle> handle(ToHandle(native_handle));
    if (handle && handle->range_cache != nullptr) {
      std::cout << "[LORC_STATS]"
                << " current_size=" << handle->range_cache->getCurrentSize()
                << " capacity=" << handle->range_cache->getCapacity()
                << " total_range_length=" << handle->range_cache->getTotalRangeLength()
                << " logical_range_count=" << handle->range_cache->logicalRangeCount()
                << " physical_range_count=" << handle->range_cache->physicalRangeCount()
                << " materialized_entries=" << handle->range_cache->totalMaterializedEntries()
                << " materialized_key_bytes=" << handle->range_cache->totalMaterializedKeyBytes()
                << " materialized_value_bytes=" << handle->range_cache->totalMaterializedValueBytes()
                << " full_hit_rate=" << handle->range_cache->fullHitRate()
                << " hit_size_rate=" << handle->range_cache->hitSizeRate()
                << " put_range_num=" << handle->range_cache->getCacheStatistic().getPutRangeNum()
                << " avg_put_range_us=" << handle->range_cache->getCacheStatistic().getAvgPutRangeTime()
                << " get_range_num=" << handle->range_cache->getCacheStatistic().getGetRangeNum()
                << " avg_get_range_us=" << handle->range_cache->getCacheStatistic().getAvgGetRangeTime()
                << " value_separated_refill_ranges=" << handle->range_cache->valueSeparatedRefillRanges()
                << " value_separated_refill_entries=" << handle->range_cache->valueSeparatedRefillEntries()
                << " value_separated_refill_bytes=" << handle->range_cache->valueSeparatedRefillBytes()
                << " value_payload_demotion_ranges=" << handle->range_cache->valuePayloadDemotionRanges()
                << " value_payload_demotion_entries=" << handle->range_cache->valuePayloadDemotionEntries()
                << " value_payload_demotion_bytes=" << handle->range_cache->valuePayloadDemotionBytes()
                << " short_expansion_candidates=" << handle->range_cache->shortRangeExpansionCandidates()
                << " short_expansion_admitted=" << handle->range_cache->shortRangeExpansionAdmitted()
                << " short_expansion_filtered=" << handle->range_cache->shortRangeExpansionFiltered()
                << " short_expansion_extra_entries=" << handle->range_cache->shortRangeExpansionExtraEntries()
                << " foreground_invalidations=" << handle->range_cache->foregroundInvalidations()
                << " foreground_invalidation_removed_ranges=" << handle->range_cache->foregroundInvalidationRemovedRanges()
                << " write_churn_bypass_count=" << handle->range_cache->writeChurnBypassCount()
                << std::endl;
    }
    if (handle && handle->rocks_db != nullptr) {
      delete handle->rocks_db;
      handle->rocks_db = nullptr;
    }
    if (handle && handle->lsbm_db != nullptr) {
      delete handle->lsbm_db;
      handle->lsbm_db = nullptr;
    }
    if (handle && handle->lsbm_block_cache != nullptr) {
      delete handle->lsbm_block_cache;
      handle->lsbm_block_cache = nullptr;
    }
  } catch (const std::exception& e) {
    Throw(env, e.what());
  }
}

extern "C" JNIEXPORT void JNICALL
Java_com_facebook_LinkBench_LinkStoreLorcKV_nativePut(
    JNIEnv* env, jclass, jlong native_handle, jbyteArray jkey,
    jbyteArray jvalue, jboolean disable_wal) {
  try {
    LinkBenchHandle* handle = ToHandle(native_handle);
    std::string key = JByteArrayToString(env, jkey);
    std::string value = JByteArrayToString(env, jvalue);
    if (handle->backend == Backend::kLsbm) {
      leveldb::WriteOptions write_options;
      CheckStatus(env, handle->lsbm_db->Put(write_options, key, value),
                  "LSbM Put");
      return;
    }
    rocksdb::WriteOptions write_options;
    write_options.disableWAL = disable_wal;
    CheckStatus(env, handle->rocks_db->Put(write_options, key, value), "Put");
  } catch (const std::exception& e) {
    Throw(env, e.what());
  }
}

extern "C" JNIEXPORT void JNICALL
Java_com_facebook_LinkBench_LinkStoreLorcKV_nativePutBatch(
    JNIEnv* env, jclass, jlong native_handle, jobjectArray jkeys,
    jobjectArray jvalues, jboolean disable_wal) {
  try {
    LinkBenchHandle* handle = ToHandle(native_handle);
    const jsize nkeys = env->GetArrayLength(jkeys);
    const jsize nvalues = env->GetArrayLength(jvalues);
    if (nkeys != nvalues) {
      Throw(env, "nativePutBatch received a mismatched key/value batch");
      return;
    }
    if (handle->backend == Backend::kLsbm) {
      leveldb::WriteBatch batch;
      for (jsize i = 0; i < nkeys; i++) {
        jbyteArray jkey =
            static_cast<jbyteArray>(env->GetObjectArrayElement(jkeys, i));
        jbyteArray jvalue =
            static_cast<jbyteArray>(env->GetObjectArrayElement(jvalues, i));
        std::string key = JByteArrayToString(env, jkey);
        std::string value = JByteArrayToString(env, jvalue);
        env->DeleteLocalRef(jkey);
        env->DeleteLocalRef(jvalue);
        batch.Put(key, value);
      }
      leveldb::WriteOptions write_options;
      CheckStatus(env, handle->lsbm_db->Write(write_options, &batch),
                  "LSbM WriteBatch");
      return;
    }

    rocksdb::WriteBatch batch;
    std::vector<std::string> written_keys;
    written_keys.reserve(static_cast<size_t>(nkeys));
    for (jsize i = 0; i < nkeys; i++) {
      jbyteArray jkey =
          static_cast<jbyteArray>(env->GetObjectArrayElement(jkeys, i));
      jbyteArray jvalue =
          static_cast<jbyteArray>(env->GetObjectArrayElement(jvalues, i));
      std::string key = JByteArrayToString(env, jkey);
      std::string value = JByteArrayToString(env, jvalue);
      env->DeleteLocalRef(jkey);
      env->DeleteLocalRef(jvalue);
      batch.Put(key, value);
      written_keys.emplace_back(std::move(key));
    }
    rocksdb::WriteOptions write_options;
    write_options.disableWAL = disable_wal;
    CheckStatus(env, handle->rocks_db->Write(write_options, &batch),
                "WriteBatch");
    if (handle->range_cache) {
      for (const std::string& key : written_keys) {
        handle->range_cache->invalidateRangeContainingKey(rocksdb::Slice(key));
      }
    }
  } catch (const std::exception& e) {
    Throw(env, e.what());
  }
}

extern "C" JNIEXPORT jbyteArray JNICALL
Java_com_facebook_LinkBench_LinkStoreLorcKV_nativeGet(
    JNIEnv* env, jclass, jlong native_handle, jbyteArray jkey) {
  try {
    LinkBenchHandle* handle = ToHandle(native_handle);
    std::string key = JByteArrayToString(env, jkey);
    std::string value;
    if (handle->backend == Backend::kLsbm) {
      leveldb::Status s =
          handle->lsbm_db->Get(leveldb::ReadOptions(), key, &value);
      if (s.IsNotFound()) {
        return nullptr;
      }
      CheckStatus(env, s, "LSbM Get");
      if (env->ExceptionCheck()) {
        return nullptr;
      }
      return StringToJByteArray(env, value);
    }
    rocksdb::Status s =
        handle->rocks_db->Get(rocksdb::ReadOptions(), key, &value);
    if (s.IsNotFound()) {
      return nullptr;
    }
    CheckStatus(env, s, "Get");
    if (env->ExceptionCheck()) {
      return nullptr;
    }
    return StringToJByteArray(env, value);
  } catch (const std::exception& e) {
    Throw(env, e.what());
    return nullptr;
  }
}

extern "C" JNIEXPORT void JNICALL
Java_com_facebook_LinkBench_LinkStoreLorcKV_nativeDelete(
    JNIEnv* env, jclass, jlong native_handle, jbyteArray jkey) {
  try {
    LinkBenchHandle* handle = ToHandle(native_handle);
    std::string key = JByteArrayToString(env, jkey);
    if (handle->backend == Backend::kLsbm) {
      leveldb::WriteOptions write_options;
      CheckStatus(env, handle->lsbm_db->Delete(write_options, key),
                  "LSbM Delete");
      return;
    }
    rocksdb::WriteOptions write_options;
    CheckStatus(env, handle->rocks_db->Delete(write_options, key), "Delete");
  } catch (const std::exception& e) {
    Throw(env, e.what());
  }
}

extern "C" JNIEXPORT jobjectArray JNICALL
Java_com_facebook_LinkBench_LinkStoreLorcKV_nativeScan(
    JNIEnv* env, jclass, jlong native_handle, jbyteArray jstart_key,
    jbyteArray jend_key, jint limit) {
  try {
    LinkBenchHandle* handle = ToHandle(native_handle);
    std::string start_key = JByteArrayToString(env, jstart_key);
    std::string end_key;
    if (jend_key != nullptr) {
      end_key = JByteArrayToString(env, jend_key);
    }

    std::vector<std::string> values;
    if (handle->backend == Backend::kLsbm) {
      leveldb::ReadOptions read_options;
      std::unique_ptr<leveldb::Iterator> it(
          handle->lsbm_db->NewIterator(read_options));
      for (it->Seek(start_key);
           it->Valid() &&
           (limit <= 0 || values.size() < static_cast<size_t>(limit));
           it->Next()) {
        std::string key = it->key().ToString();
        if (!end_key.empty() && BytewiseGreaterOrEqual(key, end_key)) {
          break;
        }
        values.emplace_back(it->value().ToString());
      }
      CheckStatus(env, it->status(), "LSbM Scan");
      if (env->ExceptionCheck()) {
        return nullptr;
      }
    } else {
      rocksdb::Slice end_slice;
      if (!end_key.empty()) {
        end_slice = rocksdb::Slice(end_key);
      }
      std::vector<std::string> keys;
      rocksdb::ReadOptions read_options;
      rocksdb::Status s = handle->rocks_db->Scan(
          read_options, handle->rocks_db->DefaultColumnFamily(),
          rocksdb::Slice(start_key), end_slice, static_cast<size_t>(limit),
          &keys, &values);
      CheckStatus(env, s, "Scan");
      if (env->ExceptionCheck()) {
        return nullptr;
      }

      std::vector<std::string> filtered_values;
      filtered_values.reserve(values.size());
      for (size_t i = 0; i < values.size(); i++) {
        if (!end_key.empty() && BytewiseGreaterOrEqual(keys[i], end_key)) {
          continue;
        }
        filtered_values.emplace_back(std::move(values[i]));
      }
      values.swap(filtered_values);
    }

    jclass byte_array_class = env->FindClass("[B");
    jobjectArray result =
        env->NewObjectArray(static_cast<jsize>(values.size()),
                            byte_array_class, nullptr);
    if (result == nullptr) {
      return nullptr;
    }
    for (jsize i = 0; i < static_cast<jsize>(values.size()); i++) {
      jbyteArray value =
          StringToJByteArray(env, values[static_cast<size_t>(i)]);
      env->SetObjectArrayElement(result, i, value);
      env->DeleteLocalRef(value);
    }
    return result;
  } catch (const std::exception& e) {
    Throw(env, e.what());
    return nullptr;
  }
}
