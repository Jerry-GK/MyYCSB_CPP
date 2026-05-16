# Official LinkBench adapter for LorcKV

This directory contains a database adapter for the upstream Facebook
LinkBench driver. It does not replace LinkBench's workload, distributions, load
phase, request phase, or statistics path. The adapter is installed through the
official `LinkStore`/`NodeStore` extension point described in LinkBench's
README.

## Build

```bash
./linkbench_lorc/build_official_linkbench_lorc.sh
```

The script copies `LinkStoreLorcKV.java` into an existing official LinkBench
checkout, builds the upstream LinkBench jar, and builds the JNI library that
links to the LorcKV RocksDB fork.

## Run

Use upstream LinkBench commands with `LinkConfigLorcKV.properties`, for example:

```bash
cd /home/gjr/projects/linkbench_official
JAVA_HOME=/usr/lib/jvm/java-11-openjdk-amd64 \
  java -Djava.library.path=/home/gjr/projects/MyYCSB_CPP/linkbench_lorc/build \
  -cp target/FacebookLinkBench.jar:target/dependency/* \
  com.facebook.LinkBench.LinkBenchDriver \
  -c /home/gjr/projects/MyYCSB_CPP/linkbench_lorc/LinkConfigLorcKV.properties \
  -l
```

The workload file remains `config/FBWorkload.properties`, which uses the
official `config/Distribution.dat`.

The paper experiment is run by:

```bash
python3 experiments/run_official_linkbench_lorc.py \
  --out result/log/official_linkbench_lorc_final_20260516 \
  --maxid1 10001 \
  --requests 20000 \
  --requesters 1 \
  --loaders 1 \
  --warmup-requests 80000 \
  --variants RocksDB BlobDB RocksDB+LORC BlobDB+LORC LSbM
```

This is a scaled-down official LinkBench run: the upstream load phase, request
phase, `FBWorkload.properties`, and `Distribution.dat` are unchanged. The
script only selects the LorcKV-backed `LinkStore` and passes database/cache
options for the compared systems. The default comparison uses five systems:
RocksDB, BlobDB, RocksDB+LORC, BlobDB+LORC, and LSbM. For the mixed official
LinkBench workload, LORC variants use a hybrid 1GB budget that reserves part of
the budget for native block/blob caches; the scan-only YCSB experiments use
range-cache-only configurations to isolate the range-cache path. The index-only
BlobDB mode remains available as an optional diagnostic variant, but it is not
part of the default LinkBench comparison.

Warmup is a fixed-count official request phase by default. It still uses
LinkBench's randomized request generator; making the count fixed prevents a
faster system from consuming more warmup requests than a slower system before
the measured phase.
