#!/usr/bin/env python3
"""Run standard YCSB A-F workloads for the five-system LORC matrix.

The measured phase keeps the standard YCSB workload proportions and defaults
from workloads/workloada through workloads/workloadf. Workload E receives
scan warmup because it is the standard scan workload; A/B/C/D/F receive read
warmup so LORC's bounded point-expansion path is measured after it has had a
fair chance to materialize clustered point neighborhoods.
"""

from __future__ import annotations

import argparse
import csv
import re
import shutil
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

import run_lorc_fair_matrix as fair


ROOT = fair.ROOT
PAPER = fair.PAPER
MB = fair.MB
GB = fair.GB
STANDARD_RECORDCOUNT = 100_000
STANDARD_OPERATIONCOUNT = 100_000
STANDARD_TAG = "standard-ycsb-100k"
POINT_WARMUP_OPS = 100_000
POINT_EXPANSION_ENTRIES = 1024


def workload_path(workload: str) -> Path:
    return ROOT / "workloads" / f"workload{workload.lower()}"


def standard_source_path(engine: str) -> Path:
    return ROOT / "db" / f"ycsb-source-{STANDARD_TAG}" / f"ycsb-{engine}-{STANDARD_TAG}"


def rewrite_property(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    if pattern.search(text):
        return pattern.sub(f"{key}={value}", text)
    return text.rstrip() + f"\n{key}={value}\n"


def generated_workload(
    out_dir: Path,
    workload: str,
    *,
    warmup_ops: int,
    warmup_operation: str,
) -> Path:
    src = workload_path(workload)
    text = src.read_text()
    if warmup_ops > 0:
        total_ops = STANDARD_OPERATIONCOUNT + warmup_ops
        text = rewrite_property(text, "operationcount", str(total_ops))
        text = rewrite_property(text, "warmup_ratio", f"{warmup_ops / total_ops:.8f}")
        text = rewrite_property(text, "warmupoperation", warmup_operation)
    path = out_dir / f"workload{workload.lower()}_measured.properties"
    path.write_text(text)
    return path


def load_sources(out_dir: Path, *, budget: int, reload: bool) -> None:
    load_workload = workload_path("a")
    for engine, variant in [
        ("rocksdb", fair.VARIANTS[0]),
        ("blobdb", fair.VARIANTS[2]),
        ("lsbm", fair.VARIANTS[4]),
    ]:
        source = standard_source_path(engine)
        if source.exists() and not reload:
            continue
        if source.exists():
            shutil.rmtree(source)
        source.parent.mkdir(parents=True, exist_ok=True)
        props = fair.load_props(variant, budget)
        rc, text, _ = fair.run_ycsb(
            mode="load",
            variant=variant,
            workload=load_workload,
            db_path=source,
            run_dir=out_dir,
            log_name=f"load_standard_ycsb__{engine}",
            props=props,
            threads=1,
            timeout=1800,
            random_seed=7,
        )
        if rc != 0:
            raise RuntimeError(f"standard source load failed for {engine}\n{text[-2500:]}")


def workload_has_writes(workload: str) -> bool:
    text = workload_path(workload).read_text()
    for key in ("updateproportion", "insertproportion", "readmodifywriteproportion"):
        match = re.search(rf"^{key}=([0-9.]+)", text, re.MULTILINE)
        if match and float(match.group(1)) > 0:
            return True
    return False


def run_experiment(
    *,
    out_dir: Path,
    budget: int,
    reload_sources: bool,
    e_warmup_ops: int,
    workloads: list[str],
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    load_sources(out_dir, budget=budget, reload=reload_sources)
    workload_dir = out_dir / "workloads"
    workload_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, str | int | float]] = []
    for workload in workloads:
        workload_file = generated_workload(
            workload_dir,
            workload,
            warmup_ops=e_warmup_ops if workload == "e" else POINT_WARMUP_OPS,
            warmup_operation="scan" if workload == "e" else "read",
        )
        writes = workload_has_writes(workload)
        for variant in fair.VARIANTS:
            source = standard_source_path(variant.engine)
            if not source.exists():
                raise FileNotFoundError(source)
            db_path = source
            work_db: Path | None = None
            if writes:
                work_db = out_dir / "workdb" / f"workload{workload}__{variant.key}"
                if work_db.exists():
                    shutil.rmtree(work_db)
                work_db.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source, work_db)
                db_path = work_db

            props = fair.system_props(variant, budget, direct_reads="false")
            if variant.lorc:
                props["rocksdb.lorc_point_expansion_entries"] = str(POINT_EXPANSION_ENTRIES)
            if variant.label == "BlobDB+LORC":
                # Standard YCSB uses small/default values. The large-value
                # KV-separation-aware path is evaluated separately; here the
                # generic range materialization path avoids unnecessary
                # per-range value-state checks on short scans.
                props["rocksdb.lorc_value_separation_aware"] = "false"
                props["rocksdb.lorc_bypass_lower_cache_on_refill"] = "false"
            if writes:
                if variant.engine == "lsbm":
                    props.update(
                        {
                            "leveldb.destroy": "false",
                            "leveldb.run_compaction": "true",
                            "leveldb.compaction_buffer_trim_interval": "30",
                        }
                    )
                else:
                    props.update(
                        {
                            "rocksdb.disable_auto_compactions": "false",
                            "rocksdb.create_if_missing": "false",
                            "rocksdb.destroy": "false",
                            "rocksdb.read_only": "false",
                        }
                    )
            else:
                props.update(fair.read_only_props(variant))

            log_name = f"standard_ycsb__{workload}__{variant.key}"
            try:
                rc, text, time_info = fair.run_ycsb(
                    mode="run",
                    variant=variant,
                    workload=workload_file,
                    db_path=db_path,
                    run_dir=out_dir,
                    log_name=log_name,
                    props=props,
                    threads=1,
                    timeout=1800,
                    random_seed=101 + ord(workload),
                )
            finally:
                if work_db is not None and work_db.exists():
                    shutil.rmtree(work_db)
            if rc != 0:
                raise RuntimeError(f"standard YCSB run failed for {workload}/{variant.label}\n{text[-2500:]}")
            parsed = fair.parse_log(text)
            rows.append(
                {
                    "workload": workload.upper(),
                    "variant": variant.label,
                    "variant_key": variant.key,
                    "engine": variant.engine,
                    "writes": int(writes),
                    "cache_budget_bytes": budget,
                    "operationcount": STANDARD_OPERATIONCOUNT,
                    "warmup_ops": e_warmup_ops if workload == "e" else POINT_WARMUP_OPS,
                    "warmup_operation": "scan" if workload == "e" else "read",
                    "direct_reads": 0,
                    "lorc_point_expansion_entries": POINT_EXPANSION_ENTRIES if variant.lorc else 0,
                    "throughput_ops_sec": float(parsed.get("throughputops/sec", 0.0)),
                    "read_avg_us": float(parsed.get("read_avg_us", 0.0)),
                    "read_p99_us": float(parsed.get("read_p99_us", 0.0)),
                    "scan_avg_us": float(parsed.get("scan_avg_us", 0.0)),
                    "scan_p99_us": float(parsed.get("scan_p99_us", 0.0)),
                    "update_avg_us": float(parsed.get("update_avg_us", 0.0)),
                    "update_p99_us": float(parsed.get("update_p99_us", 0.0)),
                    "insert_avg_us": float(parsed.get("insert_avg_us", 0.0)),
                    "insert_p99_us": float(parsed.get("insert_p99_us", 0.0)),
                    "readmodifywrite_avg_us": float(parsed.get("readmodifywrite_avg_us", 0.0)),
                    "readmodifywrite_p99_us": float(parsed.get("readmodifywrite_p99_us", 0.0)),
                    "lorc_full_hit_rate": float(parsed.get("lorc_full_hit_rate", 0.0)),
                    "lorc_hit_size_rate": float(parsed.get("lorc_hit_size_rate", 0.0)),
                    "max_rss_kb": int(time_info.get("max_rss_kb", 0)),
                    "fs_inputs": int(time_info.get("fs_inputs", 0)),
                    "fs_outputs": int(time_info.get("fs_outputs", 0)),
                    "log": str(out_dir / f"{log_name}.log"),
                }
            )

    summary = out_dir / "lorc_standard_ycsb_summary.csv"
    with summary.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return summary


