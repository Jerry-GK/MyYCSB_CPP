#!/usr/bin/env python3
"""Run one YCSB-backed LORC experiment from a JSON config.

The script intentionally has one control path for all five systems.  It can
build the requested source database with random insert order, run scan warmup
with the same Zipfian start-key distribution, then run the measured YCSB
scan/update workload while collecting throughput, latency, and peak RSS.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
import zlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import run_lorc_fair_matrix as fair


ROOT = fair.ROOT
GB = fair.GB
MB = fair.MB

DEFAULT_CONFIG = ROOT / "experiments" / "configs" / "lorc_single_test_default.json"
OUT_ROOT = ROOT / "result" / "single_config"

SYSTEM_ALIASES = {
    "rocksdb": "RocksDB",
    "rocks": "RocksDB",
    "rocksdb_lorc": "RocksDB+LORC",
    "rocksdb+lorc": "RocksDB+LORC",
    "rocksdb-plus-lorc": "RocksDB+LORC",
    "blobdb": "BlobDB",
    "blob": "BlobDB",
    "blobdb_lorc": "BlobDB+LORC",
    "blobdb+lorc": "BlobDB+LORC",
    "blobdb-plus-lorc": "BlobDB+LORC",
    "lsbm": "LSbM",
    "lsbm-tree": "LSbM",
}

VALID_LRU = {"boundary_lru", "physical_lru", "shortest_range"}


@dataclass(frozen=True)
class SingleConfig:
    total_data_gb: float
    cache_data_gb: float
    key_size: int
    value_size: int
    scan_length: int
    zipfian_const: float
    update_ratio: float
    threads: int
    lru_policy: str
    build_source: bool
    enable_compaction: bool
    system: str
    directio: bool
    measured_operations: int
    warmup_coverage: float
    min_warmup_operations: int
    max_warmup_operations: int
    request_distribution: str
    output_dir: str | None


def load_config(path: Path) -> SingleConfig:
    raw = json.loads(path.read_text())
    cfg = SingleConfig(
        total_data_gb=float(raw.get("total_data_gb", 4.0)),
        cache_data_gb=float(raw.get("cache_data_gb", 1.0)),
        key_size=int(raw.get("key_size", 24)),
        value_size=int(raw.get("value_size", 1024)),
        scan_length=int(raw.get("scan_length", 20)),
        zipfian_const=float(raw.get("zipfian_const", 0.99)),
        update_ratio=float(raw.get("update_ratio", 0.0)),
        threads=int(raw.get("threads", 1)),
        lru_policy=str(raw.get("lru_policy", "boundary_lru")),
        build_source=bool(raw.get("build_source", True)),
        enable_compaction=bool(raw.get("enable_compaction", False)),
        system=str(raw.get("system", "rocksdb_lorc")),
        directio=bool(raw.get("directio", False)),
        measured_operations=int(raw.get("measured_operations", 30_000)),
        warmup_coverage=float(raw.get("warmup_coverage", 4.0)),
        min_warmup_operations=int(raw.get("min_warmup_operations", 20_000)),
        max_warmup_operations=int(raw.get("max_warmup_operations", 1_000_000)),
        request_distribution=str(raw.get("request_distribution", "orderedzipfian")),
        output_dir=raw.get("output_dir"),
    )
    validate_config(cfg)
    return cfg


def validate_config(cfg: SingleConfig) -> None:
    if cfg.total_data_gb <= 0:
        raise ValueError("total_data_gb must be positive")
    if cfg.cache_data_gb <= 0:
        raise ValueError("cache_data_gb must be positive")
    if cfg.key_size <= 0:
        raise ValueError("key_size must be positive")
    if cfg.value_size <= 0:
        raise ValueError("value_size must be positive")
    if cfg.scan_length <= 0:
        raise ValueError("scan_length must be positive")
    if not (0.0 <= cfg.update_ratio <= 1.0):
        raise ValueError("update_ratio must be in [0, 1]")
    if cfg.threads <= 0:
        raise ValueError("threads must be positive")
    if cfg.lru_policy not in VALID_LRU:
        raise ValueError(f"lru_policy must be one of {sorted(VALID_LRU)}")
    if cfg.measured_operations <= 0:
        raise ValueError("measured_operations must be positive")
    if cfg.warmup_coverage < 0:
        raise ValueError("warmup_coverage must be non-negative")
    if cfg.request_distribution not in {"orderedzipfian", "zipfian"}:
        raise ValueError("request_distribution must be orderedzipfian or zipfian")
    canonical = SYSTEM_ALIASES.get(cfg.system.lower())
    if canonical is None:
        raise ValueError(
            "system must be one of: rocksdb, rocksdb_lorc, blobdb, "
            "blobdb_lorc, lsbm"
        )


def canonical_system(name: str) -> str:
    return SYSTEM_ALIASES[name.lower()]


def variant_for(cfg: SingleConfig) -> fair.Variant:
    label = canonical_system(cfg.system)
    for variant in fair.VARIANTS:
        if variant.label == label:
            return variant
    raise RuntimeError(f"variant not found: {label}")


def load_variant_for(engine: str) -> fair.Variant:
    if engine == "rocksdb":
        return fair.VARIANTS[0]
    if engine == "blobdb":
        return fair.VARIANTS[2]
    if engine == "lsbm":
        return fair.VARIANTS[4]
    raise ValueError(f"unknown engine: {engine}")


def record_count(cfg: SingleConfig) -> int:
    total_bytes = int(cfg.total_data_gb * GB)
    per_record = cfg.key_size + cfg.value_size
    return max(1, total_bytes // per_record)


def cache_bytes(cfg: SingleConfig) -> int:
    return int(cfg.cache_data_gb * GB)


def dataset_for(cfg: SingleConfig) -> fair.Dataset:
    records = record_count(cfg)
    tag = (
        f"single-{cfg.total_data_gb:g}GB-key{cfg.key_size}B-"
        f"value{cfg.value_size}B"
    ).replace(".", "p")
    return fair.Dataset(
        name=f"{cfg.total_data_gb:g}GB-{cfg.value_size}B",
        recordcount=records,
        fieldlength=cfg.value_size,
        source_root=ROOT / "db" / "single-config" / tag,
        source_tag=tag,
    )


def warmup_ops_for(cfg: SingleConfig) -> int:
    cache_records = max(1, cache_bytes(cfg) // (cfg.key_size + cfg.value_size))
    coverage_ops = math.ceil(
        cfg.warmup_coverage * cache_records / max(cfg.scan_length, 1)
    )
    return min(
        cfg.max_warmup_operations,
        max(cfg.min_warmup_operations, coverage_ops),
    )


def workload_text(
    *,
    cfg: SingleConfig,
    dataset: fair.Dataset,
    operationcount: int,
    warmup_ratio: float,
    warmup_operation: str,
) -> str:
    scan_ratio = 1.0 - cfg.update_ratio
    return f"""# Generated by experiments/run_lorc_single_config.py
