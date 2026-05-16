/*
 * Copyright 2012, Facebook, Inc.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
package com.facebook.LinkBench;

import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.EOFException;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Properties;
import java.util.concurrent.atomic.AtomicLong;

import org.apache.log4j.Logger;

/**
 * Official LinkBench store adapter for LorcKV.
 *
 * This class intentionally leaves LinkBench's workload generation and request
 * driver untouched. It only supplies a database backend through the extension
 * point documented in the upstream LinkBench README.
 */
public class LinkStoreLorcKV extends GraphStore {
  private static final Logger logger = Logger.getLogger(LinkStoreLorcKV.class);

  private static final byte KEY_LINK_ORDER = 'L';
  private static final byte KEY_LINK_POINT = 'P';
  private static final byte KEY_LINK_COUNT = 'C';
  private static final byte KEY_NODE = 'N';

  private static final int LONG_BYTES = 8;
  private static final int INT_BYTES = 4;
  private static final int LINK_PREFIX_BYTES = 1 + LONG_BYTES + LONG_BYTES;

  private static final String PROP_DB_PATH = "lorckv.db_path";
  private static final String PROP_ENGINE = "lorckv.engine";
  private static final String PROP_NATIVE_LIBRARY = "lorckv.native_library";
  private static final String PROP_DESTROY = "lorckv.destroy";
  private static final String PROP_CREATE_IF_MISSING = "lorckv.create_if_missing";
  private static final String PROP_ENABLE_BLOB_FILES = "lorckv.enable_blob_files";
  private static final String PROP_MIN_BLOB_SIZE = "lorckv.min_blob_size";
  private static final String PROP_BLOB_FILE_SIZE = "lorckv.blob_file_size";
  private static final String PROP_BLOCK_CACHE_SIZE = "lorckv.block_cache_size";
  private static final String PROP_BLOB_CACHE_SIZE = "lorckv.blob_cache_size";
  private static final String PROP_RANGE_CACHE_SIZE = "lorckv.range_cache_size";
  private static final String PROP_VALUE_SEPARATION_AWARE =
      "lorckv.value_separation_aware";
  private static final String PROP_BYPASS_LOWER_CACHE_ON_REFILL =
      "lorckv.bypass_lower_cache_on_refill";
  private static final String PROP_INDEX_ONLY_ON_REFILL =
      "lorckv.index_only_on_refill";
  private static final String PROP_MIN_MATERIALIZED_VALUE_BYTES =
      "lorckv.min_materialized_value_bytes";
  private static final String PROP_MAX_MATERIALIZED_RANGE_ENTRIES =
      "lorckv.max_materialized_range_entries";
  private static final String PROP_MAX_MATERIALIZED_RANGE_BYTES =
      "lorckv.max_materialized_range_bytes";
  private static final String PROP_DISABLE_AUTO_COMPACTIONS =
      "lorckv.disable_auto_compactions";
  private static final String PROP_ENABLE_STATISTICS = "lorckv.enable_statistics";
  private static final String PROP_DISABLE_WAL = "lorckv.disable_wal";

  private static final Object DB_LOCK = new Object();
  private static final Object COUNT_LOCK = new Object();
  private static long nativeHandle = 0;
  private static int openRefs = 0;
  private static String openDbPath = null;
  private static final AtomicLong nextNodeId = new AtomicLong(1);

  private boolean disableWal = false;

  static {
    // The script that installs this adapter into an official LinkBench checkout
    // sets java.library.path. A caller may also pass an absolute library path
    // through lorckv.native_library, which is loaded during initialize().
  }

