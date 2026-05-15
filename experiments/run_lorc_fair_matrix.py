#!/usr/bin/env python3
"""Run the fair LORC evaluation matrix and generate paper figures.

The matrix enforces a single configured cache budget per system:

  RocksDB        : block cache = budget
  RocksDB+LORC  : range cache = budget, block/blob cache = 0
  BlobDB        : block cache + blob cache = budget
  BlobDB+LORC   : range cache = budget, block/blob cache = 0
  LSbM          : block cache = budget

Every run is wrapped by /usr/bin/time -v and records max RSS. Read-only runs
reuse source databases; workloads with foreground updates copy the source DB.
"""

from __future__ import annotations

import argparse
import csv
import math
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter


ROOT = Path(__file__).resolve().parents[1]
PAPER = ROOT.parent / "68368b2dccae04737d71ce11"
MB = 1024 * 1024
GB = 1024 * MB
DEFAULT_CACHE_BUDGET = 1024 * MB
GENERAL_RECORDCOUNT = 4_000_000
GENERAL_FIELD_LENGTH = 1024
VALUE_SWEEP_BYTES = 1024 * MB


@dataclass(frozen=True)
class Dataset:
    name: str
    recordcount: int
    fieldlength: int
    source_root: Path
    source_tag: str

    def source_path(self, engine: str) -> Path:
        return self.source_root / f"ycsb-{engine}-{self.source_tag}-random"


@dataclass(frozen=True)
class Variant:
    label: str
    engine: str
    db_name: str
    prop_file: Path
    lorc: bool = False
    blobdb: bool = False

    @property
    def key(self) -> str:
        return (
            self.label.lower()
            .replace("+", "_plus_")
            .replace(" ", "_")
            .replace("/", "_")
        )


GENERAL_DATASET = Dataset(
    name="4GB-1KB",
    recordcount=GENERAL_RECORDCOUNT,
    fieldlength=GENERAL_FIELD_LENGTH,
    source_root=ROOT / "db" / "ycsb-source-24B-1KB-4GB-fair-nocomp",
    source_tag="source-24B-1KB-4GB-fair-nocomp",
)

VARIANTS = [
    Variant("RocksDB", "rocksdb", "rocksdb_lorc", Path("rocksdb_lorc/rocksdb.properties")),
    Variant(
        "RocksDB+LORC",
        "rocksdb",
        "rocksdb_lorc",
        Path("rocksdb_lorc/rocksdb_lorc.properties"),
        lorc=True,
    ),
    Variant(
        "BlobDB",
        "blobdb",
        "rocksdb_lorc",
        Path("rocksdb_lorc/blobdb.properties"),
        blobdb=True,
    ),
    Variant(
        "BlobDB+LORC",
        "blobdb",
        "rocksdb_lorc",
        Path("rocksdb_lorc/blobdb_lorc.properties"),
        lorc=True,
        blobdb=True,
    ),
    Variant("LSbM", "lsbm", "lsbm", Path("lsbm/lsbm.properties")),
]


METRIC_RE = re.compile(
    r"\[(?P<op>[A-Z-]+): Count=(?P<count>\d+) Max=(?P<max>[\d.]+) "
    r"Min=(?P<min>[\d.]+) Avg=(?P<avg>[\d.]+) 90=(?P<p90>[\d.]+) "
    r"99=(?P<p99>[\d.]+) 99\.9=(?P<p999>[\d.]+) 99\.99=(?P<p9999>[\d.]+)\]"
)
SIMPLE_RE = re.compile(r"Run (?P<name>[^:]+): (?P<value>[-\d.]+)")
LORC_STATS_RE = re.compile(r"\[LORC_STATS\]\s+(?P<body>.*)")
ROCKSDB_STATS_RE = re.compile(r"\[ROCKSDB_STATS\]\s+(?P<body>.*)")
RSS_RE = re.compile(r"Maximum resident set size \(kbytes\):\s+(?P<rss>\d+)")
FS_IN_RE = re.compile(r"File system inputs:\s+(?P<value>\d+)")
FS_OUT_RE = re.compile(r"File system outputs:\s+(?P<value>\d+)")


def shell_quote(parts: Iterable[str]) -> str:
    return " ".join(subprocess.list2cmdline([p]) for p in parts)


