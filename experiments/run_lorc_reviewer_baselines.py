#!/usr/bin/env python3
"""Run reviewer-driven LORC baseline experiments.

This script keeps the paper's main five-system matrix unchanged and adds two
targeted checks requested by reviewers:

  * an end-to-end entry-granular ordered range-cache baseline using the
    in-engine skip-list backend, compared against RocksDB and LORC continuous
    segments on the same 4GB source database;
  * a cache-split sensitivity check that keeps the total configured cache budget
    fixed at 1GB while varying how much memory is assigned to LORC versus native
    block/blob caches.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

import run_lorc_fair_matrix as fair


ROOT = fair.ROOT
PAPER = fair.PAPER
SUMMARY = PAPER / "figures" / "experiments" / "lorc_reviewer_baselines_summary.csv"
ENTRY_FIGURE = PAPER / "figures" / "experiments" / "eval_entry_cache_end_to_end.pdf"
HYBRID_FIGURE = PAPER / "figures" / "experiments" / "eval_hybrid_cache_split.pdf"

ENTRY_SCAN_LENGTHS = [5, 20, 100]
HYBRID_RANGE_SHARES = [0.0, 0.25, 0.50, 0.75, 1.0]


ENTRY_VARIANTS = [
    fair.Variant(
        "RocksDB",
        "rocksdb",
        "rocksdb_lorc",
        Path("rocksdb_lorc/rocksdb.properties"),
    ),
    fair.Variant(
        "Entry range cache",
        "rocksdb",
        "rocksdb_lorc",
        Path("rocksdb_lorc/rocksdb_lorc.properties"),
        lorc=True,
    ),
    fair.Variant(
        "LORC segment",
        "rocksdb",
        "rocksdb_lorc",
        Path("rocksdb_lorc/rocksdb_lorc.properties"),
        lorc=True,
    ),
]


def ensure_required_sources() -> None:
    missing: list[Path] = []
    for engine in ("rocksdb", "blobdb"):
        source = fair.GENERAL_DATASET.source_path(engine)
        if not source.exists():
            missing.append(source)
    if missing:
        formatted = "\n".join(str(path) for path in missing)
        raise FileNotFoundError(
            "Reviewer baseline experiments require the standard RocksDB/BlobDB "
            f"4GB source databases. Missing:\n{formatted}"
        )


def reviewer_props(variant: fair.Variant, budget: int, *, direct_reads: str) -> dict[str, str]:
    if variant.label == "Entry range cache":
        props = fair.system_props(fair.VARIANTS[1], budget, direct_reads=direct_reads)
        props["rocksdb.range_cache_backend"] = "skiplist"
        props["rocksdb.range_cache_size"] = str(budget)
        props["rocksdb.block_cache_size"] = "0"
        props["rocksdb.blob_cache_size"] = "0"
        props["rocksdb.lorc_enable_stats"] = "true"
        return props
    if variant.label == "LORC segment":
        return fair.system_props(fair.VARIANTS[1], budget, direct_reads=direct_reads)
    if variant.label == "RocksDB":
        return fair.system_props(fair.VARIANTS[0], budget, direct_reads=direct_reads)
    raise ValueError(f"unknown variant {variant.label}")


def hybrid_props(engine: str, share: float, budget: int, *, direct_reads: str) -> dict[str, str]:
    range_cache = int(round(budget * share))
    remaining = budget - range_cache
    if engine == "rocksdb":
        block_cache = remaining
        blob_cache = 0
        props = fair.system_props(fair.VARIANTS[1] if range_cache else fair.VARIANTS[0],
                                  budget, direct_reads=direct_reads)
        props.update({
            "rocksdb.block_cache_size": str(block_cache),
            "rocksdb.blob_cache_size": str(blob_cache),
            "rocksdb.range_cache_size": str(range_cache),
            "rocksdb.range_cache_backend": "lorc",
            "rocksdb.range_cache_physical_type": "continuous",
            "rocksdb.lorc_enable_stats": "true" if range_cache else "false",
        })
        return props

    if engine == "blobdb":
        block_cache = remaining // 4
        blob_cache = remaining - block_cache
        props = fair.system_props(fair.VARIANTS[3] if range_cache else fair.VARIANTS[2],
                                  budget, direct_reads=direct_reads)
        props.update({
            "rocksdb.block_cache_size": str(block_cache),
            "rocksdb.blob_cache_size": str(blob_cache),
            "rocksdb.range_cache_size": str(range_cache),
            "rocksdb.range_cache_backend": "lorc",
            "rocksdb.range_cache_physical_type": "continuous",
            "rocksdb.lorc_enable_stats": "true" if range_cache else "false",
            "rocksdb.lorc_value_separation_aware": "true",
            "rocksdb.lorc_bypass_lower_cache_on_refill": "true",
            "rocksdb.lorc_min_materialized_value_bytes": "512",
        })
        return props

    raise ValueError(f"unknown engine {engine}")


def run_single(
    *,
    out_dir: Path,
    suite: str,
    variant: fair.Variant,
    dataset: fair.Dataset,
    workload: Path,
    props: dict[str, str],
    x_value: str,
    x_label: str,
    scan_length: int,
    warmup_ops: int,
    warmup_ratio: float,
    hot_ratio: float,
    timeout: int,
    seed: int,
) -> dict[str, str | int | float]:
    source = dataset.source_path(variant.engine)
    if not source.exists():
        raise FileNotFoundError(source)
    props = dict(props)
    props.update(fair.read_only_props(variant))
    log_name = f"{suite}__{x_value}__{variant.key}"
    rc, text, time_info = fair.run_ycsb(
        mode="run",
        variant=variant,
        workload=workload,
        db_path=source,
        run_dir=out_dir,
        log_name=log_name,
        props=props,
        threads=1,
        timeout=timeout,
        random_seed=seed,
    )
    if rc != 0:
        raise RuntimeError(f"{log_name} failed\n{text[-2500:]}")
    parsed = fair.parse_log(text)
    block_cache = int(props.get("rocksdb.block_cache_size", "0"))
    blob_cache = int(props.get("rocksdb.blob_cache_size", "0"))
    range_cache = int(props.get("rocksdb.range_cache_size", "0"))
    row: dict[str, str | int | float] = {
        "suite": suite,
        "x_value": x_value,
        "x_label": x_label,
        "dataset": dataset.name,
        "recordcount": dataset.recordcount,
        "fieldlength": dataset.fieldlength,
        "variant": variant.label,
        "variant_key": variant.key,
        "engine": variant.engine,
        "scan_length": scan_length,
        "hot_ratio": hot_ratio,
        "warmup_ops": warmup_ops,
        "warmup_ratio": warmup_ratio,
        "cache_budget_bytes": fair.DEFAULT_CACHE_BUDGET,
        "block_cache_bytes": block_cache,
        "blob_cache_bytes": blob_cache,
        "range_cache_bytes": range_cache,
        "configured_total_cache_bytes": block_cache + blob_cache + range_cache,
        "random_seed": seed,
        "max_rss_kb": int(time_info.get("max_rss_kb", 0)),
        "fs_inputs": int(time_info.get("fs_inputs", 0)),
        "fs_outputs": int(time_info.get("fs_outputs", 0)),
        "log": str(out_dir / f"{log_name}.log"),
    }
    for key, value in parsed.items():
        row[key] = value
    if row["configured_total_cache_bytes"]:
        row["rss_to_cache_budget"] = (
            int(row["max_rss_kb"]) * 1024 / int(row["configured_total_cache_bytes"])
        )
    return row


def run_entry_baseline(out_dir: Path) -> list[dict[str, str | int | float]]:
    dataset = fair.GENERAL_DATASET
    rows: list[dict[str, str | int | float]] = []
    workload_dir = out_dir / "workloads"
    workload_dir.mkdir(parents=True, exist_ok=True)
    for scan_length in ENTRY_SCAN_LENGTHS:
        workload, op_count, warmup_ops, warmup_ratio = fair.write_workload(
            workload_dir,
            f"entry_baseline_sl{scan_length}",
            dataset=dataset,
            measured_ops=30_000,
            hot_ratio=0.05,
            read_prop=0.0,
            update_prop=0.0,
            scan_prop=1.0,
            requestdistribution="zipfian",
            scan_length=scan_length,
            min_warmup_ops=40_000,
            coverage_factor=20.0,
        )
        for variant in ENTRY_VARIANTS:
            props = reviewer_props(variant, fair.DEFAULT_CACHE_BUDGET, direct_reads="false")
            row = run_single(
                out_dir=out_dir,
                suite="entry_baseline",
                variant=variant,
                dataset=dataset,
                workload=workload,
                props=props,
                x_value=str(scan_length),
                x_label=str(scan_length),
                scan_length=scan_length,
                warmup_ops=warmup_ops,
                warmup_ratio=warmup_ratio,
                hot_ratio=0.05,
                timeout=1800,
                seed=10_000 + scan_length * 17 + len(rows),
            )
            row["operationcount"] = op_count
            rows.append(row)
            print(row, flush=True)
    return rows


def run_hybrid_split(out_dir: Path) -> list[dict[str, str | int | float]]:
    dataset = fair.GENERAL_DATASET
    rows: list[dict[str, str | int | float]] = []
    workload_dir = out_dir / "workloads"
    workload_dir.mkdir(parents=True, exist_ok=True)
    workload, op_count, warmup_ops, warmup_ratio = fair.write_workload(
        workload_dir,
        "hybrid_split_sl20",
        dataset=dataset,
        measured_ops=30_000,
        hot_ratio=0.05,
        read_prop=0.0,
        update_prop=0.0,
        scan_prop=1.0,
        requestdistribution="zipfian",
        scan_length=20,
        min_warmup_ops=40_000,
        coverage_factor=20.0,
    )
    for engine in ("rocksdb", "blobdb"):
        base_variant = (
            fair.VARIANTS[1] if engine == "rocksdb" else fair.VARIANTS[3]
        )
        for share in HYBRID_RANGE_SHARES:
            label = f"{'RocksDB' if engine == 'rocksdb' else 'BlobDB'} split {int(share * 100)}pct"
            variant = fair.Variant(
                label,
                engine,
                "rocksdb_lorc",
                base_variant.prop_file,
                lorc=share > 0,
                blobdb=engine == "blobdb",
            )
            props = hybrid_props(engine, share, fair.DEFAULT_CACHE_BUDGET, direct_reads="false")
            row = run_single(
                out_dir=out_dir,
                suite="hybrid_split",
                variant=variant,
                dataset=dataset,
                workload=workload,
                props=props,
                x_value=f"{share:.2f}",
                x_label=f"{int(share * 100)}%",
                scan_length=20,
                warmup_ops=warmup_ops,
                warmup_ratio=warmup_ratio,
                hot_ratio=0.05,
                timeout=1800,
                seed=20_000 + int(share * 1000) + (0 if engine == "rocksdb" else 5000),
            )
            row["operationcount"] = op_count
            row["range_share"] = share
            rows.append(row)
            print(row, flush=True)
    return rows


def write_summary(rows: list[dict[str, str | int | float]], path: Path) -> None:
    keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def f(row: dict[str, str | int | float], key: str, default: float = 0.0) -> float:
    try:
        return float(row.get(key, default))
    except (TypeError, ValueError):
        return default


def plot_entry(rows: list[dict[str, str | int | float]]) -> None:
    data = [r for r in rows if r["suite"] == "entry_baseline"]
    colors = {
        "RocksDB": "#4E79A7",
        "Entry range cache": "#8C6D31",
        "LORC segment": "#3D8B5B",
    }
    markers = {"RocksDB": "o", "Entry range cache": "s", "LORC segment": "^"}
    plt.rcParams.update({"font.size": 8.5, "pdf.fonttype": 42, "ps.fonttype": 42})
    fig, axes = plt.subplots(1, 2, figsize=(6.7, 2.35), constrained_layout=True)
    for label in ["RocksDB", "Entry range cache", "LORC segment"]:
        subset = sorted([r for r in data if r["variant"] == label], key=lambda r: int(r["scan_length"]))
        xs = [int(r["scan_length"]) for r in subset]
        tput = [f(r, "throughputops/sec") / 1000.0 for r in subset]
        p99 = [f(r, "scan_p99_us") for r in subset]
        axes[0].plot(xs, tput, marker=markers[label], color=colors[label], label=label, linewidth=1.7)
        axes[1].plot(xs, p99, marker=markers[label], color=colors[label], label=label, linewidth=1.7)
    for ax in axes:
        ax.set_xscale("log")
        ax.set_xticks(ENTRY_SCAN_LENGTHS)
        ax.get_xaxis().set_major_formatter(lambda x, _pos: f"{int(x)}")
        ax.grid(axis="y", color="#E7E7E7", linewidth=0.65)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.set_xlabel("Scan length")
    axes[0].set_ylabel("K scans/s")
    axes[0].set_title("throughput")
    axes[1].set_ylabel("p99 scan (us)")
    axes[1].set_title("tail latency")
    axes[0].legend(frameon=False, ncol=1, loc="best")
    fig.savefig(ENTRY_FIGURE, bbox_inches="tight")
    plt.close(fig)


def plot_hybrid(rows: list[dict[str, str | int | float]]) -> None:
    data = [r for r in rows if r["suite"] == "hybrid_split"]
    plt.rcParams.update({"font.size": 8.5, "pdf.fonttype": 42, "ps.fonttype": 42})
    fig, axes = plt.subplots(1, 2, figsize=(6.7, 2.35), constrained_layout=True)
    for engine, color, label in [("rocksdb", "#F28E2B", "RocksDB+range"), ("blobdb", "#E15759", "BlobDB+range")]:
        subset = sorted([r for r in data if r["engine"] == engine], key=lambda r: f(r, "range_share"))
        xs = [100.0 * f(r, "range_share") for r in subset]
        tput = [f(r, "throughputops/sec") / 1000.0 for r in subset]
        p99 = [f(r, "scan_p99_us") for r in subset]
        axes[0].plot(xs, tput, marker="o", color=color, label=label, linewidth=1.7)
        axes[1].plot(xs, p99, marker="o", color=color, label=label, linewidth=1.7)
    for ax in axes:
        ax.set_xticks([0, 25, 50, 75, 100])
        ax.grid(axis="y", color="#E7E7E7", linewidth=0.65)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.set_xlabel("Budget assigned to LORC (%)")
    axes[0].set_ylabel("K scans/s")
    axes[0].set_title("throughput")
    axes[1].set_ylabel("p99 scan (us)")
    axes[1].set_title("tail latency")
    axes[0].legend(frameon=False, loc="best")
    fig.savefig(HYBRID_FIGURE, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", choices=["all", "entry", "hybrid"], default="all")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    out_dir = args.out or (ROOT / "result" / "log" / f"lorc_reviewer_baselines_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    out_dir.mkdir(parents=True, exist_ok=True)

    ensure_required_sources()
    rows: list[dict[str, str | int | float]] = []
    if args.suite in {"all", "entry"}:
        rows.extend(run_entry_baseline(out_dir))
    if args.suite in {"all", "hybrid"}:
        rows.extend(run_hybrid_split(out_dir))

    local_summary = out_dir / "lorc_reviewer_baselines_summary.csv"
    write_summary(rows, local_summary)
    write_summary(rows, SUMMARY)
    if any(r["suite"] == "entry_baseline" for r in rows):
        plot_entry(rows)
    if any(r["suite"] == "hybrid_split" for r in rows):
        plot_hybrid(rows)
    print(f"summary={local_summary}")
    print(f"paper_summary={SUMMARY}")
    print(f"entry_figure={ENTRY_FIGURE}")
    print(f"hybrid_figure={HYBRID_FIGURE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
