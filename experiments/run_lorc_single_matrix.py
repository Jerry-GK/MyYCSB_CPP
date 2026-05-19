#!/usr/bin/env python3
"""Run the single-config LORC/YCSB experiment matrix.

This script deliberately drives experiments through run_lorc_single_config.py
so every point uses the same load/warmup/run path as an individually specified
JSON config.  It writes one generated JSON file per point, runs the point, and
collects summary.json into a matrix CSV.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "experiments" / "configs" / "lorc_single_test_default.json"
SCRIPT = ROOT / "experiments" / "run_lorc_single_config.py"
OUT_ROOT = ROOT / "result" / "single_matrix"

SYSTEMS = ["rocksdb", "rocksdb_lorc", "blobdb", "blobdb_lorc", "lsbm"]
LORC_SYSTEMS = ["rocksdb_lorc", "blobdb_lorc"]
SYSTEM_ENGINES = {
    "rocksdb": "rocksdb",
    "rocksdb_lorc": "rocksdb",
    "blobdb": "blobdb",
    "blobdb_lorc": "blobdb",
    "lsbm": "lsbm",
}


@dataclass(frozen=True)
class Point:
    suite: str
    x_value: str
    x_label: str
    system: str
    cfg: dict[str, Any]
    rep: int = 1

    @property
    def name(self) -> str:
        safe = lambda s: str(s).replace("/", "_").replace(".", "p")
        return (
            f"{safe(self.suite)}__{safe(self.x_value)}__"
            f"{safe(self.system)}__r{self.rep}"
        )


def load_base(path: Path) -> dict[str, Any]:
    cfg = json.loads(path.read_text())
    cfg["build_source"] = False
    cfg["enable_compaction"] = False
    cfg["directio"] = False
    return cfg


def make_point(
    base: dict[str, Any],
    *,
    suite: str,
    x_value: Any,
    x_label: str | None = None,
    system: str,
    rep: int = 1,
    **overrides: Any,
) -> Point:
    cfg = dict(base)
    cfg.update(overrides)
    cfg["system"] = system
    cfg["build_source"] = bool(overrides.get("build_source", False))
    cfg["enable_compaction"] = float(cfg.get("update_ratio", 0.0)) > 0.0
    return Point(
        suite=suite,
        x_value=str(x_value),
        x_label=str(x_label if x_label is not None else x_value),
        system=system,
        cfg=cfg,
        rep=rep,
    )


def build_points(
    base: dict[str, Any],
    suites: set[str],
    reps: int,
    systems: list[str],
) -> list[Point]:
    points: list[Point] = []
    selected_systems = systems
    lru_systems = [system for system in systems if system in LORC_SYSTEMS]

    def add_for_systems(
        suite: str,
        x_value: Any,
        *,
        x_label: str | None = None,
        systems: list[str] | None = None,
        **overrides: Any,
    ) -> None:
        point_systems = systems if systems is not None else selected_systems
        for rep in range(1, reps + 1):
            for system in point_systems:
                points.append(
                    make_point(
                        base,
                        suite=suite,
                        x_value=x_value,
                        x_label=x_label,
                        system=system,
                        rep=rep,
                        **overrides,
                    )
                )

    if "scan_length" in suites:
        for value in [5, 10, 20, 50, 100]:
            add_for_systems("scan_length", value, scan_length=value)

    if "value_size" in suites:
        for value, label in [(256, "256B"), (512, "512B"), (1024, "1KB"), (4096, "4KB"), (8192, "8KB")]:
            add_for_systems("value_size", value, x_label=label, value_size=value, build_source=True)

    if "zipfian" in suites:
        for value in [0.0, 0.5, 0.8, 0.99, 1.2, 1.5]:
            add_for_systems("zipfian", value, zipfian_const=value)

    if "cache_size" in suites:
        for value, label in [(0.25, "256MB"), (0.5, "512MB"), (1.0, "1GB"), (2.0, "2GB")]:
            add_for_systems("cache_size", value, x_label=label, cache_data_gb=value)

    if "update_ratio" in suites:
        for value, label in [(0.0, "0%"), (0.05, "5%"), (0.10, "10%"), (0.20, "20%"), (0.50, "50%")]:
            add_for_systems("update_ratio", value, x_label=label, update_ratio=value)

    if "lru_policy" in suites:
        for value, label in [
            ("boundary_lru", "Boundary-LRU"),
            ("physical_lru", "Physical LRU"),
            ("shortest_range", "Shortest range"),
        ]:
            add_for_systems(
                "lru_policy",
                value,
                x_label=label,
                systems=lru_systems,
                lru_policy=value,
            )

    if "threads" in suites:
        for value in [1, 2, 4, 8, 16]:
            add_for_systems("threads", value, threads=value)

    return points


def run_point(point: Point, run_root: Path, force: bool) -> dict[str, Any]:
    out_dir = run_root / point.name
    cfg_dir = run_root / "configs"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = cfg_dir / f"{point.name}.json"
    cfg = dict(point.cfg)
    cfg["output_dir"] = str(out_dir)
    cfg_path.write_text(json.dumps(cfg, indent=2) + "\n")

    summary_path = out_dir / "summary.json"
    if summary_path.exists() and not force:
        summary = json.loads(summary_path.read_text())
        summary["skipped_existing"] = 1
    else:
        out_dir.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, str(SCRIPT), "--config", str(cfg_path)]
        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        (out_dir / "matrix_driver.log").write_text(proc.stdout)
        if proc.returncode != 0:
            return {
                "suite": point.suite,
                "x_value": point.x_value,
                "x_label": point.x_label,
                "system": point.system,
                "rep": point.rep,
                "returncode": proc.returncode,
                "output_dir": str(out_dir),
                "error": proc.stdout[-2000:],
            }
        summary = json.loads(summary_path.read_text())
        summary["skipped_existing"] = 0

    summary.update(
        {
            "suite": point.suite,
            "x_value": point.x_value,
            "x_label": point.x_label,
            "system_key": point.system,
            "rep": point.rep,
            "returncode": 0,
            "output_dir": str(out_dir),
        }
    )
    return summary


def source_key(point: Point) -> tuple[Any, ...]:
    cfg = point.cfg
    return (
        SYSTEM_ENGINES[point.system],
        cfg.get("total_data_gb", 4.0),
        cfg.get("key_size", 24),
        cfg.get("value_size", 1024),
    )


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    keys: list[str] = []
    preferred = [
        "suite", "x_value", "x_label", "system", "system_key", "rep",
        "returncode", "throughputops/sec", "scan_throughput_ops_sec",
        "scan_count", "scan_avg_us", "scan_p99_us", "update_count",
        "update_avg_us", "update_p99_us", "max_rss_kb", "fs_inputs",
        "fs_outputs", "lorc_full_hit_rate", "lorc_hit_size_rate",
        "lorc_physical_range_count", "lorc_logical_range_count",
        "lorc_total_range_length", "lorc_current_size",
        "scan_length", "value_size", "zipfian_const", "cache_bytes",
        "update_ratio", "threads", "lru_policy", "warmup_operations",
        "point_expansion_entries", "measured_operations", "output_dir", "error",
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--suites",
        default="scan_length,zipfian,cache_size,update_ratio,lru_policy,threads",
        help=(
            "Comma-separated suites: scan_length,value_size,zipfian,"
            "cache_size,update_ratio,lru_policy,threads,all"
        ),
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument(
        "--systems",
        default=",".join(SYSTEMS),
        help=(
            "Comma-separated systems to run: rocksdb,rocksdb_lorc,"
            "blobdb,blobdb_lorc,lsbm"
        ),
    )
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base = load_base(args.base_config)
    if args.suites == "all":
        suites = {
            "scan_length", "value_size", "zipfian", "cache_size",
            "update_ratio", "lru_policy", "threads",
        }
    else:
        suites = {s.strip() for s in args.suites.split(",") if s.strip()}
    systems = [s.strip() for s in args.systems.split(",") if s.strip()]
    invalid_systems = [s for s in systems if s not in SYSTEMS]
    if invalid_systems:
        raise SystemExit(f"unknown systems: {','.join(invalid_systems)}")
    run_id = args.run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    run_root = OUT_ROOT / run_id
    run_root.mkdir(parents=True, exist_ok=True)

    points = build_points(base, suites=suites, reps=args.reps, systems=systems)
    (run_root / "matrix_points.json").write_text(
        json.dumps([p.__dict__ | {"cfg": p.cfg} for p in points], indent=2)
    )
    print(f"[matrix] run_root={run_root}")
    print(f"[matrix] suites={','.join(sorted(suites))} systems={','.join(systems)} points={len(points)} jobs={args.jobs}")

    rows: list[dict[str, Any]] = []
    if args.jobs <= 1:
        built_sources: set[tuple[Any, ...]] = set()
        for idx, point in enumerate(points, 1):
            if point.cfg.get("build_source", False):
                key = source_key(point)
                if key in built_sources:
                    point = Point(
                        suite=point.suite,
                        x_value=point.x_value,
                        x_label=point.x_label,
                        system=point.system,
                        cfg={**point.cfg, "build_source": False},
                        rep=point.rep,
                    )
                else:
                    built_sources.add(key)
            print(f"[matrix] {idx}/{len(points)} {point.name}")
            rows.append(run_point(point, run_root, args.force))
            write_csv(run_root / "summary.csv", rows)
    else:
        if any(point.cfg.get("build_source", False) for point in points):
            raise SystemExit(
                "--jobs > 1 is disabled for suites that build source databases; "
                "run sequentially so each shared source is loaded once"
            )
        with ThreadPoolExecutor(max_workers=args.jobs) as executor:
            futs = {
                executor.submit(run_point, point, run_root, args.force): point
                for point in points
            }
            for idx, fut in enumerate(as_completed(futs), 1):
                point = futs[fut]
                try:
                    row = fut.result()
                except Exception as exc:  # keep long batch alive
                    row = {
                        "suite": point.suite,
                        "x_value": point.x_value,
                        "x_label": point.x_label,
                        "system_key": point.system,
                        "rep": point.rep,
                        "returncode": -1,
                        "error": repr(exc),
                    }
                rows.append(row)
                print(
                    f"[matrix] done {idx}/{len(points)} {point.name} "
                    f"rc={row.get('returncode')}"
                )
                write_csv(run_root / "summary.csv", rows)

    write_csv(run_root / "summary.csv", rows)
    failed = [r for r in rows if int(r.get("returncode", 0) or 0) != 0]
    print(f"[matrix] complete rows={len(rows)} failed={len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