def system_props(variant: Variant, budget: int, *, direct_reads: str) -> dict[str, str]:
    """Return cache and diagnostic properties for a fair run."""
    if variant.engine == "lsbm":
        return {
            "leveldb.cache_size": str(budget),
            "leveldb.destroy": "false",
            "leveldb.compression": "no",
            "leveldb.run_compaction": "true",
            "leveldb.compaction_buffer_trim_interval": "30",
        }

    block_cache = 0
    blob_cache = 0
    range_cache = 0
    if variant.label == "RocksDB":
        block_cache = budget
    elif variant.label == "RocksDB+LORC":
        range_cache = budget
    elif variant.label == "BlobDB":
        block_cache = budget // 4
        blob_cache = budget - block_cache
    elif variant.label == "BlobDB+LORC":
        range_cache = budget
    else:
        raise ValueError(f"Unknown variant: {variant.label}")

    props = {
        "rocksdb.block_cache_size": str(block_cache),
        "rocksdb.blob_cache_size": str(blob_cache),
        "rocksdb.range_cache_size": str(range_cache),
        "rocksdb.compression": "no",
        "rocksdb.use_direct_reads": direct_reads,
        "rocksdb.enable_statistics": "true",
        "rocksdb.range_cache_physical_type": "continuous",
        "rocksdb.range_cache_victim_policy": "boundary_lru",
        "rocksdb.lorc_enable_stats": "true" if variant.lorc else "false",
    }
    return props


def read_only_props(variant: Variant) -> dict[str, str]:
    if variant.engine == "lsbm":
        return {
            "leveldb.destroy": "false",
            "leveldb.run_compaction": "false",
            "leveldb.compaction_buffer_trim_interval": "1000000000",
        }
    return {
        "rocksdb.disable_auto_compactions": "true",
        "rocksdb.create_if_missing": "false",
        "rocksdb.destroy": "false",
        "rocksdb.read_only": "true",
    }


def load_props(variant: Variant, budget: int) -> dict[str, str]:
    if variant.engine == "lsbm":
        return {
            "leveldb.cache_size": str(min(budget, 256 * MB)),
            "leveldb.destroy": "true",
            "leveldb.compression": "no",
            "leveldb.run_compaction": "true",
            "leveldb.compaction_buffer_trim_interval": "30",
        }
    props = system_props(variant, min(budget, 256 * MB), direct_reads="false")
    props.update(
        {
            "rocksdb.create_if_missing": "true",
            "rocksdb.destroy": "true",
            "rocksdb.compression": "no",
            "rocksdb.disable_auto_compactions": "false",
            "rocksdb.read_only": "false",
            "rocksdb.range_cache_size": "0",
            "rocksdb.lorc_enable_stats": "false",
        }
    )
    if variant.engine == "blobdb":
        props["rocksdb.blob_cache_size"] = str(min(budget, 256 * MB))
    return props


def workload_text(
    *,
    dataset: Dataset,
    operationcount: int,
    warmup_ratio: float,
    hot_data_ratio: float,
    readproportion: float,
    updateproportion: float,
    scanproportion: float,
    requestdistribution: str,
    scan_length: int,
) -> str:
    return f"""# Generated by experiments/run_lorc_fair_matrix.py
recordcount={dataset.recordcount}
operationcount={operationcount}
hot_data_ratio={hot_data_ratio}
warmup_ratio={warmup_ratio:.8f}
insertorder=random

workload=com.yahoo.ycsb.workloads.CoreWorkload

zeropadding=24
fieldcount=1
fieldlength={dataset.fieldlength}

readallfields=true
writeallfields=true

readproportion={readproportion}
updateproportion={updateproportion}
scanproportion={scanproportion}
insertproportion=0

requestdistribution={requestdistribution}

minscanlength={scan_length}
maxscanlength={scan_length}
scanlengthdistribution=uniform
"""


def load_workload_text(dataset: Dataset) -> str:
    return f"""# Generated by experiments/run_lorc_fair_matrix.py
recordcount={dataset.recordcount}
operationcount={dataset.recordcount}
insertorder=random

workload=com.yahoo.ycsb.workloads.CoreWorkload

zeropadding=24
fieldcount=1
fieldlength={dataset.fieldlength}

readallfields=true
writeallfields=true

readproportion=0
updateproportion=0
scanproportion=0
insertproportion=1

requestdistribution=zipfian

minscanlength=20
maxscanlength=20
scanlengthdistribution=uniform
"""