  @Override
  public void initialize(Properties p, Phase currentPhase, int threadId)
      throws IOException, Exception {
    String libPath = p.getProperty(PROP_NATIVE_LIBRARY);
    if (libPath != null && libPath.length() > 0) {
      System.load(libPath);
    } else {
      System.loadLibrary("lorc_linkbench_jni");
    }

    String dbPath = getRequired(p, PROP_DB_PATH);
    boolean destroy = getBoolean(p, PROP_DESTROY, false) &&
        currentPhase == Phase.LOAD && threadId == 0;
    boolean createIfMissing = getBoolean(p, PROP_CREATE_IF_MISSING,
        currentPhase == Phase.LOAD);

    disableWal = getBoolean(p, PROP_DISABLE_WAL, false);
    if (currentPhase == Phase.REQUEST) {
      long maxId = getLong(p, Config.MAX_ID, 1L);
      while (true) {
        long current = nextNodeId.get();
        if (current >= maxId || nextNodeId.compareAndSet(current, maxId)) {
          break;
        }
      }
    }

    synchronized (DB_LOCK) {
      if (nativeHandle == 0) {
        nativeHandle = nativeOpen(
            getString(p, PROP_ENGINE, "rocksdb"),
            dbPath,
            destroy,
            createIfMissing,
            getBoolean(p, PROP_ENABLE_BLOB_FILES, false),
            getInt(p, PROP_MIN_BLOB_SIZE, 512),
            getLong(p, PROP_BLOB_FILE_SIZE, 67108864L),
            getLong(p, PROP_BLOCK_CACHE_SIZE, 0L),
            getLong(p, PROP_BLOB_CACHE_SIZE, 0L),
            getLong(p, PROP_RANGE_CACHE_SIZE, 0L),
            getBoolean(p, PROP_VALUE_SEPARATION_AWARE, false),
            getBoolean(p, PROP_BYPASS_LOWER_CACHE_ON_REFILL, false),
            getBoolean(p, PROP_INDEX_ONLY_ON_REFILL, false),
            getLong(p, PROP_MIN_MATERIALIZED_VALUE_BYTES, 0L),
            getLong(p, PROP_MAX_MATERIALIZED_RANGE_ENTRIES, 0L),
            getLong(p, PROP_MAX_MATERIALIZED_RANGE_BYTES, 0L),
            getBoolean(p, PROP_DISABLE_AUTO_COMPACTIONS, false),
            getBoolean(p, PROP_ENABLE_STATISTICS, false));
        openDbPath = dbPath;
        logger.info("Opened LorcKV backend at " + dbPath);
      } else if (!dbPath.equals(openDbPath)) {
        throw new IllegalStateException("LorcKV backend already open at " +
            openDbPath + ", cannot also open " + dbPath);
      }
      openRefs++;
    }
  }

  @Override
  public void close() {
    synchronized (DB_LOCK) {
      if (openRefs > 0 && --openRefs == 0 && nativeHandle != 0) {
        nativeClose(nativeHandle);
        nativeHandle = 0;
        openDbPath = null;
      }
    }
  }

  @Override
  public void clearErrors(int threadID) {
    // Native RocksDB operations report failures as exceptions; there is no
    // per-thread connection state to reset.
  }

  @Override
  public boolean addLink(String dbid, Link link, boolean noinverse)
      throws Exception {
    Link old = getLink(dbid, link.id1, link.link_type, link.id2);
    if (old != null) {
      nativeDelete(handle(), linkOrderKey(old));
    }
    byte[] value = encodeLink(link);
    nativePut(handle(), linkPointKey(link.id1, link.link_type, link.id2),
        value, disableWal);
    nativePut(handle(), linkOrderKey(link), value, disableWal);
    if (old == null) {
      if (link.visibility == VISIBILITY_DEFAULT) {
        adjustCount(link.id1, link.link_type, 1);
      }
    } else if (old.visibility != VISIBILITY_DEFAULT &&
        link.visibility == VISIBILITY_DEFAULT) {
      adjustCount(link.id1, link.link_type, 1);
    } else if (old.visibility == VISIBILITY_DEFAULT &&
        link.visibility != VISIBILITY_DEFAULT) {
      adjustCount(link.id1, link.link_type, -1);
    }
    return old == null;
  }

  @Override
  public boolean deleteLink(String dbid, long id1, long linkType, long id2,
      boolean noinverse, boolean expunge) throws Exception {
    Link old = getLink(dbid, id1, linkType, id2);
    if (old == null) {
      return false;
    }
    nativeDelete(handle(), linkOrderKey(old));
    if (old.visibility == VISIBILITY_DEFAULT) {
      adjustCount(id1, linkType, -1);
    }
    if (expunge) {
      nativeDelete(handle(), linkPointKey(id1, linkType, id2));
    } else {
      old.visibility = VISIBILITY_HIDDEN;
      byte[] value = encodeLink(old);
      nativePut(handle(), linkPointKey(old.id1, old.link_type, old.id2),
          value, disableWal);
      nativePut(handle(), linkOrderKey(old), value, disableWal);
    }
    return true;
  }

  @Override
  public boolean updateLink(String dbid, Link link, boolean noinverse)
      throws Exception {
    return !addLink(dbid, link, noinverse);
  }

  @Override
  public Link getLink(String dbid, long id1, long linkType, long id2)
      throws Exception {
    byte[] data = nativeGet(handle(), linkPointKey(id1, linkType, id2));
    if (data == null) {
      return null;
    }
    return decodeLink(data);
  }

