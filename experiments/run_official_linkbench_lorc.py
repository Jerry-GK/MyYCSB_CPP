#!/usr/bin/env python3
"""Run official Facebook LinkBench against LorcKV backends.

The script uses the upstream LinkBench driver, FBWorkload.properties, and
Distribution.dat. It only changes the storage backend through LinkBench's
documented LinkStore/NodeStore plugin mechanism.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LINKBENCH_HOME = Path(os.environ.get("LINKBENCH_HOME", "/home/gjr/projects/linkbench_official"))
CONFIG = ROOT / "linkbench_lorc" / "LinkConfigLorcKV.properties"
NATIVE_LIB = ROOT / "linkbench_lorc" / "build" / "liblorc_linkbench_jni.so"
BUILD_SCRIPT = ROOT / "linkbench_lorc" / "build_official_linkbench_lorc.sh"
JAVA_HOME = Path(os.environ.get("JAVA_HOME", "/usr/lib/jvm/java-11-openjdk-amd64"))
try:
    has_javac = (JAVA_HOME / "bin" / "javac").exists()
except OSError:
    has_javac = False
if not has_javac:
    JAVA_HOME = Path("/usr/lib/jvm/java-11-openjdk-amd64")


GiB = 1024 * 1024 * 1024
MiB = 1024 * 1024
LINKSTORE_DEFAULT_RANGE_LIMIT = 10000
LINKBENCH_LORC_MAX_ADMITTED_ENTRIES = 512


VARIANTS = {
    "RocksDB": {
        "lorckv.engine": "rocksdb",
        "lorckv.enable_blob_files": "false",
        "lorckv.min_blob_size": "512",
        "lorckv.block_cache_size": str(1 * GiB),
        "lorckv.blob_cache_size": "0",
        "lorckv.range_cache_size": "0",
        "lorckv.value_separation_aware": "false",
        "lorckv.index_only_on_refill": "false",
        "lorckv.bypass_lower_cache_on_refill": "false",
        "lorckv.max_materialized_range_entries": str(LINKBENCH_LORC_MAX_ADMITTED_ENTRIES),
    },
    "BlobDB": {
        "lorckv.engine": "rocksdb",
        "lorckv.enable_blob_files": "true",
        "lorckv.min_blob_size": "1",
        "lorckv.block_cache_size": str(256 * MiB),
        "lorckv.blob_cache_size": str(768 * MiB),
        "lorckv.range_cache_size": "0",
        "lorckv.value_separation_aware": "false",
        "lorckv.index_only_on_refill": "false",
        "lorckv.bypass_lower_cache_on_refill": "false",
    },
    "RocksDB+LORC": {
        "lorckv.engine": "rocksdb",
        "lorckv.enable_blob_files": "false",
        "lorckv.min_blob_size": "512",
        "lorckv.block_cache_size": str(512 * MiB),
        "lorckv.blob_cache_size": "0",
        "lorckv.range_cache_size": str(512 * MiB),
        "lorckv.value_separation_aware": "false",
        "lorckv.index_only_on_refill": "false",
        "lorckv.bypass_lower_cache_on_refill": "false",
    },
    "BlobDB+IndexLORC": {
        "lorckv.engine": "rocksdb",
        "lorckv.enable_blob_files": "true",
        "lorckv.min_blob_size": "1",
        "lorckv.block_cache_size": str(192 * MiB),
        "lorckv.blob_cache_size": str(768 * MiB),
        "lorckv.range_cache_size": str(64 * MiB),
        "lorckv.value_separation_aware": "true",
        "lorckv.index_only_on_refill": "true",
        "lorckv.bypass_lower_cache_on_refill": "false",
        "lorckv.max_materialized_range_entries": str(LINKBENCH_LORC_MAX_ADMITTED_ENTRIES),
    },
    "BlobDB+LORC": {
        "lorckv.engine": "rocksdb",
        "lorckv.enable_blob_files": "true",
        "lorckv.min_blob_size": "1",
        "lorckv.block_cache_size": str(256 * MiB),
        "lorckv.blob_cache_size": str(256 * MiB),
        "lorckv.range_cache_size": str(512 * MiB),
        "lorckv.value_separation_aware": "true",
        "lorckv.index_only_on_refill": "false",
        "lorckv.bypass_lower_cache_on_refill": "false",
        "lorckv.max_materialized_range_entries": str(LINKBENCH_LORC_MAX_ADMITTED_ENTRIES),
    },
    "LSbM": {
        "lorckv.engine": "lsbm",
        "lorckv.enable_blob_files": "false",
        "lorckv.min_blob_size": "512",
        "lorckv.block_cache_size": str(1 * GiB),
        "lorckv.blob_cache_size": "0",
        "lorckv.range_cache_size": "0",
        "lorckv.value_separation_aware": "false",
        "lorckv.index_only_on_refill": "false",
        "lorckv.bypass_lower_cache_on_refill": "false",
    },
}


REQ_DONE_RE = re.compile(r"REQUEST PHASE COMPLETED\..*Requests/second = ([0-9.]+)")
TOTAL_REQUESTS_RE = re.compile(
    r"total requests = (?P<requests>[0-9]+) requests/second = (?P<rate>[0-9.]+) "
    r"found = (?P<found>[0-9]+) not found = (?P<not_found>[0-9]+) "
    r"history queries = (?P<history>[0-9]+)/(?P<history_total>[0-9]+)"
)
RANGE_SIZE_RE = re.compile(
    r"RANGE_SIZE totalOps = (?P<count>[0-9]+).*?"
    r"mean = (?P<mean>[0-9.]+).*?"
    r"95% = (?P<p95>[0-9.]+).*?"
    r"99% = (?P<p99>[0-9.]+).*?"
    r"max = (?P<max>[0-9.]+)"
)
OP_RE = re.compile(
    r"(?P<op>[A-Z_]+) count = (?P<count>[0-9]+).*?p95 = (?P<p95>\[[^\]]+\]ms).*?"
    r"p99 = (?P<p99>\[[^\]]+\]ms).*?max = (?P<max>[0-9.]+)ms\s+mean = (?P<mean>[0-9.]+)ms"
)
LORC_STATS_RE = re.compile(r"\[LORC_STATS\]\s+(?P<body>.*)")

GET_LINK_LIST_PROB = 0.507119145


def estimate_warmup_seconds(
    *,
    maxid1: int,
    startid1: int,
    requesters: int,
    coverage: float,
    hot_fraction: float,
    range_limit: int,
    min_seconds: int,
    assumed_ops_per_second: int,
) -> tuple[int, int, int]:
    """Estimate a conservative time-based official LinkBench warmup.

    LinkBench's built-in warmup is time based, not request-count based.  We keep
    using that official warmup path, but derive the time from the number of
    random getLinkList operations needed to cover the hot id1 population with
    high probability.  The formula intentionally errs on the high side because
    LORC materializes list payloads during warmup.
    """
    population = max(1, maxid1 - startid1)
    hot_ids = max(1, int(math.ceil(population * hot_fraction)))
    coverage = min(max(coverage, 0.0), 0.9999)
    if hot_ids == 1:
      needed_list_ops = 1
    else:
      needed_list_ops = int(math.ceil(math.log(1.0 - coverage) /
                                     math.log(1.0 - 1.0 / hot_ids)))
    range_multiplier = max(1.0, math.sqrt(max(1, range_limit) /
                                          float(LINKSTORE_DEFAULT_RANGE_LIMIT)))
    needed_requests = int(math.ceil(
        needed_list_ops / GET_LINK_LIST_PROB * range_multiplier))
    aggregate_rate = max(1, assumed_ops_per_second * max(1, requesters))
    seconds = max(min_seconds, int(math.ceil(needed_requests / aggregate_rate)))
    return seconds, needed_requests, hot_ids


def estimate_warmup_requests(
    *,
    maxid1: int,
    startid1: int,
    coverage: float,
    hot_fraction: float,
    range_limit: int,
    min_requests: int,
) -> tuple[int, int]:
    """Estimate fixed-count warmup requests for official LinkBench.

    The stock LinkBench warmup is time based.  That is convenient for a single
    system, but unfair in a five-system comparison because faster systems
    consume more warmup writes and advance farther in the request RNG stream.
    This helper keeps the official random request generator but makes warmup a
    fixed request phase so all systems see the same randomized operations.
    """
    population = max(1, maxid1 - startid1)
    hot_ids = max(1, int(math.ceil(population * hot_fraction)))
    coverage = min(max(coverage, 0.0), 0.9999)
    if hot_ids == 1:
        needed_list_ops = 1
    else:
        needed_list_ops = int(math.ceil(math.log(1.0 - coverage) /
                                       math.log(1.0 - 1.0 / hot_ids)))
    range_multiplier = max(1.0, math.sqrt(max(1, range_limit) /
                                          float(LINKSTORE_DEFAULT_RANGE_LIMIT)))
    needed_requests = int(math.ceil(
        needed_list_ops / GET_LINK_LIST_PROB * range_multiplier))
    return max(min_requests, needed_requests), hot_ids


def run_logged(cmd: list[str], cwd: Path, log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["JAVA_HOME"] = str(JAVA_HOME)
    env["PATH"] = f"{JAVA_HOME / 'bin'}:{env.get('PATH', '')}"
    env["LINKBENCH_HOME"] = str(LINKBENCH_HOME)
    env["LD_LIBRARY_PATH"] = f"/home/gjr/mylibs/lorcdb_release/lib:{env.get('LD_LIBRARY_PATH', '')}"
    with log_path.open("w", encoding="utf-8") as log:
        log.write("$ " + " ".join(shlex.quote(x) for x in cmd) + "\n")
        log.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            log.write(line)
        rc = proc.wait()
        if rc != 0:
            raise subprocess.CalledProcessError(rc, cmd)


def java_cmd(extra: dict[str, str], load: bool, request: bool, csvstats: Path | None) -> list[str]:
    cmd = [
        str(JAVA_HOME / "bin" / "java"),
        f"-Djava.library.path={ROOT / 'linkbench_lorc' / 'build'}",
        "-cp",
        "target/FacebookLinkBench.jar:target/dependency/*",
        "com.facebook.LinkBench.LinkBenchDriver",
        "-c",
        str(CONFIG),
    ]
    for key, value in extra.items():
        cmd.extend(["-D", f"{key}={value}"])
    if csvstats is not None:
        cmd.extend(["-csvstats", str(csvstats)])
    if load:
        cmd.append("-l")
    if request:
        cmd.append("-r")
    return cmd


def parse_log(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    row: dict[str, str] = {}
    done = REQ_DONE_RE.search(text)
    if done:
        row["throughput_ops_s"] = done.group(1)
    total = TOTAL_REQUESTS_RE.search(text)
    if total:
        row["request_count"] = total.group("requests")
        row["thread_reported_ops_s"] = total.group("rate")
        row["found_count"] = total.group("found")
        row["not_found_count"] = total.group("not_found")
        row["history_queries"] = total.group("history")
        row["history_query_total"] = total.group("history_total")
    range_size = RANGE_SIZE_RE.search(text)
    if range_size:
        row["range_size_count"] = range_size.group("count")
        row["range_size_mean"] = range_size.group("mean")
        row["range_size_p95"] = range_size.group("p95")
        row["range_size_p99"] = range_size.group("p99")
        row["range_size_max"] = range_size.group("max")
    for stats in LORC_STATS_RE.finditer(text):
        for token in stats.group("body").split():
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            row[f"lorc_{key}"] = value
    for match in OP_RE.finditer(text):
        op = match.group("op").lower()
        row[f"{op}_count"] = match.group("count")
        row[f"{op}_mean_ms"] = match.group("mean")
        row[f"{op}_p95_bin"] = match.group("p95")
        row[f"{op}_p99_bin"] = match.group("p99")
        row[f"{op}_max_ms"] = match.group("max")
    return row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "result" / "log" / "official_linkbench_lorc"))
    parser.add_argument("--maxid1", type=int, default=10001)
    parser.add_argument("--requests", type=int, default=20000)
    parser.add_argument("--requesters", type=int, default=1)
    parser.add_argument("--loaders", type=int, default=1)
    parser.add_argument(
        "--warmup-time",
        type=int,
        default=None,
        help="Official LinkBench warmup time in seconds. Defaults to an "
             "auto-estimate from maxid1, range limit, and target coverage.",
    )
    parser.add_argument("--warmup-coverage", type=float, default=0.995)
    parser.add_argument("--warmup-hot-fraction", type=float, default=0.05)
    parser.add_argument("--warmup-range-limit", type=int,
                        default=LINKSTORE_DEFAULT_RANGE_LIMIT)
    parser.add_argument("--warmup-min-seconds", type=int, default=90)
    parser.add_argument("--warmup-assumed-ops-per-second", type=int,
                        default=500)
    parser.add_argument(
        "--time-based-warmup",
        action="store_true",
        help="Use LinkBench's stock time-based warmup inside the measured "
             "request command. The default is a fixed-count official request "
             "warmup phase so every system sees the same warmup operations.",
    )
    parser.add_argument("--warmup-requests", type=int, default=None)
    parser.add_argument("--warmup-min-requests", type=int, default=80000)
    parser.add_argument(
        "--lorc-max-admitted-entries",
        type=int,
        default=LINKBENCH_LORC_MAX_ADMITTED_ENTRIES,
        help="Maximum returned entries admitted as one LORC segment in the "
             "official mixed LinkBench run. This is an admission guardrail for "
             "high-fanout lists, not a workload generator change.",
    )
    parser.add_argument("--load-random-seed", type=int, default=20260516)
    parser.add_argument("--warmup-random-seed", type=int, default=20260517)
    parser.add_argument("--request-random-seed", type=int, default=20260518)
    parser.add_argument("--reuse-load", action="store_true")
    parser.add_argument(
        "--variants",
        nargs="*",
        default=["RocksDB", "BlobDB", "RocksDB+LORC", "BlobDB+LORC", "LSbM"],
    )
    args = parser.parse_args()

    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    warmup_time = args.warmup_time
    if args.time_based_warmup:
        if warmup_time is None:
            warmup_time, estimated_warmup_requests, warmup_hot_ids = estimate_warmup_seconds(
                maxid1=args.maxid1,
                startid1=1,
                requesters=args.requesters,
                coverage=args.warmup_coverage,
                hot_fraction=args.warmup_hot_fraction,
                range_limit=args.warmup_range_limit,
                min_seconds=args.warmup_min_seconds,
                assumed_ops_per_second=args.warmup_assumed_ops_per_second,
            )
        else:
            estimated_warmup_requests = 0
            warmup_hot_ids = max(1, int(math.ceil((args.maxid1 - 1) *
                                                 args.warmup_hot_fraction)))
        fixed_warmup_requests = 0
    else:
        if args.warmup_requests is None:
            fixed_warmup_requests, warmup_hot_ids = estimate_warmup_requests(
                maxid1=args.maxid1,
                startid1=1,
                coverage=args.warmup_coverage,
                hot_fraction=args.warmup_hot_fraction,
                range_limit=args.warmup_range_limit,
                min_requests=args.warmup_min_requests,
            )
        else:
            fixed_warmup_requests = args.warmup_requests
            warmup_hot_ids = max(1, int(math.ceil((args.maxid1 - 1) *
                                                 args.warmup_hot_fraction)))
        warmup_time = 0
        estimated_warmup_requests = fixed_warmup_requests
    with (out / "warmup_plan.txt").open("w", encoding="utf-8") as f:
        f.write(f"warmup_time_seconds={warmup_time}\n")
        f.write(f"fixed_warmup_requests={fixed_warmup_requests}\n")
        f.write(f"estimated_warmup_requests={estimated_warmup_requests}\n")
        f.write(f"estimated_hot_id1_count={warmup_hot_ids}\n")
        f.write(f"coverage_target={args.warmup_coverage}\n")
        f.write(f"hot_fraction={args.warmup_hot_fraction}\n")
        f.write(f"range_limit={args.warmup_range_limit}\n")
        f.write(f"load_random_seed={args.load_random_seed}\n")
        f.write(f"warmup_random_seed={args.warmup_random_seed}\n")
        f.write(f"request_random_seed={args.request_random_seed}\n")
        if args.time_based_warmup:
            f.write("method=official LinkBench time-based randomized warmup\n")
        else:
            f.write("method=official LinkBench fixed-count randomized request warmup\n")
    print(
        "Official LinkBench warmup: "
        f"{warmup_time}s time warmup, {fixed_warmup_requests} fixed requests, "
        f"target hot ids ~{warmup_hot_ids}, estimated requests >= "
        f"{estimated_warmup_requests}"
    )

    subprocess.run([str(BUILD_SCRIPT)], cwd=str(ROOT), check=True)

    rows: list[dict[str, str]] = []
    for variant in args.variants:
      if variant not in VARIANTS:
          raise ValueError(f"Unknown variant {variant}")
      safe = variant.replace("+", "_").replace(" ", "_")
      db_path = out / f"db_{safe}"
      common = {
          "lorckv.db_path": str(db_path),
          "lorckv.native_library": str(NATIVE_LIB),
          "lorckv.disable_auto_compactions": "false",
          "lorckv.disable_wal": "false",
          "lorckv.enable_statistics": "true",
          "load_random_seed": str(args.load_random_seed),
          "startid1": "1",
          "maxid1": str(args.maxid1),
          "loaders": str(args.loaders),
          "requesters": str(args.requesters),
          "load_progress_interval": "50000",
          "req_progress_interval": str(max(10000, args.requests // 4)),
      }
      common.update(VARIANTS[variant])
      if int(common.get("lorckv.range_cache_size", "0")) > 0:
          common["lorckv.max_materialized_range_entries"] = str(
              args.lorc_max_admitted_entries)

      marker = db_path / "LINKBENCH_LOAD_DONE"
      if not args.reuse_load or not marker.exists():
          load_props = dict(common)
          load_props["lorckv.destroy"] = "true"
          load_props["lorckv.create_if_missing"] = "true"
          run_logged(
              java_cmd(load_props, load=True, request=False, csvstats=out / f"{safe}_load.csv"),
              LINKBENCH_HOME,
              out / f"{safe}_load.log",
          )
          marker.touch()

      if not args.time_based_warmup and fixed_warmup_requests > 0:
          warm_props = dict(common)
          warm_props["lorckv.destroy"] = "false"
          warm_props["lorckv.create_if_missing"] = "false"
          warm_props["request_random_seed"] = str(args.warmup_random_seed)
          warm_props["requests"] = str(fixed_warmup_requests)
          warm_props["warmup_time"] = "0"
          warm_props["req_progress_interval"] = str(
              max(10000, fixed_warmup_requests // 4))
          run_logged(
              java_cmd(warm_props, load=False, request=True,
                       csvstats=out / f"{safe}_warmup.csv"),
              LINKBENCH_HOME,
              out / f"{safe}_warmup.log",
          )

      req_props = dict(common)
      req_props["lorckv.destroy"] = "false"
      req_props["lorckv.create_if_missing"] = "false"
      req_props["request_random_seed"] = str(args.request_random_seed)
      req_props["requests"] = str(args.requests)
      req_props["warmup_time"] = str(warmup_time)
      run_logged(
          java_cmd(req_props, load=False, request=True, csvstats=out / f"{safe}_request.csv"),
          LINKBENCH_HOME,
          out / f"{safe}_request.log",
      )
      row = {"variant": variant}
      row.update(parse_log(out / f"{safe}_request.log"))
      rows.append(row)

    keys = ["variant"]
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    with (out / "summary.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {out / 'summary.csv'}")


if __name__ == "__main__":
    main()