def plot(summary: Path, figure: Path) -> None:
    with summary.open() as f:
        rows = list(csv.DictReader(f))

    workloads = sorted({r["workload"] for r in rows})
    variants = [v.label for v in fair.VARIANTS]
    index = {(r["workload"], r["variant"]): float(r["throughput_ops_sec"]) for r in rows}
    rocks_base = {w: index[(w, "RocksDB")] for w in workloads}

    plt.rcParams.update(
        {
            "font.size": 8.4,
            "axes.labelsize": 8.4,
            "legend.fontsize": 8,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "figure.dpi": 180,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )
    colors = [fair.COLORS[v] for v in variants]
    hatches = [fair.HATCHES[v] for v in variants]
    width = 0.15
    xs = list(range(len(workloads)))
    fig, ax = plt.subplots(figsize=(7.35, 2.45))
    for i, variant in enumerate(variants):
        values = [index[(w, variant)] / rocks_base[w] for w in workloads]
        bars = ax.bar(
            [x + (i - 2) * width for x in xs],
            values,
            width,
            label=variant,
            color=colors[i],
            edgecolor="#222222",
            linewidth=0.45,
        )
        for bar in bars:
            bar.set_hatch(hatches[i])
    ax.axhline(1.0, color="#666666", linewidth=0.8, linestyle="--")
    ax.set_ylabel("Relative throughput\n(RocksDB=1)")
    ax.set_xticks(xs)
    ax.set_xticklabels([f"YCSB-{w}" for w in workloads])
    ax.grid(axis="y", color="#E7E7E7", linewidth=0.65)
    ax.set_axisbelow(True)
    ax.legend(
        loc="upper center",
        bbox_to_anchor=(0.5, 1.32),
        ncol=5,
        frameon=False,
        columnspacing=0.9,
        handlelength=1.2,
    )
    fig.tight_layout()
    figure.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(figure)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--plot-only", action="store_true")
    parser.add_argument("--reload-sources", action="store_true")
    parser.add_argument("--budget-mb", type=int, default=1024)
    parser.add_argument("--e-warmup-ops", type=int, default=40_000)
    parser.add_argument("--workloads", default="a,b,c,d,e,f")
    args = parser.parse_args()

    out_dir = args.out or (ROOT / "result" / "log" / f"lorc_standard_ycsb_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    figure = PAPER / "figures" / "experiments" / "eval_standard_ycsb_af.pdf"
    paper_summary = PAPER / "figures" / "experiments" / "lorc_standard_ycsb_summary.csv"

    if args.plot_only:
        if not args.summary:
            raise SystemExit("--plot-only requires --summary")
        plot(args.summary, figure)
        paper_summary.write_text(args.summary.read_text())
        print(f"summary={args.summary}")
        print(f"figure={figure}")
        return 0

    summary = run_experiment(
        out_dir=out_dir,
        budget=args.budget_mb * MB,
        reload_sources=args.reload_sources,
        e_warmup_ops=args.e_warmup_ops,
        workloads=[w.strip().lower() for w in args.workloads.split(",") if w.strip()],
    )
    plot(summary, figure)
    paper_summary.write_text(summary.read_text())
    print(f"summary={summary}")
    print(f"paper_summary={paper_summary}")
    print(f"figure={figure}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