recordcount={dataset.recordcount}
operationcount={operationcount}
warmup_ratio={warmup_ratio:.8f}
warmupoperation={warmup_operation}
warmupboundary=false
insertorder=random

workload=com.yahoo.ycsb.workloads.CoreWorkload

zeropadding={cfg.key_size}
fieldcount=1
fieldlength={cfg.value_size}

readallfields=true
writeallfields=true

readproportion=0
updateproportion={cfg.update_ratio:.8f}
scanproportion={scan_ratio:.8f}
insertproportion=0

requestdistribution={cfg.request_distribution}
zipfian_const={cfg.zipfian_const:.8f}

minscanlength={cfg.scan_length}
maxscanlength={cfg.scan_length}
warmupscanlength={cfg.scan_length}
scanlengthdistribution=uniform
"""


def load_workload_text(*, cfg: SingleConfig, dataset: fair.Dataset) -> str:
    return f"""# Generated by experiments/run_lorc_single_config.py
recordcount={dataset.recordcount}
operationcount={dataset.recordcount}
insertorder=random

workload=com.yahoo.ycsb.workloads.CoreWorkload

zeropadding={cfg.key_size}
fieldcount=1
fieldlength={cfg.value_size}

readallfields=true
writeallfields=true

readproportion=0
updateproportion=0
scanproportion=0
insertproportion=1