  @Override
  public Link[] getLinkList(String dbid, long id1, long linkType)
      throws Exception {
    return getLinkList(dbid, id1, linkType, 0, Long.MAX_VALUE, 0, rangeLimit);
  }

  @Override
  public Link[] getLinkList(String dbid, long id1, long linkType,
      long minTimestamp, long maxTimestamp, int offset, int limit)
      throws Exception {
    byte[] prefix = linkListPrefix(id1, linkType);
    byte[] scanStart = linkListSeekStart(prefix, maxTimestamp);
    byte[] scanEnd = linkListSeekEnd(prefix, minTimestamp);
    byte[][] values = nativeScan(handle(), scanStart, scanEnd,
        boundedScanLimit(offset, limit));
    ArrayList<Link> result = new ArrayList<Link>(Math.max(0, limit));
    int skipped = 0;
    for (byte[] value : values) {
      Link link = decodeLink(value);
      if (link.visibility != VISIBILITY_DEFAULT) {
        continue;
      }
      if (link.time < minTimestamp || link.time > maxTimestamp) {
        continue;
      }
      if (skipped++ < offset) {
        continue;
      }
      result.add(link);
      if (limit > 0 && result.size() >= limit) {
        break;
      }
    }
    return result.size() == 0 ? null : result.toArray(new Link[result.size()]);
  }

  @Override
  public long countLinks(String dbid, long id1, long linkType)
      throws Exception {
    return readCount(id1, linkType);
  }

  @Override
  public int bulkLoadBatchSize() {
    return 1000;
  }

  @Override
  public void addBulkLinks(String dbid, List<Link> links, boolean noinverse)
      throws Exception {
    byte[][] keys = new byte[links.size() * 2][];
    byte[][] values = new byte[links.size() * 2][];
    int pos = 0;
    for (Link link : links) {
      byte[] value = encodeLink(link);
      keys[pos] = linkPointKey(link.id1, link.link_type, link.id2);
      values[pos++] = value;
      keys[pos] = linkOrderKey(link);
      values[pos++] = value;
    }
    nativePutBatch(handle(), keys, values, disableWal);
  }

  @Override
  public void addBulkCounts(String dbid, List<LinkCount> counts)
      throws Exception {
    byte[][] keys = new byte[counts.size()][];
    byte[][] values = new byte[counts.size()][];
    for (int i = 0; i < counts.size(); i++) {
      LinkCount count = counts.get(i);
      keys[i] = linkCountKey(count.id1, count.link_type);
      values[i] = encodeLong(count.count);
    }
    nativePutBatch(handle(), keys, values, disableWal);
  }

  @Override
  public void resetNodeStore(String dbid, long startID) throws Exception {
    nextNodeId.set(startID);
  }

  @Override
  public long addNode(String dbid, Node node) throws Exception {
    long id = nextNodeId.getAndIncrement();
    Node stored = new Node(id, node.type, node.version, node.time, node.data);
    nativePut(handle(), nodeKey(stored.type, id), encodeNode(stored), disableWal);
    return id;
  }

  @Override
  public long[] bulkAddNodes(String dbid, List<Node> nodes) throws Exception {
    long[] ids = new long[nodes.size()];
    byte[][] keys = new byte[nodes.size()][];
    byte[][] values = new byte[nodes.size()][];
    for (int i = 0; i < nodes.size(); i++) {
      long id = nextNodeId.getAndIncrement();
      Node node = nodes.get(i);
      Node stored = new Node(id, node.type, node.version, node.time, node.data);
      ids[i] = id;
      keys[i] = nodeKey(stored.type, id);
      values[i] = encodeNode(stored);
    }
    nativePutBatch(handle(), keys, values, disableWal);
    return ids;
  }

  @Override
  public Node getNode(String dbid, int type, long id) throws Exception {
    byte[] data = nativeGet(handle(), nodeKey(type, id));
    return data == null ? null : decodeNode(data);
  }

  @Override
  public boolean updateNode(String dbid, Node node) throws Exception {
    byte[] key = nodeKey(node.type, node.id);
    if (nativeGet(handle(), key) == null) {
      return false;
    }
    nativePut(handle(), key, encodeNode(node), disableWal);
    return true;
  }

  @Override
  public boolean deleteNode(String dbid, int type, long id) throws Exception {
    byte[] key = nodeKey(type, id);
    if (nativeGet(handle(), key) == null) {
      return false;
    }
    nativeDelete(handle(), key);
    return true;
  }

