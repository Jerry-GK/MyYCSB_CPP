#!/usr/bin/env python3
"""Apply LorcKV benchmark harness patches to an upstream LinkBench checkout.

The patch keeps the official request generator and workload distributions
unchanged.  It only adds an optional fixed-count warmup phase that runs inside
the same requester process, so in-memory caches are actually warmed before
statistics collection starts.
"""

from __future__ import annotations

import sys
from pathlib import Path
import re


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise SystemExit(f"Patch anchor not found in {path}: {old!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def regex_replace_once(path: Path, pattern: str, new: str, already: str) -> None:
    text = path.read_text(encoding="utf-8")
    if already in text:
        return
    text, count = re.subn(pattern, new, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise SystemExit(f"Patch regex not found in {path}: {pattern!r}")
    path.write_text(text, encoding="utf-8")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch_official_linkbench.py LINKBENCH_HOME")
    root = Path(sys.argv[1])
    config_java = root / "src/main/java/com/facebook/LinkBench/Config.java"
    request_java = root / "src/main/java/com/facebook/LinkBench/LinkBenchRequest.java"

    replace_once(
        config_java,
        '  public static final String WARMUP_TIME = "warmup_time";\n',
        '  public static final String WARMUP_TIME = "warmup_time";\n'
        '  public static final String WARMUP_REQUESTS = "warmup_requests";\n',
    )

    replace_once(
        request_java,
        "  private long warmupTime;\n",
        "  private long warmupTime;\n"
        "  private long warmupRequestLimit;\n",
    )
    replace_once(
        request_java,
        "    warmupTime = Math.max(0, ConfigUtil.getLong(props, Config.WARMUP_TIME, 0L));\n",
        "    warmupTime = Math.max(0, ConfigUtil.getLong(props, Config.WARMUP_TIME, 0L));\n"
        "    warmupRequestLimit = Math.max(0, ConfigUtil.getLong(props, Config.WARMUP_REQUESTS, 0L));\n",
    )
    replace_once(
        request_java,
        '    logger.info("Requester thread #" + requesterID + " started: will do "\n'
        '        + numRequests + " ops after " + warmupTime + " second warmup");\n',
        '    logger.info("Requester thread #" + requesterID + " started: will do "\n'
        '        + numRequests + " ops after " + warmupTime + " second warmup and "\n'
        '        + warmupRequestLimit + " warmup requests");\n',
    )
    replace_once(
        request_java,
        "    boolean warmupDone = warmupTime <= 0;\n",
        "    boolean warmupDone = warmupTime <= 0 && warmupRequestLimit <= 0;\n",
    )
    regex_replace_once(
        request_java,
        r'          logger\.info\(String\.format\("Requester #%d warming up\.  "\s*\+\n'
        r'              " %d warmup requests done\. %d/%d seconds of warmup done",\n'
        r'              requesterID, warmupRequests, \(curTime - warmupStartTime\) / 1000,\n'
        r'              warmupTime\)\);\n',
        '          logger.info(String.format("Requester #%d warming up.  " +\n'
        '              " %d/%d warmup requests done. %d/%d seconds of warmup done",\n'
        '              requesterID, warmupRequests, warmupRequestLimit,\n'
        '              (curTime - warmupStartTime) / 1000, warmupTime));\n',
        "%d/%d warmup requests done. %d/%d seconds of warmup done",
    )
    regex_replace_once(
        request_java,
        r"      if \(!warmupDone && curTime >= benchmarkStartTime\) \{\n",
        "      if (!warmupDone && ((warmupTime > 0 && curTime >= benchmarkStartTime) ||\n"
        "          (warmupRequestLimit > 0 && warmupRequests >= warmupRequestLimit))) {\n",
        "warmupRequestLimit > 0 && warmupRequests >= warmupRequestLimit",
    )
    replace_once(
        request_java,
        "        lastUpdate = curTime;\n"
        "        lastStatDisplay_ms = curTime;\n"
        "        requestsSinceLastUpdate = 0;\n",
        "        benchmarkStartTime = curTime;\n"
        "        endTime = benchmarkStartTime + maxTime * 1000;\n"
        "        lastUpdate = curTime;\n"
        "        lastStatDisplay_ms = curTime;\n"
        "        requestsSinceLastUpdate = 0;\n",
    )


if __name__ == "__main__":
    main()
