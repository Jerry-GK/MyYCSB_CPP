#!/usr/bin/env python3
"""Run controlled LORC/YCSB sweeps through the single-config runner.

This script is intentionally thin: every experiment point is executed by

    python3 experiments/run_lorc_single_config.py --config <generated.json> --no-build

The script only generates temporary JSON configs, changes one knob per sweep,
prints each finished system result, and redraws grouped bar charts for scan
throughput and p99 latency.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "experiments" / "configs" / "lorc_single_test_default.json"
DEFAULT_RUNNER = ROOT / "experiments" / "run_lorc_single_config.py"
DEFAULT_OUTPUT_ROOT = ROOT / "result" / "manual_three_sweeps"

SYSTEMS = ["rocksdb", "rocksdb_lorc", "blobdb", "blobdb_lorc", "lsbm"]
SYSTEM_LABELS = {
    "rocksdb": "RocksDB",
    "rocksdb_lorc": "RocksDB+LORC",
    "blobdb": "BlobDB",
    "blobdb_lorc": "BlobDB+LORC",
    "lsbm": "LSbM",
}
SYSTEM_COLORS = {
    "rocksdb": "#E6862D",
    "rocksdb_lorc": "#E6862D",
    "blobdb": "#C84C4C",
    "blobdb_lorc": "#C84C4C",
    "lsbm": "#8E6BBE",
}
SYSTEM_HATCHES = {
    "rocksdb": "",
    "rocksdb_lorc": "///",
    "blobdb": "",
    "blobdb_lorc": "///",
    "lsbm": "",
}

LORC_STATS_RE = re.compile(r"\[LORC_STATS\]\s+(?P<body>.*)")
LORC_RANGE_SAMPLE_RE = re.compile(r"\[LORC_RANGE_SAMPLE\]\s+(?P<body>.*)")

SWEEP_FUNCS = {
    "scan_length": "run_scan_length_sweep",
    "zipfian": "run_zipfian_sweep",
    "value_size": "run_value_size_sweep",
    "threads": "run_thread_sweep",
    "update_ratio": "run_update_ratio_sweep",
}


@dataclass(frozen=True)
class SweepPoint:
    suite: str
    knob: str
    value: Any
    label: str
    system: str
    rep: int

    @property
    def safe_name(self) -> str:
        def safe(x: Any) -> str:
            return str(x).replace("/", "_").replace(".", "p").replace("%", "pct")

        return (
            f"{safe(self.suite)}__{safe(self.value)}__"
            f"{safe(self.system)}__r{self.rep}"
        )


def parse_csv_list(raw: str, cast: type) -> list[Any]:
    values: list[Any] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        values.append(cast(item))
    if not values:
        raise ValueError(f"empty list: {raw!r}")
    return values


def value_size_label(value: int) -> str:
    if value >= 1024 and value % 1024 == 0:
        return f"{value // 1024}KB"
    return f"{value}B"


def load_base_config(path: Path) -> dict[str, Any]:
    cfg = json.loads(path.read_text())
    cfg["build_source"] = False
    return cfg


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    keys: list[str] = []
    preferred = [
        "suite",
        "knob",
        "x_value",
        "x_label",
        "system_key",
        "system",
        "rep",
        "returncode",
        "scan_throughput_ops_sec",
        "scan_avg_us",
        "scan_p99_us",
        "throughputops/sec",
        "max_rss_kb",
        "lorc_full_hit_rate",
        "lorc_hit_size_rate",
        "lorc_physical_range_count",
        "lorc_logical_range_count",
        "output_dir",
        "config_path",
        "error",
    ]
    for key in preferred:
        if any(key in row for row in rows):
            keys.append(key)
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def same_point(row: dict[str, Any], point: SweepPoint) -> bool:
    return (
        str(row.get("suite", "")) == point.suite
        and str(row.get("x_label", "")) == point.label
        and str(row.get("system_key", "")) == point.system
        and str(row.get("rep", "")) == str(point.rep)
    )


def successful_point_row(
    rows: list[dict[str, Any]], point: SweepPoint
) -> dict[str, Any] | None:
    for row in rows:
        if not same_point(row, point):
            continue
        try:
            if int(row.get("returncode", 1) or 1) == 0:
                return row
        except ValueError:
            continue
    return None


def remove_point_rows(rows: list[dict[str, Any]], point: SweepPoint) -> None:
    rows[:] = [row for row in rows if not same_point(row, point)]


def parse_lorc_kv_body(body: str) -> dict[str, float | str]:
    parsed: dict[str, float | str] = {}
    for token in body.split():
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        try:
            parsed[key] = float(value)
        except ValueError:
            parsed[key] = value
    return parsed


def parse_lorc_range_distribution(run_log: Path) -> dict[str, float | str]:
    if not run_log.exists():
        return {}
    text = run_log.read_text(errors="replace")
    stats_matches = list(LORC_STATS_RE.finditer(text))
    if not stats_matches:
        return {}
    stats = parse_lorc_kv_body(stats_matches[-1].group("body"))
    sample_matches = list(LORC_RANGE_SAMPLE_RE.finditer(text))
    sample = (
        parse_lorc_kv_body(sample_matches[-1].group("body"))
        if sample_matches
        else {}
    )

    physical_count = float(stats.get("physical_range_count", 0.0) or 0.0)
    logical_count = float(stats.get("logical_range_count", 0.0) or 0.0)
    total_length = float(stats.get("total_range_length", 0.0) or 0.0)
    avg_physical = total_length / physical_count if physical_count > 0 else 0.0
    avg_logical = float(sample.get("avg_len", 0.0) or 0.0)
    if avg_logical == 0.0 and logical_count > 0:
        avg_logical = total_length / logical_count

    return {
        "lorc_logical_range_count": logical_count,
        "lorc_avg_logical_range_length": avg_logical,
        "lorc_physical_range_count": physical_count,
        "lorc_avg_physical_range_length": avg_physical,
        "lorc_total_range_length": total_length,
    }


def median_lorc_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        returncode = row.get("returncode", 1)
        if returncode is None or int(returncode) != 0:
            continue
        system_key = str(row.get("system_key", ""))
        if "lorc" not in system_key:
            continue
        if "scan_throughput_ops_sec" not in row:
            continue
        key = (
            str(row.get("suite", "")),
            str(row.get("x_label", "")),
            system_key,
        )
        groups.setdefault(key, []).append(row)

    selected: list[dict[str, Any]] = []
    for group_rows in groups.values():
        values = sorted(float(r.get("scan_throughput_ops_sec", 0.0) or 0.0)
                        for r in group_rows)
        if not values:
            continue
        mid = len(values) // 2
        if len(values) % 2:
            median_value = values[mid]
        else:
            median_value = (values[mid - 1] + values[mid]) / 2.0
        selected.append(
            min(
                group_rows,
                key=lambda r: (
                    abs(float(r.get("scan_throughput_ops_sec", 0.0) or 0.0)
                        - median_value),
                    int(r.get("rep", 0) or 0),
                ),
            )
        )

    return sorted(
        selected,
        key=lambda r: (
            str(r.get("suite", "")),
            float(r.get("x_order", 0.0) or 0.0),
            str(r.get("system_key", "")),
        ),
    )


def write_lorc_range_distribution(path: Path, rows: list[dict[str, Any]]) -> None:
    records: list[dict[str, Any]] = []
    for row in median_lorc_rows(rows):
        run_log = Path(str(row.get("run_log") or ""))
        distribution = parse_lorc_range_distribution(run_log)
        if not distribution:
            continue
        record = {
            "suite": row.get("suite"),
            "knob": row.get("knob"),
            "x_value": row.get("x_value"),
            "x_label": row.get("x_label"),
            "system_key": row.get("system_key"),
            "system": row.get("system"),
            "rep": row.get("rep"),
            "scan_length": row.get("scan_length"),
            "value_size": row.get("value_size"),
            "zipfian_const": row.get("zipfian_const"),
            "cache_bytes": row.get("cache_bytes"),
            "update_ratio": row.get("update_ratio"),
            "threads": row.get("threads"),
            "lru_policy": row.get("lru_policy"),
            "scan_throughput_ops_sec": row.get("scan_throughput_ops_sec"),
            "scan_p99_us": row.get("scan_p99_us"),
            "run_log": str(run_log),
        }
        record.update(distribution)
        records.append(record)

    if records:
        path.write_text(
            "".join(json.dumps(r, sort_keys=True) + "\n" for r in records)
        )
    elif path.exists():
        path.unlink()


def print_rule(title: str, char: str = "=") -> None:
    line = char * 92
    print(f"\n{line}\n{title}\n{line}", flush=True)


def fmt_int(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def fmt_float(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def median_rows(rows: list[dict[str, Any]], suite: str) -> pd.DataFrame:
    df = pd.DataFrame([r for r in rows if r.get("suite") == suite])
    if df.empty:
        return df
    df = df[df["returncode"].fillna(1).astype(int) == 0].copy()
    if df.empty:
        return df
    if "x_order" in df.columns:
        df["x_order"] = pd.to_numeric(df["x_order"], errors="coerce")
    numeric_cols = [
        "scan_throughput_ops_sec",
        "scan_p99_us",
        "scan_avg_us",
        "max_rss_kb",
        "lorc_full_hit_rate",
        "lorc_hit_size_rate",
        "lorc_physical_range_count",
        "lorc_logical_range_count",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    grouped = (
        df.groupby(["x_label", "x_order", "system_key"], as_index=False)[
            [c for c in numeric_cols if c in df.columns]
        ]
        .median(numeric_only=True)
        .sort_values(["x_order", "system_key"])
    )
    return grouped


def plot_sweep(
    rows: list[dict[str, Any]],
    *,
    suite: str,
    x_title: str,
    output_dir: Path,
    systems: list[str],
) -> None:
    df = median_rows(rows, suite)
    if df.empty:
        return
    plt.rcParams.update(
        {
            "font.size": 9,
            "axes.titlesize": 10,
            "axes.labelsize": 9,
            "xtick.labelsize": 8.5,
            "ytick.labelsize": 8.5,
            "legend.fontsize": 8.2,
            "hatch.linewidth": 0.8,
        }
    )
    order_df = df[["x_label", "x_order"]].drop_duplicates().sort_values("x_order")
    x_labels = order_df["x_label"].tolist()
    x = np.arange(len(x_labels))
    width = min(0.16, 0.78 / max(len(systems), 1))

    fig, axes = plt.subplots(1, 2, figsize=(10.8, 2.9))
    fig.patch.set_facecolor("white")
    for ax, metric, y_label, title in [
        (axes[0], "scan_throughput_ops_sec", "Scan throughput (ops/s)", "Throughput"),
        (axes[1], "scan_p99_us", "p99 latency (us)", "Tail latency"),
    ]:
        ax.set_facecolor("#fbfbfb")
        for i, system in enumerate(systems):
            vals: list[float] = []
            for label in x_labels:
                row = df[(df["x_label"] == label) & (df["system_key"] == system)]
                vals.append(float(row[metric].iloc[0]) if len(row) else math.nan)
            ax.bar(
                x + (i - (len(systems) - 1) / 2) * width,
                vals,
                width,
                label=SYSTEM_LABELS.get(system, system),
                color=SYSTEM_COLORS.get(system, "#999999"),
                edgecolor="#2d2d2d",
                linewidth=0.42,
                hatch=SYSTEM_HATCHES.get(system, ""),
            )
        ax.set_xticks(x)
        ax.set_xticklabels(x_labels)
        ax.set_xlabel(x_title)
        ax.set_ylabel(y_label)
        ax.set_title(title)
        ax.grid(axis="y", color="#e4e4e4", linewidth=0.72)
        ax.set_axisbelow(True)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)

    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        ncol=min(len(systems), 5),
        frameon=False,
        loc="upper center",
        bbox_to_anchor=(0.5, 1.04),
        fontsize=8.5,
    )
    fig.tight_layout(rect=[0, 0, 1, 0.94], pad=0.65)
    for ext in ("png", "pdf"):
        fig.savefig(output_dir / f"{suite}_throughput_p99.{ext}", dpi=220, bbox_inches="tight")
    plt.close(fig)


def generated_config(
    *,
    base: dict[str, Any],
    point: SweepPoint,
    point_dir: Path,
) -> dict[str, Any]:
    cfg = dict(base)
    cfg[point.knob] = point.value
    cfg["system"] = point.system
    cfg["output_dir"] = str(point_dir)
    cfg["build_source"] = False
    if point.suite == "update_ratio":
        # The workload-mix sweep measures scan throughput while foreground
        # updates are present.  Run it on a disposable copy and keep compaction
        # enabled so write-heavy points do not accumulate an artificial L0.
        cfg["enable_compaction"] = True
    return cfg


def apply_manual_overrides(base: dict[str, Any], args: argparse.Namespace) -> None:
    if args.lsbm_compaction_buffer_use_length is not None:
        base["lsbm_compaction_buffer_use_length"] = (
            args.lsbm_compaction_buffer_use_length
        )


def estimated_record_count(cfg: dict[str, Any]) -> int:
    total_bytes = int(float(cfg.get("total_data_gb", 4.0)) * 1024 * 1024 * 1024)
    key_size = int(cfg.get("key_size", 24))
    value_size = int(cfg.get("value_size", 1024))
    return max(1, total_bytes // (key_size + value_size))


def computed_warmup_ops(cfg: dict[str, Any]) -> int:
    recordcount = estimated_record_count(cfg)
    warmup_coverage = float(cfg.get("warmup_coverage", 8.0))
    scan_length = max(1, int(cfg.get("scan_length", 50)))
    return int(math.ceil(warmup_coverage * recordcount / scan_length))


def run_one_point(
    *,
    base: dict[str, Any],
    point: SweepPoint,
    runner: Path,
    run_root: Path,
    python: str,
) -> dict[str, Any]:
    point_dir = run_root / point.suite / point.safe_name
    config_dir = run_root / "configs" / point.suite
    point_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / f"{point.safe_name}.json"
    cfg = generated_config(base=base, point=point, point_dir=point_dir)
    config_path.write_text(json.dumps(cfg, indent=2) + "\n")

    cmd = [python, str(runner), "--config", str(config_path), "--no-build"]
    recordcount = estimated_record_count(cfg)
    warmup_ops = computed_warmup_ops(cfg)
    print_rule(
        f"{point.suite} | {point.knob}={point.label} | "
        f"{SYSTEM_LABELS.get(point.system, point.system)} | rep {point.rep}",
        "-",
    )
    print(
        f"{'config':18s}: {config_path}\n"
        f"{'output':18s}: {point_dir}\n"
        f"{'records':18s}: {recordcount:,}\n"
        f"{'warmup_ops':18s}: {warmup_ops:,}\n"
        f"{'measured_ops':18s}: {fmt_int(cfg.get('measured_operations', 0))}\n"
        f"{'cache_budget_gb':18s}: {fmt_float(cfg.get('cache_data_gb', 0), 3)}\n"
        f"{'read_compaction':18s}: {'on' if bool(cfg.get('enable_compaction', False)) or float(cfg.get('update_ratio', 0.0)) > 0.0 else 'off'}",
        flush=True,
    )
    proc = subprocess.Popen(
        cmd,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        bufsize=1,
    )
    output_lines: list[str] = []
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            print(line, end="", flush=True)
            output_lines.append(line)
        proc.wait()
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
    output_text = "".join(output_lines)
    (point_dir / "driver.log").write_text(output_text)

    row: dict[str, Any] = {
        "suite": point.suite,
        "knob": point.knob,
        "x_value": point.value,
        "x_label": point.label,
        "x_order": point.value if isinstance(point.value, (int, float)) else point.rep,
        "system_key": point.system,
        "system": SYSTEM_LABELS.get(point.system, point.system),
        "rep": point.rep,
        "returncode": proc.returncode,
        "output_dir": str(point_dir),
        "config_path": str(config_path),
    }
    summary_path = point_dir / "summary.json"
    if proc.returncode == 0 and summary_path.exists():
        summary = json.loads(summary_path.read_text())
        row.update(summary)
        row["system_key"] = point.system
        row["system"] = summary.get("system", SYSTEM_LABELS.get(point.system, point.system))
        print(
            "[RESULT] "
            f"{SYSTEM_LABELS.get(point.system, point.system):14s} | "
            f"{point.knob}={point.label:>6s} | "
            f"thr={float(summary.get('scan_throughput_ops_sec', 0.0)):>10,.1f} scans/s | "
            f"avg={float(summary.get('scan_avg_us', 0.0)):>8,.2f} us | "
            f"p99={float(summary.get('scan_p99_us', 0.0)):>8,.2f} us | "
            f"rss={float(summary.get('max_rss_kb', 0.0)) / 1024 / 1024:>5.2f} GiB",
            flush=True,
        )
    else:
        row["error"] = output_text[-3000:]
        print(
            "[fail] "
            f"{SYSTEM_LABELS.get(point.system, point.system):14s} "
            f"{point.knob}={point.label:>6s} rc={proc.returncode}; "
            f"log={point_dir / 'driver.log'}",
            flush=True,
        )
    print(
        f"[case finished at] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        flush=True,
    )
    return row


def run_sweep(
    *,
    base: dict[str, Any],
    suite: str,
    knob: str,
    values: Iterable[Any],
    labels: Iterable[str],
    x_title: str,
    runner: Path,
    run_root: Path,
    systems: list[str],
    reps: int,
    python: str,
    stop_on_failure: bool,
    all_rows: list[dict[str, Any]] | None = None,
    resume: bool = False,
    rerun_existing: bool = False,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = (
        [row for row in all_rows if row.get("suite") == suite]
        if all_rows is not None
        else []
    )
    suite_dir = run_root / suite
    suite_dir.mkdir(parents=True, exist_ok=True)
    print_rule(f"SWEEP: {suite} ({knob})")
    for value, label in zip(values, labels):
        print_rule(f"POINT: {knob}={label}", "-")
        for system in systems:
            for rep in range(1, reps + 1):
                point = SweepPoint(
                    suite=suite,
                    knob=knob,
                    value=value,
                    label=str(label),
                    system=system,
                    rep=rep,
                )
                if resume and all_rows is not None and not rerun_existing:
                    existing = successful_point_row(all_rows, point)
                    if existing is not None:
                        print(
                            "[resume skip] "
                            f"{SYSTEM_LABELS.get(point.system, point.system):14s} | "
                            f"{point.knob}={point.label:>6s} | "
                            f"rep={point.rep} | "
                            f"thr={fmt_float(existing.get('scan_throughput_ops_sec', 0.0), 1)} scans/s | "
                            f"p99={fmt_float(existing.get('scan_p99_us', 0.0), 2)} us",
                            flush=True,
                        )
                        continue

                row = run_one_point(
                    base=base,
                    point=point,
                    runner=runner,
                    run_root=run_root,
                    python=python,
                )
                remove_point_rows(rows, point)
                if all_rows is not None:
                    remove_point_rows(all_rows, point)
                rows.append(row)
                if all_rows is not None:
                    all_rows.append(row)
                write_csv(suite_dir / "summary.csv", rows)
                if all_rows is not None:
                    write_csv(run_root / "summary.csv", all_rows)
                    write_lorc_range_distribution(
                        run_root / "lorc_range_distribution_median.jsonl",
                        all_rows,
                    )
                plot_sweep(
                    rows,
                    suite=suite,
                    x_title=x_title,
                    output_dir=suite_dir,
                    systems=systems,
                )
                if stop_on_failure and int(row.get("returncode", 0) or 0) != 0:
                    raise RuntimeError(f"point failed: {point}")
    write_csv(suite_dir / "summary.csv", rows)
    if all_rows is not None:
        write_csv(run_root / "summary.csv", all_rows)
        write_lorc_range_distribution(
            run_root / "lorc_range_distribution_median.jsonl",
            all_rows,
        )
    plot_sweep(
        rows,
        suite=suite,
        x_title=x_title,
        output_dir=suite_dir,
        systems=systems,
    )
    print(f"[PLOT] {suite}: {suite_dir / (suite + '_throughput_p99.png')}", flush=True)
    return rows


def run_scan_length_sweep(
    args: argparse.Namespace,
    base: dict[str, Any],
    run_root: Path,
    all_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    values = parse_csv_list(args.scan_lengths, int)
    labels = [str(v) for v in values]
    return run_sweep(
        base=base,
        suite="scan_length",
        knob="scan_length",
        values=values,
        labels=labels,
        x_title="Scan length",
        runner=args.runner,
        run_root=run_root,
        systems=args.systems,
        reps=args.reps,
        python=args.python,
        stop_on_failure=args.stop_on_failure,
        all_rows=all_rows,
        resume=args.resume,
        rerun_existing=args.rerun_existing,
    )


def run_zipfian_sweep(
    args: argparse.Namespace,
    base: dict[str, Any],
    run_root: Path,
    all_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    values = parse_csv_list(args.zipfian_values, float)
    labels = [f"{v:g}" for v in values]
    return run_sweep(
        base=base,
        suite="zipfian",
        knob="zipfian_const",
        values=values,
        labels=labels,
        x_title="Zipfian constant",
        runner=args.runner,
        run_root=run_root,
        systems=args.systems,
        reps=args.reps,
        python=args.python,
        stop_on_failure=args.stop_on_failure,
        all_rows=all_rows,
        resume=args.resume,
        rerun_existing=args.rerun_existing,
    )


def run_value_size_sweep(
    args: argparse.Namespace,
    base: dict[str, Any],
    run_root: Path,
    all_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    values = parse_csv_list(args.value_sizes, int)
    labels = [value_size_label(v) for v in values]
    return run_sweep(
        base=base,
        suite="value_size",
        knob="value_size",
        values=values,
        labels=labels,
        x_title="Value size",
        runner=args.runner,
        run_root=run_root,
        systems=args.systems,
        reps=args.reps,
        python=args.python,
        stop_on_failure=args.stop_on_failure,
        all_rows=all_rows,
        resume=args.resume,
        rerun_existing=args.rerun_existing,
    )


def run_thread_sweep(
    args: argparse.Namespace,
    base: dict[str, Any],
    run_root: Path,
    all_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    values = parse_csv_list(args.thread_values, int)
    labels = [str(v) for v in values]
    return run_sweep(
        base=base,
        suite="threads",
        knob="threads",
        values=values,
        labels=labels,
        x_title="Client threads",
        runner=args.runner,
        run_root=run_root,
        systems=args.systems,
        reps=args.reps,
        python=args.python,
        stop_on_failure=args.stop_on_failure,
        all_rows=all_rows,
        resume=args.resume,
        rerun_existing=args.rerun_existing,
    )


def run_update_ratio_sweep(
    args: argparse.Namespace,
    base: dict[str, Any],
    run_root: Path,
    all_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    values = parse_csv_list(args.update_ratios, float)
    labels = [f"{int(round(v * 100))}%" for v in values]
    return run_sweep(
        base=base,
        suite="update_ratio",
        knob="update_ratio",
        values=values,
        labels=labels,
        x_title="Update ratio",
        runner=args.runner,
        run_root=run_root,
        systems=args.systems,
        reps=args.reps,
        python=args.python,
        stop_on_failure=args.stop_on_failure,
        all_rows=all_rows,
        resume=args.resume,
        rerun_existing=args.rerun_existing,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--runner", type=Path, default=DEFAULT_RUNNER)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-id", default=datetime.now().strftime("%Y%m%d_%H%M%S"))
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--systems", default=",".join(SYSTEMS))
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument("--scan-lengths", default="5,10,20,50,100")
    parser.add_argument("--zipfian-values", default="0,0.5,0.8,0.99,1.2,1.5")
    parser.add_argument("--value-sizes", default="256,512,1024,4096,8192")
    parser.add_argument("--thread-values", default="1,2,4,8,16")
    parser.add_argument("--update-ratios", default="0,0.2,0.4,0.6,0.8")
    parser.add_argument(
        "--suites",
        default=",".join(SWEEP_FUNCS),
        help=(
            "Comma-separated sweep groups to run. Choices: "
            + ", ".join(SWEEP_FUNCS)
        ),
    )
    parser.add_argument("--stop-on-failure", action="store_true")
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Resume an existing run-id: load its summary.csv, skip successful "
            "suite/value/system/rep points, and keep appending unfinished "
            "points in the same output directory."
        ),
    )
    parser.add_argument(
        "--rerun-existing",
        action="store_true",
        help=(
            "With --resume, rerun matching points instead of skipping them; "
            "old rows for the same suite/value/system/rep are replaced."
        ),
    )
    parser.add_argument(
        "--lsbm-compaction-buffer-use-length",
        default=None,
        help=(
            "Optional LSbM-only override passed as "
            "leveldb.compaction_buffer_use_length, e.g. 0,0,0."
        ),
    )
    args = parser.parse_args()
    args.systems = [s.strip() for s in args.systems.split(",") if s.strip()]
    args.suites = [s.strip() for s in args.suites.split(",") if s.strip()]
    unknown = [s for s in args.systems if s not in SYSTEMS]
    if unknown:
        raise SystemExit(f"unknown systems: {', '.join(unknown)}")
    unknown_suites = [s for s in args.suites if s not in SWEEP_FUNCS]
    if unknown_suites:
        raise SystemExit(f"unknown suites: {', '.join(unknown_suites)}")
    if args.reps <= 0:
        raise SystemExit("--reps must be positive")
    return args


def main() -> int:
    args = parse_args()
    base = load_base_config(args.base_config)
    apply_manual_overrides(base, args)
    run_root = args.output_root / args.run_id
    run_root.mkdir(parents=True, exist_ok=True)
    print_rule("MANUAL LORC/YCSB SWEEPS")
    print(f"{'root':12s}: {run_root}", flush=True)
    print(f"{'base':12s}: {args.base_config}", flush=True)
    print(f"{'runner':12s}: {args.runner}", flush=True)
    print(f"{'systems':12s}: {', '.join(args.systems)}", flush=True)
    print(f"{'suites':12s}: {', '.join(args.suites)}", flush=True)
    print(f"{'reps':12s}: {args.reps}", flush=True)
    print(f"{'resume':12s}: {args.resume}", flush=True)
    print(f"{'rerun_exist':12s}: {args.rerun_existing}", flush=True)
    if args.lsbm_compaction_buffer_use_length is not None:
        print(
            f"{'lsbm_cb_use':12s}: {args.lsbm_compaction_buffer_use_length}",
            flush=True,
        )

    all_rows: list[dict[str, Any]] = []
    if args.resume:
        all_rows = read_csv(run_root / "summary.csv")
        print(
            f"{'loaded_rows':12s}: {len(all_rows)} from {run_root / 'summary.csv'}",
            flush=True,
        )
    func_by_name = {
        "scan_length": run_scan_length_sweep,
        "zipfian": run_zipfian_sweep,
        "value_size": run_value_size_sweep,
        "threads": run_thread_sweep,
        "update_ratio": run_update_ratio_sweep,
    }
    for suite in args.suites:
        fn = func_by_name[suite]
        fn(args, base, run_root, all_rows)
        write_csv(run_root / "summary.csv", all_rows)
        write_lorc_range_distribution(
            run_root / "lorc_range_distribution_median.jsonl", all_rows
        )
    print(f"[SUMMARY] {run_root / 'summary.csv'}", flush=True)
    range_path = run_root / "lorc_range_distribution_median.jsonl"
    if range_path.exists():
        print(f"[LORC_RANGE_DISTRIBUTION] {range_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