  private static long handle() {
    if (nativeHandle == 0) {
      throw new IllegalStateException("LorcKV backend is not initialized");
    }
    return nativeHandle;
  }

  private static String getRequired(Properties p, String key) {
    String value = p.getProperty(key);
    if (value == null || value.length() == 0) {
      throw new IllegalArgumentException("Missing required property " + key);
    }
    return value;
  }

  private static String getString(Properties p, String key, String def) {
    String value = p.getProperty(key);
    return value == null ? def : value;
  }

  private static boolean getBoolean(Properties p, String key, boolean def) {
    String value = p.getProperty(key);
    return value == null ? def : Boolean.parseBoolean(value);
  }

  private static int getInt(Properties p, String key, int def) {
    String value = p.getProperty(key);
    return value == null ? def : Integer.parseInt(value);
  }

  private static long getLong(Properties p, String key, long def) {
    String value = p.getProperty(key);
    return value == null ? def : Long.parseLong(value);
  }

  private static byte[] linkPointKey(long id1, long linkType, long id2) {
    ByteBuffer buf = ByteBuffer.allocate(1 + 3 * LONG_BYTES);
    buf.put(KEY_LINK_POINT);
    putOrderedLong(buf, id1);
    putOrderedLong(buf, linkType);
    putOrderedLong(buf, id2);
    return buf.array();
  }

  private static byte[] linkOrderKey(Link link) {
    ByteBuffer buf = ByteBuffer.allocate(1 + 4 * LONG_BYTES);
    buf.put(KEY_LINK_ORDER);
    putOrderedLong(buf, link.id1);
    putOrderedLong(buf, link.link_type);
    putOrderedLong(buf, Long.MAX_VALUE - link.time);
    putOrderedLong(buf, link.id2);
    return buf.array();
  }

  private static byte[] linkListPrefix(long id1, long linkType) {
    ByteBuffer buf = ByteBuffer.allocate(LINK_PREFIX_BYTES);
    buf.put(KEY_LINK_ORDER);
    putOrderedLong(buf, id1);
    putOrderedLong(buf, linkType);
    return buf.array();
  }

  private static byte[] linkListSeekStart(byte[] prefix, long maxTimestamp) {
    if (maxTimestamp == Long.MAX_VALUE) {
      return prefix;
    }
    ByteBuffer buf = ByteBuffer.allocate(prefix.length + LONG_BYTES);
    buf.put(prefix);
    putOrderedLong(buf, Long.MAX_VALUE - maxTimestamp);
    return buf.array();
  }

  private static byte[] linkListSeekEnd(byte[] prefix, long minTimestamp) {
    if (minTimestamp <= 0) {
      return prefixEnd(prefix);
    }
    long inverseTime = Long.MAX_VALUE - minTimestamp;
    if (inverseTime == Long.MAX_VALUE) {
      return prefixEnd(prefix);
    }
    ByteBuffer buf = ByteBuffer.allocate(prefix.length + LONG_BYTES);
    buf.put(prefix);
    putOrderedLong(buf, inverseTime + 1);
    return buf.array();
  }

  private int boundedScanLimit(int offset, int limit) {
    long wanted = Math.max(0, offset);
    wanted += limit > 0 ? limit : rangeLimit;
    if (wanted <= 0) {
      return rangeLimit;
    }
    return wanted > Integer.MAX_VALUE ? Integer.MAX_VALUE : (int) wanted;
  }

  private static byte[] linkCountKey(long id1, long linkType) {
    ByteBuffer buf = ByteBuffer.allocate(1 + 2 * LONG_BYTES);
    buf.put(KEY_LINK_COUNT);
    putOrderedLong(buf, id1);
    putOrderedLong(buf, linkType);
    return buf.array();
  }

  private static byte[] nodeKey(int type, long id) {
    ByteBuffer buf = ByteBuffer.allocate(1 + INT_BYTES + LONG_BYTES);
    buf.put(KEY_NODE);
    buf.putInt(type ^ Integer.MIN_VALUE);
    putOrderedLong(buf, id);
    return buf.array();
  }

  private static void putOrderedLong(ByteBuffer buf, long value) {
    buf.putLong(value ^ Long.MIN_VALUE);
  }