def warmup_ops_for(
    dataset: Dataset,
    hot_ratio: float,
    scan_length: int,
    *,
    min_warmup_ops: int,
    coverage_factor: float,
) -> int:
    hot_records = max(1, int(dataset.recordcount * hot_ratio))
    # Warmup remains randomized by the workload generator. The factor controls
    # expected random scan coverage; e.g., factor 20 is effectively full for
    # the hot regions used in the short-scan experiments.
    coverage_ops = math.ceil(coverage_factor * hot_records / max(scan_length, 1))
    return max(min_warmup_ops, coverage_ops)


def write_workload(
    out_dir: Path,
    name: str,
    *,
    dataset: Dataset,
    measured_ops: int,
    hot_ratio: float,
    read_prop: float,
    update_prop: float,
    scan_prop: float,
    requestdistribution: str,
    scan_length: int,
    min_warmup_ops: int,
    coverage_factor: float,
) -> tuple[Path, int, int, float]:
    warmup_ops = warmup_ops_for(
        dataset,
        hot_ratio,
        scan_length,
        min_warmup_ops=min_warmup_ops,
        coverage_factor=coverage_factor,
    )
    total_ops = warmup_ops + measured_ops
    warmup_ratio = warmup_ops / total_ops
    path = out_dir / f"{name}.properties"
    path.write_text(
        workload_text(
            dataset=dataset,
            operationcount=total_ops,
            warmup_ratio=warmup_ratio,
            hot_data_ratio=hot_ratio,
            readproportion=read_prop,
            updateproportion=update_prop,
            scanproportion=scan_prop,
            requestdistribution=requestdistribution,
            scan_length=scan_length,
        )
    )
    return path, total_ops, warmup_ops, warmup_ratio


def parse_log(text: str) -> dict[str, float | int | str]:
    out: dict[str, float | int | str] = {}
    for match in METRIC_RE.finditer(text):
        op = match.group("op").lower().replace("-", "_")
        values = {
            "count": int(match.group("count")),
            "max_us": float(match.group("max")),
            "min_us": float(match.group("min")),
            "avg_us": float(match.group("avg")),
            "p90_us": float(match.group("p90")),
            "p99_us": float(match.group("p99")),
            "p999_us": float(match.group("p999")),
            "p9999_us": float(match.group("p9999")),
        }
        for key, value in values.items():
            out[f"{op}_{key}"] = value

    for match in SIMPLE_RE.finditer(text):
        name = (
            match.group("name")
            .strip()
            .lower()
            .replace(" ", "_")
            .replace("(", "")
            .replace(")", "")
        )
        out[name] = float(match.group("value"))

    for match in LORC_STATS_RE.finditer(text):
        for token in match.group("body").split():
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            try:
                out[f"lorc_{key}"] = float(value)
            except ValueError:
                out[f"lorc_{key}"] = value

    for match in ROCKSDB_STATS_RE.finditer(text):
        for token in match.group("body").split():
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            try:
                out[f"rocksdb_{key}"] = float(value)
            except ValueError:
                out[f"rocksdb_{key}"] = value

    if "measured_throughputops/sec" in out:
        out["throughputops/sec"] = out["measured_throughputops/sec"]
    return out