requestdistribution={cfg.request_distribution}
zipfian_const={cfg.zipfian_const:.8f}

minscanlength={cfg.scan_length}
maxscanlength={cfg.scan_length}
scanlengthdistribution=uniform
"""


def system_props(cfg: SingleConfig, variant: fair.Variant) -> dict[str, str]:
    props = fair.system_props(
        variant, cache_bytes(cfg), direct_reads=str(cfg.directio).lower()
    )
    if variant.lorc:
        props["rocksdb.range_cache_victim_policy"] = cfg.lru_policy
        props["rocksdb.lorc_point_expansion_entries"] = "0"
    if variant.label == "BlobDB+LORC" and cfg.value_size < 512:
        props["rocksdb.lorc_value_separation_aware"] = "false"
        props["rocksdb.lorc_bypass_lower_cache_on_refill"] = "false"
        props["rocksdb.lorc_min_materialized_value_bytes"] = "0"
    if variant.engine == "lsbm":
        props["leveldb.deserialize_on_read"] = "true"
    else:
        props["rocksdb.deserialize_on_read"] = "true"
    return props


def run_props(cfg: SingleConfig, variant: fair.Variant, read_only: bool) -> dict[str, str]:
    props = system_props(cfg, variant)
    if variant.engine == "lsbm":
        props.update(
            {
                "leveldb.destroy": "false",
                "leveldb.run_compaction": "true" if cfg.enable_compaction else "false",
                "leveldb.compaction_buffer_trim_interval": (
                    "30" if cfg.enable_compaction else "1000000000"
                ),
            }
        )
        return props

    props.update(
        {
            "rocksdb.create_if_missing": "false",
            "rocksdb.destroy": "false",
            "rocksdb.disable_auto_compactions": (
                "false" if cfg.enable_compaction else "true"
            ),
            "rocksdb.read_only": "true" if read_only else "false",
        }
    )
    return props


def load_props(cfg: SingleConfig, variant: fair.Variant) -> dict[str, str]:
    if variant.engine == "lsbm":
        return {
            "leveldb.cache_size": str(cache_bytes(cfg)),
            "leveldb.destroy": "true",
            "leveldb.compression": "no",
            "leveldb.run_compaction": "true",
            "leveldb.compaction_buffer_trim_interval": "30",
        }
    props = fair.system_props(variant, cache_bytes(cfg), direct_reads="false")
    props.update(
        {
            "rocksdb.create_if_missing": "true",
            "rocksdb.destroy": "true",
            "rocksdb.compression": "no",
            "rocksdb.disable_auto_compactions": "false",
            "rocksdb.read_only": "false",
            "rocksdb.range_cache_size": "0",
            "rocksdb.lorc_enable_stats": "false",
            "rocksdb.deserialize_on_read": "true",
        }
    )
    return props


def random_seed(cfg: SingleConfig, phase: str) -> int:
    key = (
        f"{phase}|{canonical_system(cfg.system)}|{cfg.total_data_gb}|"
        f"{cfg.cache_data_gb}|{cfg.key_size}|{cfg.value_size}|"
        f"{cfg.scan_length}|{cfg.zipfian_const}|{cfg.update_ratio}|"
        f"{cfg.threads}|{cfg.lru_policy}|{cfg.directio}"
    )
    return 1 + (zlib.crc32(key.encode("utf-8")) % 2_000_000_000)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def run_load(
    *,
    cfg: SingleConfig,
    dataset: fair.Dataset,
    source_path: Path,
    out_dir: Path,
) -> None:
    load_variant = load_variant_for(variant_for(cfg).engine)
    workload = out_dir / "workload_load.properties"
    workload.write_text(load_workload_text(cfg=cfg, dataset=dataset))
    if source_path.exists():
        shutil.rmtree(source_path)
    source_path.parent.mkdir(parents=True, exist_ok=True)
    rc, text, time_info = fair.run_ycsb(
        mode="load",
        variant=load_variant,
        workload=workload,
        db_path=source_path,
        run_dir=out_dir,
        log_name="load",
        props=load_props(cfg, load_variant),
        threads=1,
        timeout=24 * 3600,
        random_seed=random_seed(cfg, "load"),
    )
    if rc != 0:
        raise RuntimeError(f"load failed; see {out_dir / 'load.log'}\n{text[-2500:]}")
    print(
        f"[load] completed records={dataset.recordcount} "
        f"max_rss={time_info.get('max_rss_kb', 0) / 1024:.1f} MiB"
    )


def run_measurement(
    *,
    cfg: SingleConfig,
    dataset: fair.Dataset,
    source_path: Path,
    out_dir: Path,
) -> dict[str, Any]:
    variant = variant_for(cfg)
    warmup_ops = warmup_ops_for(cfg)
    total_ops = warmup_ops + cfg.measured_operations
    warmup_ratio = warmup_ops / total_ops
    workload = out_dir / "workload_run.properties"
    workload.write_text(
        workload_text(
            cfg=cfg,
            dataset=dataset,
            operationcount=total_ops,
            warmup_ratio=warmup_ratio,
            warmup_operation="scan",
        )
    )

    read_only = (cfg.update_ratio == 0.0 and not cfg.enable_compaction)
    db_path = source_path
    work_db: Path | None = None
    if not read_only:
        work_db = out_dir / "workdb"
        if work_db.exists():
            shutil.rmtree(work_db)
        shutil.copytree(source_path, work_db)
        db_path = work_db

    props = run_props(cfg, variant, read_only=read_only)
    if cfg.update_ratio > 0 and not cfg.enable_compaction:
        print(
            "[warn] update_ratio > 0 while enable_compaction=false; "
            "running exactly as configured."
        )
    try:
        rc, text, time_info = fair.run_ycsb(
            mode="run",
            variant=variant,
            workload=workload,
            db_path=db_path,
            run_dir=out_dir,
            log_name="run",
            props=props,
            threads=cfg.threads,
            timeout=24 * 3600,
            random_seed=random_seed(cfg, "run"),
        )
    finally:
        if work_db is not None and work_db.exists():
            shutil.rmtree(work_db)
    if rc != 0:
        raise RuntimeError(f"run failed; see {out_dir / 'run.log'}\n{text[-2500:]}")

    parsed = fair.parse_log(text)
    parsed.update(time_info)
    parsed.update(
        {
            "system": canonical_system(cfg.system),
            "engine": variant.engine,
            "source_path": str(source_path),
            "read_only": read_only,
            "recordcount": dataset.recordcount,
            "estimated_data_bytes": dataset.recordcount
            * (cfg.key_size + cfg.value_size),
            "cache_bytes": cache_bytes(cfg),
            "warmup_operations": warmup_ops,
            "measured_operations": cfg.measured_operations,
            "operationcount": total_ops,
            "warmup_ratio": warmup_ratio,
            "scan_length": cfg.scan_length,
            "zipfian_const": cfg.zipfian_const,
            "update_ratio": cfg.update_ratio,
            "scan_ratio": 1.0 - cfg.update_ratio,
            "threads": cfg.threads,
            "lru_policy": cfg.lru_policy,
            "directio": cfg.directio,
            "enable_compaction": cfg.enable_compaction,
            "request_distribution": cfg.request_distribution,
            "run_log": str(out_dir / "run.log"),
            "time_log": str(out_dir / "run.time"),
        }
    )
    return parsed


def print_summary(summary: dict[str, Any]) -> None:
    def fnum(name: str, default: float = 0.0) -> float:
        try:
            return float(summary.get(name, default))
        except (TypeError, ValueError):
            return default

    print("\n===== Single LORC/YCSB Run Summary =====")
    print(f"system                 : {summary['system']}")
    print(f"source_path            : {summary['source_path']}")
    print(f"recordcount            : {summary['recordcount']}")
    print(
        "estimated_data_size    : "
        f"{summary['estimated_data_bytes'] / GB:.3f} GiB"
    )
    print(f"cache_budget           : {summary['cache_bytes'] / GB:.3f} GiB")
    print(f"warmup_operations      : {summary['warmup_operations']}")
    print(f"measured_operations    : {summary['measured_operations']}")
    print(f"throughput             : {fnum('throughputops/sec'):.2f} ops/s")
    if "scan_count" in summary:
        print(
            "scan latency           : "
            f"avg={fnum('scan_avg_us'):.2f} us, "
            f"p99={fnum('scan_p99_us'):.2f} us, "
            f"count={int(fnum('scan_count'))}"
        )
    if "update_count" in summary:
        print(
            "update latency         : "
            f"avg={fnum('update_avg_us'):.2f} us, "
            f"p99={fnum('update_p99_us'):.2f} us, "
            f"count={int(fnum('update_count'))}"
        )
    if "max_rss_kb" in summary:
        rss_kib = fnum("max_rss_kb")
        print(
            "peak RSS               : "
            f"{rss_kib / 1024:.1f} MiB ({rss_kib / 1024 / 1024:.3f} GiB)"
        )
    if "lorc_full_hit_rate" in summary:
        print(
            "LORC hit rates         : "
            f"full={fnum('lorc_full_hit_rate'):.4f}, "
            f"size={fnum('lorc_hit_size_rate'):.4f}"
        )
    print(f"run_log                : {summary['run_log']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"JSON config path (default: {DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "--no-build",
        action="store_true",
        help="Override config and reuse the source database.",
    )
    parser.add_argument(
        "--build-only",
        action="store_true",
        help="Build or refresh the source database, then exit before warmup/run.",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.no_build:
        cfg = SingleConfig(**{**cfg.__dict__, "build_source": False})

    variant = variant_for(cfg)
    dataset = dataset_for(cfg)
    source_path = dataset.source_path(variant.engine)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = (
        Path(cfg.output_dir)
        if cfg.output_dir
        else OUT_ROOT / f"{timestamp}__{variant.key}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "effective_config.json", cfg.__dict__)

    print("===== Single LORC/YCSB Config =====")
    print(f"config                 : {args.config}")
    print(f"output_dir             : {out_dir}")
    print(f"system                 : {canonical_system(cfg.system)}")
    print(f"engine/source type     : {variant.engine}")
    print(f"source_path            : {source_path}")
    print(f"build_source           : {cfg.build_source}")
    print(f"insert order for load  : random")
    print(f"request distribution   : {cfg.request_distribution}")
    print(f"zipfian_const          : {cfg.zipfian_const}")
    print(f"recordcount            : {dataset.recordcount}")
    print(
        f"warmup formula         : ceil({cfg.warmup_coverage} * "
        f"cache_records / scan_length), cache_records="
        f"{cache_bytes(cfg) // (cfg.key_size + cfg.value_size)}"
    )

    if cfg.build_source or not source_path.exists():
        run_load(cfg=cfg, dataset=dataset, source_path=source_path, out_dir=out_dir)
    else:
        print("[load] skipped; reusing existing source database")

    if args.build_only:
        print("[build-only] source database is ready; skipping warmup/run")
        return 0

    summary = run_measurement(
        cfg=cfg, dataset=dataset, source_path=source_path, out_dir=out_dir
    )
    write_json(out_dir / "summary.json", summary)
    print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