  private static byte[] prefixEnd(byte[] prefix) {
    byte[] end = Arrays.copyOf(prefix, prefix.length);
    for (int i = end.length - 1; i >= 0; i--) {
      int b = end[i] & 0xff;
      if (b != 0xff) {
        end[i] = (byte)(b + 1);
        return Arrays.copyOf(end, i + 1);
      }
    }
    return null;
  }

  private static byte[] encodeLink(Link link) throws IOException {
    ByteArrayOutputStream bos = new ByteArrayOutputStream();
    DataOutputStream out = new DataOutputStream(bos);
    out.writeLong(link.id1);
    out.writeLong(link.link_type);
    out.writeLong(link.id2);
    out.writeByte(link.visibility);
    out.writeInt(link.version);
    out.writeLong(link.time);
    writeBytes(out, link.data);
    out.close();
    return bos.toByteArray();
  }

  private static byte[] encodeLong(long value) {
    ByteBuffer buf = ByteBuffer.allocate(LONG_BYTES);
    buf.putLong(value);
    return buf.array();
  }

  private static long decodeLong(byte[] value) {
    if (value == null || value.length != LONG_BYTES) {
      return 0L;
    }
    return ByteBuffer.wrap(value).getLong();
  }

  private static long readCount(long id1, long linkType) throws Exception {
    return decodeLong(nativeGet(handle(), linkCountKey(id1, linkType)));
  }

  private static void adjustCount(long id1, long linkType, long delta)
      throws Exception {
    synchronized (COUNT_LOCK) {
      byte[] key = linkCountKey(id1, linkType);
      long count = decodeLong(nativeGet(handle(), key));
      long updated = count + delta;
      if (updated < 0) {
        updated = 0;
      }
      nativePut(handle(), key, encodeLong(updated), false);
    }
  }

  private static Link decodeLink(byte[] data) throws IOException {
    DataInputStream in = new DataInputStream(new java.io.ByteArrayInputStream(data));
    long id1 = in.readLong();
    long linkType = in.readLong();
    long id2 = in.readLong();
    byte visibility = in.readByte();
    int version = in.readInt();
    long time = in.readLong();
    byte[] payload = readBytes(in);
    return new Link(id1, linkType, id2, visibility, payload, version, time);
  }

  private static byte[] encodeNode(Node node) throws IOException {
    ByteArrayOutputStream bos = new ByteArrayOutputStream();
    DataOutputStream out = new DataOutputStream(bos);
    out.writeLong(node.id);
    out.writeInt(node.type);
    out.writeLong(node.version);
    out.writeInt(node.time);
    writeBytes(out, node.data);
    out.close();
    return bos.toByteArray();
  }

  private static Node decodeNode(byte[] data) throws IOException {
    DataInputStream in = new DataInputStream(new java.io.ByteArrayInputStream(data));
    long id = in.readLong();
    int type = in.readInt();
    long version = in.readLong();
    int time = in.readInt();
    byte[] payload = readBytes(in);
    return new Node(id, type, version, time, payload);
  }

  private static void writeBytes(DataOutputStream out, byte[] data)
      throws IOException {
    if (data == null) {
      out.writeInt(-1);
    } else {
      out.writeInt(data.length);
      out.write(data);
    }
  }

  private static byte[] readBytes(DataInputStream in) throws IOException {
    int len = in.readInt();
    if (len < 0) {
      return null;
    }
    byte[] data = new byte[len];
    int off = 0;
    while (off < len) {
      int n = in.read(data, off, len - off);
      if (n < 0) {
        throw new EOFException("Unexpected end of encoded payload");
      }
      off += n;
    }
    return data;
  }

  private static native long nativeOpen(String engine, String path, boolean destroy,
      boolean createIfMissing, boolean enableBlobFiles, int minBlobSize,
      long blobFileSize, long blockCacheSize, long blobCacheSize,
      long rangeCacheSize, boolean valueSeparationAware,
      boolean bypassLowerCacheOnRefill, boolean indexOnlyOnRefill,
      long minMaterializedValueBytes, long maxMaterializedRangeEntries,
      long maxMaterializedRangeBytes, boolean disableAutoCompactions,
      boolean enableStatistics);

  private static native void nativeClose(long handle);

  private static native void nativePut(long handle, byte[] key, byte[] value,
      boolean disableWal);

  private static native void nativePutBatch(long handle, byte[][] keys,
      byte[][] values, boolean disableWal);

  private static native byte[] nativeGet(long handle, byte[] key);

  private static native void nativeDelete(long handle, byte[] key);

  private static native byte[][] nativeScan(long handle, byte[] startKey,
      byte[] endKey, int limit);
}