def parse_time_file(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    text = path.read_text(errors="replace")
    result: dict[str, int] = {}
    match = RSS_RE.search(text)
    if match:
        result["max_rss_kb"] = int(match.group("rss"))
    match = FS_IN_RE.search(text)
    if match:
        result["fs_inputs"] = int(match.group("value"))
    match = FS_OUT_RE.search(text)
    if match:
        result["fs_outputs"] = int(match.group("value"))
    return result


def run_ycsb(
    *,
    mode: str,
    variant: Variant,
    workload: Path,
    db_path: Path,
    run_dir: Path,
    log_name: str,
    props: dict[str, str],
    threads: int,
    timeout: int,
) -> tuple[int, str, dict[str, int]]:
    log_path = run_dir / f"{log_name}.log"
    time_path = run_dir / f"{log_name}.time"
    cmd = [
        "/usr/bin/time",
        "-v",
        "-o",
        str(time_path),
        "./ycsb",
        f"-{mode}",
        "-db",
        variant.db_name,
        "-P",
        str(workload),
        "-P",
        str(ROOT / variant.prop_file),
    ]

    path_prop = "leveldb.dbname" if variant.engine == "lsbm" else "rocksdb.dbname"
    all_props = dict(props)
    all_props[path_prop] = str(db_path)
    for key, value in all_props.items():
        cmd.extend(["-p", f"{key}={value}"])
    cmd.extend(["-threads", str(threads), "-s"])

    header = [
        f"===== START {log_name} {datetime.now(timezone.utc).isoformat()} =====",
        f"Command: {shell_quote(cmd)}",
        f"db_path={db_path}",
        "",
    ]
    print(f"[{mode}] {log_name}", flush=True)
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    text = (
        "\n".join(header)
        + proc.stdout
        + f"\n===== END {log_name} rc={proc.returncode} "
        + f"{datetime.now(timezone.utc).isoformat()} =====\n"
    )
    log_path.write_text(text, errors="replace")
    return proc.returncode, text, parse_time_file(time_path)


def value_dataset(value_size: int) -> Dataset:
    tag = f"source-24B-{value_size}B-1GB-fair-nocomp"
    recordcount = max(32_768, VALUE_SWEEP_BYTES // value_size)
    return Dataset(
        name=f"1GB-{value_size}B",
        recordcount=int(recordcount),
        fieldlength=value_size,
        source_root=ROOT / "db" / f"ycsb-{tag}",
        source_tag=tag,
    )


def prepare_sources(
    datasets: list[Dataset],
    *,
    budget: int,
    out_dir: Path,
    reload: bool,
) -> None:
    workload_dir = out_dir / "prepare_workloads"
    workload_dir.mkdir(parents=True, exist_ok=True)
    for dataset in datasets:
        workload = workload_dir / f"load_{dataset.name}.properties"
        workload.write_text(load_workload_text(dataset))
        dataset.source_root.mkdir(parents=True, exist_ok=True)
        for engine in ("rocksdb", "blobdb", "lsbm"):
            source_path = dataset.source_path(engine)
            if source_path.exists() and not reload:
                continue
            if source_path.exists():
                shutil.rmtree(source_path)
            if engine == "rocksdb":
                variant = VARIANTS[0]
            elif engine == "blobdb":
                variant = VARIANTS[2]
            else:
                variant = VARIANTS[4]
            props = load_props(variant, budget)
            rc, text, _ = run_ycsb(
                mode="load",
                variant=variant,
                workload=workload,
                db_path=source_path,
                run_dir=out_dir,
                log_name=f"load__{dataset.name}__{engine}",
                props=props,
                threads=1,
                timeout=7200,
            )
            if rc != 0:
                raise RuntimeError(f"Load failed for {dataset.name}/{engine}\n{text[-2000:]}")


def make_plan(
    out_dir: Path,
    *,
    budget: int,
    measured_ops: int,
    direct_reads: str,
) -> list[dict]:
    workload_dir = out_dir / "workloads"
    workload_dir.mkdir(parents=True, exist_ok=True)
    plan: list[dict] = []

    def add_suite(
        *,
        suite: str,
        x_value: str,
        x_label: str,
        dataset: Dataset,
        scan_length: int = 20,
        hot_ratio: float = 0.05,
        read_prop: float = 0.0,
        update_prop: float = 0.0,
        scan_prop: float = 1.0,
        requestdistribution: str = "zipfian",
        min_warmup_ops: int = 20_000,
        coverage_factor: float = 20.0,
        timeout: int = 1200,
    ) -> None:
        name = (
            f"{suite}_{x_value}_{dataset.name}_sl{scan_length}_hot{hot_ratio:g}_"
            f"scan{scan_prop:g}_upd{update_prop:g}"
        ).replace(".", "p").replace("/", "_")
        workload, op_count, warmup_ops, warmup_ratio = write_workload(
            workload_dir,
            name,
            dataset=dataset,
            measured_ops=measured_ops,
            hot_ratio=hot_ratio,
            read_prop=read_prop,
            update_prop=update_prop,
            scan_prop=scan_prop,
            requestdistribution=requestdistribution,
            scan_length=scan_length,
            min_warmup_ops=min_warmup_ops,
            coverage_factor=coverage_factor,
        )
        read_only = update_prop == 0.0
        for variant in VARIANTS:
            plan.append(
                {
                    "suite": suite,
                    "x_value": x_value,
                    "x_label": x_label,
                    "dataset": dataset,
                    "workload": workload,
                    "variant": variant,
                    "read_only": read_only,
                    "operationcount": op_count,
                    "warmup_ops": warmup_ops,
                    "warmup_ratio": warmup_ratio,
                    "scan_length": scan_length,
                    "hot_ratio": hot_ratio,
                    "read_prop": read_prop,
                    "update_prop": update_prop,
                    "scan_prop": scan_prop,
                    "requestdistribution": requestdistribution,
                    "direct_reads": direct_reads,
                    "timeout": timeout,
                }
            )

    for scan_length in [5, 10, 20, 50, 100]:
        add_suite(
            suite="scan_length",
            x_value=str(scan_length),
            x_label=str(scan_length),
            dataset=GENERAL_DATASET,
            scan_length=scan_length,
            min_warmup_ops=40_000,
            coverage_factor=20.0,
        )

    for hot_ratio in [0.01, 0.02, 0.05, 0.10, 0.20]:
        add_suite(
            suite="hot_ratio",
            x_value=f"{hot_ratio:.2f}",
            x_label=f"{int(hot_ratio * 100)}%",
            dataset=GENERAL_DATASET,
            hot_ratio=hot_ratio,
            min_warmup_ops=40_000,
            coverage_factor=20.0,
        )

    for value_size in [256, 512, 1024, 4096, 8192]:
        dataset = value_dataset(value_size)
        add_suite(
            suite="value_size",
            x_value=str(value_size),
            x_label=f"{value_size // 1024}KB" if value_size >= 1024 else f"{value_size}B",
            dataset=dataset,
            scan_length=50,
            min_warmup_ops=30_000,
            coverage_factor=20.0,
            timeout=1800,
        )

    workload_cases = [
        ("scan100", "100% scan", 0.0, 0.0, 1.0),
        ("scan50_read50", "50/50", 0.5, 0.0, 0.5),
        ("scan10_read90", "10/90", 0.9, 0.0, 0.1),
        ("upd5_scan50", "5% upd", 0.45, 0.05, 0.5),
    ]
    for x_value, x_label, read_prop, update_prop, scan_prop in workload_cases:
        add_suite(
            suite="workload",
            x_value=x_value,
            x_label=x_label,
            dataset=GENERAL_DATASET,
            read_prop=read_prop,
            update_prop=update_prop,
            scan_prop=scan_prop,
            min_warmup_ops=40_000,
            coverage_factor=20.0,
            timeout=1800 if update_prop > 0 else 1200,
        )

    return plan


def execute_plan(
    plan: list[dict],
    *,
    out_dir: Path,
    budget: int,
    max_runs: int | None,
) -> list[dict]:
    rows: list[dict] = []
    if max_runs is not None:
        plan = plan[:max_runs]

    for idx, item in enumerate(plan, start=1):
        variant: Variant = item["variant"]
        dataset: Dataset = item["dataset"]
        source = dataset.source_path(variant.engine)
        if not source.exists():
            raise FileNotFoundError(f"Missing source DB for {variant.label}: {source}")

        db_path = source
        work_db: Path | None = None
        if not item["read_only"]:
            work_db = out_dir / "workdb" / f"{idx:03d}__{item['suite']}__{item['x_value']}__{variant.key}"
            if work_db.exists():
                shutil.rmtree(work_db)
            work_db.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, work_db)
            db_path = work_db

        props = system_props(variant, budget, direct_reads=item["direct_reads"])
        if item["read_only"]:
            props.update(read_only_props(variant))

        block_cache = int(props.get("rocksdb.block_cache_size", "0"))
        blob_cache = int(props.get("rocksdb.blob_cache_size", "0"))
        range_cache = int(props.get("rocksdb.range_cache_size", "0"))
        lsbm_cache = int(props.get("leveldb.cache_size", "0"))
        configured_total = block_cache + blob_cache + range_cache + lsbm_cache

        log_name = f"{idx:03d}__{item['suite']}__{item['x_value']}__{variant.key}"
        try:
            rc, text, time_info = run_ycsb(
                mode="run",
                variant=variant,
                workload=item["workload"],
                db_path=db_path,
                run_dir=out_dir,
                log_name=log_name,
                props=props,
                threads=1,
                timeout=item["timeout"],
            )
        finally:
            if work_db is not None and work_db.exists():
                shutil.rmtree(work_db)

        row: dict[str, str | int | float] = {
            "run_index": idx,
            "suite": item["suite"],
            "x_value": item["x_value"],
            "x_label": item["x_label"],
            "dataset": dataset.name,
            "recordcount": dataset.recordcount,
            "fieldlength": dataset.fieldlength,
            "variant": variant.label,
            "variant_key": variant.key,
            "engine": variant.engine,
            "returncode": rc,
            "read_only": int(item["read_only"]),
            "scan_length": item["scan_length"],
            "hot_ratio": item["hot_ratio"],
            "read_prop": item["read_prop"],
            "update_prop": item["update_prop"],
            "scan_prop": item["scan_prop"],
            "requestdistribution": item["requestdistribution"],
            "operationcount": item["operationcount"],
            "warmup_ops": item["warmup_ops"],
            "warmup_ratio": item["warmup_ratio"],
            "direct_reads": item["direct_reads"],
            "cache_budget_bytes": budget,
            "block_cache_bytes": block_cache,
            "blob_cache_bytes": blob_cache,
            "range_cache_bytes": range_cache,
            "lsbm_cache_bytes": lsbm_cache,
            "configured_total_cache_bytes": configured_total,
            "log": str(out_dir / f"{log_name}.log"),
        }
        row.update(parse_log(text))
        row.update(time_info)
        if configured_total > 0 and "max_rss_kb" in row:
            row["rss_to_cache_budget"] = (int(row["max_rss_kb"]) * 1024) / configured_total
        rows.append(row)

        if rc != 0:
            write_summary(rows, out_dir / "summary_partial.csv")
            raise RuntimeError(f"Run failed: {log_name}\n{text[-2500:]}")

    return rows


SUMMARY_KEYS = [
    "run_index",
    "suite",
    "x_value",
    "x_label",
    "dataset",
    "recordcount",
    "fieldlength",
    "variant",
    "engine",
    "returncode",
    "read_only",
    "throughputops/sec",
    "scan_count",
    "scan_avg_us",
    "scan_p99_us",
    "read_count",
    "read_avg_us",
    "read_p99_us",
    "update_count",
    "update_avg_us",
    "update_p99_us",
    "scan_length",
    "hot_ratio",
    "read_prop",
    "update_prop",
    "scan_prop",
    "operationcount",
    "warmup_ops",
    "direct_reads",
    "cache_budget_bytes",
    "block_cache_bytes",
    "blob_cache_bytes",
    "range_cache_bytes",
    "lsbm_cache_bytes",
    "configured_total_cache_bytes",
    "max_rss_kb",
    "rss_to_cache_budget",
    "fs_inputs",
    "fs_outputs",
    "lorc_current_size",
    "lorc_capacity",
    "lorc_total_range_length",
    "lorc_materialized_entries",
    "lorc_materialized_key_bytes",
    "lorc_materialized_value_bytes",
    "lorc_full_hit_rate",
    "lorc_hit_size_rate",
    "rocksdb_block_data_hit",
    "rocksdb_block_data_miss",
    "rocksdb_block_bytes_read",
    "rocksdb_iter_bytes_read",
    "rocksdb_db_seek",
    "rocksdb_db_next",
    "rocksdb_blob_cache_hit",
    "rocksdb_blob_cache_miss",
    "rocksdb_blob_cache_bytes_read",
    "rocksdb_blob_file_bytes_read",
    "rocksdb_blob_next",
    "log",
]


def write_summary(rows: list[dict], path: Path) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_KEYS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open() as f:
        return list(csv.DictReader(f))


def as_float(row: dict | None, key: str, default: float = float("nan")) -> float:
    if row is None:
        return default
    try:
        value = row.get(key, "")
        if value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


COLORS = {
    "RocksDB": "#4E79A7",
    "RocksDB+LORC": "#F28E2B",
    "BlobDB": "#59A14F",
    "BlobDB+LORC": "#E15759",
    "LSbM": "#8A60B0",
}
HATCHES = {
    "RocksDB": "",
    "RocksDB+LORC": "//",
    "BlobDB": "",
    "BlobDB+LORC": "//",
    "LSbM": "",
}
VARIANT_ORDER = [v.label for v in VARIANTS]


def setup_style() -> None:
    plt.rcParams.update(
        {
            "figure.dpi": 160,
            "savefig.dpi": 300,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "font.size": 8,
            "axes.titlesize": 8.5,
            "axes.labelsize": 8,
            "legend.fontsize": 7.2,
            "xtick.labelsize": 7.2,
            "ytick.labelsize": 7.2,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )


def grouped_bars(
    ax,
    rows: list[dict],
    *,
    metric: str,
    transform=lambda x: x,
    ylabel: str,
    suite: str,
    title: str,
    log: bool = False,
) -> None:
    suite_rows = [r for r in rows if r["suite"] == suite]
    groups: list[tuple[str, str]] = []
    for row in suite_rows:
        pair = (row["x_value"], row["x_label"])
        if pair not in groups:
            groups.append(pair)
    variants = [v for v in VARIANT_ORDER if any(r["variant"] == v for r in suite_rows)]
    width = min(0.15, 0.80 / max(len(variants), 1))
    centers = list(range(len(groups)))

    for i, variant in enumerate(variants):
        offset = (i - (len(variants) - 1) / 2) * width
        values = []
        for x_value, _ in groups:
            row = next(
                (r for r in suite_rows if r["variant"] == variant and r["x_value"] == x_value),
                None,
            )
            values.append(transform(as_float(row, metric)))
        ax.bar(
            [c + offset for c in centers],
            values,
            width=width,
            color=COLORS[variant],
            edgecolor="#262626",
            linewidth=0.35,
            hatch=HATCHES[variant],
            label=variant,
        )

    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.set_xticks(centers)
    ax.set_xticklabels([label for _, label in groups], rotation=0)
    if log:
        ax.set_yscale("log")
    ax.grid(axis="y", color="#e1e1e1", linewidth=0.55)
    ax.set_axisbelow(True)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:,.0f}"))


def make_metric_row(rows: list[dict], *, suite: str, out_path: Path, title_prefix: str) -> None:
    setup_style()
    fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.2), constrained_layout=True)
    grouped_bars(
        axes[0],
        rows,
        metric="throughputops/sec",
        transform=lambda v: v / 1000.0,
        ylabel="Kops/s",
        suite=suite,
        title=f"{title_prefix}: throughput",
    )
    grouped_bars(
        axes[1],
        rows,
        metric="scan_avg_us",
        ylabel="avg scan (us)",
        suite=suite,
        title=f"{title_prefix}: average",
    )
    grouped_bars(
        axes[2],
        rows,
        metric="scan_p99_us",
        ylabel="p99 scan (us)",
        suite=suite,
        title=f"{title_prefix}: p99",
    )
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        loc="upper center",
        ncol=5,
        bbox_to_anchor=(0.5, 1.12),
        frameon=False,
        columnspacing=1.0,
        handlelength=1.2,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)


def make_memory_figure(rows: list[dict], out_path: Path) -> None:
    setup_style()
    fig, ax = plt.subplots(figsize=(7.2, 2.35), constrained_layout=True)
    main_rows = [
        r
        for r in rows
        if r["suite"] == "scan_length" and r["x_value"] == "20"
    ]
    labels = [r["variant"] for r in main_rows]
    configured = [as_float(r, "configured_total_cache_bytes") / MB for r in main_rows]
    rss = [as_float(r, "max_rss_kb") / 1024.0 for r in main_rows]
    centers = list(range(len(labels)))
    width = 0.34
    ax.bar(
        [c - width / 2 for c in centers],
        configured,
        width=width,
        color="#b7b7b7",
        edgecolor="#333333",
        linewidth=0.35,
        label="configured cache",
    )
    ax.bar(
        [c + width / 2 for c in centers],
        rss,
        width=width,
        color=[COLORS.get(label, "#777777") for label in labels],
        edgecolor="#333333",
        linewidth=0.35,
        label="max RSS",
    )
    ax.set_xticks(centers)
    ax.set_xticklabels(labels, rotation=14, ha="right")
    ax.set_ylabel("MB")
    ax.grid(axis="y", color="#e1e1e1", linewidth=0.55)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, ncol=2, loc="upper left")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)


def make_figures(summary: Path, figure_dir: Path) -> None:
    rows = load_rows(summary)
    make_metric_row(
        rows,
        suite="scan_length",
        out_path=figure_dir / "eval_fair_scan_length.pdf",
        title_prefix="scan length",
    )
    make_metric_row(
        rows,
        suite="hot_ratio",
        out_path=figure_dir / "eval_fair_hot_ratio.pdf",
        title_prefix="hot region",
    )
    make_metric_row(
        rows,
        suite="value_size",
        out_path=figure_dir / "eval_fair_value_size.pdf",
        title_prefix="value size",
    )
    make_metric_row(
        rows,
        suite="workload",
        out_path=figure_dir / "eval_fair_workload.pdf",
        title_prefix="workload",
    )


def write_manifest(out_dir: Path, plan: list[dict], budget: int) -> None:
    manifest = out_dir / "manifest.txt"
    lines = [
        f"generated_at={datetime.now(timezone.utc).isoformat()}",
        f"cache_budget_bytes={budget}",
        f"runs={len(plan)}",
        "",
        "Cache allocation:",
        "RocksDB:        block=budget",
        "RocksDB+LORC:  range=budget, block=0, blob=0",
        "BlobDB:        block=budget/4, blob=3*budget/4",
        "BlobDB+LORC:   range=budget, block=0, blob=0",
        "LSbM:          block=budget",
        "Compression:   disabled for RocksDB, BlobDB, and LSbM",
        "Warmup:        two boundary scans, then randomized scan warmup from the configured request distribution",
        "",
        "Plan:",
    ]
    for idx, item in enumerate(plan, start=1):
        lines.append(
            f"{idx:03d} suite={item['suite']} x={item['x_value']} "
            f"variant={item['variant'].label} dataset={item['dataset'].name} "
            f"scan_length={item['scan_length']} hot_ratio={item['hot_ratio']} "
            f"warmup_ops={item['warmup_ops']} read_only={item['read_only']}"
        )
    manifest.write_text("\n".join(lines) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None, help="output run directory")
    parser.add_argument("--summary", type=Path, help="existing summary CSV to plot")
    parser.add_argument("--budget-mb", type=int, default=DEFAULT_CACHE_BUDGET // MB)
    parser.add_argument("--measured-ops", type=int, default=50_000)
    parser.add_argument("--direct-reads", choices=["true", "false"], default="false")
    parser.add_argument("--reload-value-sources", action="store_true")
    parser.add_argument(
        "--reload-general-source",
        action="store_true",
        help="rebuild the 4GB source databases with the current controlled options",
    )
    parser.add_argument(
        "--reload-sources",
        action="store_true",
        help="rebuild both the 4GB source databases and value-size sweep sources",
    )
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--plot-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-runs", type=int, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    budget = args.budget_mb * MB
    out_dir = args.out or (ROOT / "result" / "log" / f"lorc_fair_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    figure_dir = PAPER / "figures" / "experiments"

    if args.plot_only:
        if not args.summary:
            raise SystemExit("--plot-only requires --summary")
        make_figures(args.summary, figure_dir)
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    plan = make_plan(
        out_dir,
        budget=budget,
        measured_ops=args.measured_ops,
        direct_reads=args.direct_reads,
    )
    write_manifest(out_dir, plan, budget)

    if args.dry_run:
        print(f"Prepared plan with {len(plan)} runs in {out_dir}")
        return 0

    active_plan = plan[: args.max_runs] if args.max_runs is not None else plan
    needs_general_source = (
        args.prepare_only
        or args.reload_sources
        or args.reload_general_source
        or any(item["dataset"].name == GENERAL_DATASET.name for item in active_plan)
    )
    general_missing = [
        GENERAL_DATASET
        for engine in ("rocksdb", "blobdb", "lsbm")
        if not GENERAL_DATASET.source_path(engine).exists()
    ]
    if needs_general_source and (general_missing or args.reload_sources or args.reload_general_source):
        prepare_sources(
            [GENERAL_DATASET],
            budget=budget,
            out_dir=out_dir,
            reload=args.reload_sources or args.reload_general_source,
        )

    needs_value_sources = (
        args.prepare_only
        or args.reload_sources
        or args.reload_value_sources
        or any(item["suite"] == "value_size" for item in active_plan)
    )
    value_datasets = [value_dataset(v) for v in [256, 512, 1024, 4096, 8192]]
    missing = []
    if needs_value_sources:
        missing = [
            dataset
            for dataset in value_datasets
            for engine in ("rocksdb", "blobdb", "lsbm")
            if not dataset.source_path(engine).exists()
        ]
    if needs_value_sources and (missing or args.reload_sources or args.reload_value_sources):
        unique = []
        seen = set()
        for dataset in value_datasets:
            if dataset.name not in seen:
                seen.add(dataset.name)
                unique.append(dataset)
        prepare_sources(
            unique,
            budget=budget,
            out_dir=out_dir,
            reload=args.reload_sources or args.reload_value_sources,
        )

    if args.prepare_only:
        return 0

    rows = execute_plan(plan, out_dir=out_dir, budget=budget, max_runs=args.max_runs)
    summary = out_dir / "summary.csv"
    write_summary(rows, summary)
    make_figures(summary, figure_dir)
    print(f"summary={summary}")
    print(f"figures={figure_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
