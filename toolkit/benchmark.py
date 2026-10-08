"""Before/after performance tests.

System test: short synthetic checks of the things the tweaks affect (timer wake-up delay,
CPU ramp-up, background load). In-game test: records real frame times from Fortnite with
Intel PresentMon (open source, reads Windows' own graphics events; it never touches the game).
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
import statistics
import sys
import tempfile
import time
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

import psutil

from .winutils import APP_DIR, IS_WINDOWS, require_windows, run

Log = Callable[[str], None]

RESULTS_FILE = APP_DIR / "benchmarks.json"
GAME_EXE = "FortniteClient-Win64-Shipping.exe"

PRESENTMON_VERSION = "2.3.0"
PRESENTMON_URL = (f"https://github.com/GameTechDev/PresentMon/releases/download/"
                  f"v{PRESENTMON_VERSION}/PresentMon-{PRESENTMON_VERSION}-x64.exe")
PRESENTMON_SHA256 = ""  # filled in once verified by the CI build; empty = not checked

HIGHER, LOWER = True, False

# Run-to-run noise. A change counts only if it beats the absolute floor (NOISE), the relative
# floor (REL_NOISE, percent; 3% when not listed) and the spread the test itself measured
# while repeating that measurement (stored under "_noise" in each result).
NOISE = {"bg_cpu": 2.0, "processes": 3, "ram_used": 0.2,
         "sleep_avg": 0.05, "sleep_p95": 0.15, "stutters": 1.0}
REL_NOISE = {"sleep_avg": 15, "sleep_p95": 25, "cpu_burst": 10, "cpu_score": 6, "stutters": 20}

# key: (name, unit, higher is better)
METRICS: dict[str, tuple[str, str, bool | None]] = {
    "fps_avg": ("Average FPS", "", HIGHER),
    "fps_1low": ("1% low FPS", "", HIGHER),
    "fps_01low": ("0.1% low FPS", "", HIGHER),
    "frametime_avg": ("Average frame time", "ms", LOWER),
    "stutters": ("Stutters per minute", "", LOWER),
    "frames": ("Frames recorded", "", None),
    "sleep_avg": ("Timer wake-up delay (average)", "ms", LOWER),
    "sleep_p95": ("Timer wake-up delay (slowest 5%)", "ms", LOWER),
    "cpu_burst": ("CPU burst time", "ms", LOWER),
    "cpu_score": ("Sustained CPU score", "", HIGHER),
    "bg_cpu": ("Background CPU use", "%", LOWER),
    "processes": ("Running processes", "", LOWER),
    "ram_used": ("RAM in use", "GB", LOWER),
}


# --------------------------------------------------------------------------- results

def load_results() -> list[dict]:
    try:
        return json.loads(RESULTS_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        return []


def save_result(kind: str, label: str, metrics: dict) -> dict:
    result = {
        "id": uuid.uuid4().hex[:8],
        "kind": kind,
        "label": label.strip() or "Unnamed",
        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "metrics": metrics,
    }
    results = load_results() + [result]
    APP_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_FILE.write_text(json.dumps(results, indent=2), "utf-8")
    return result


def clear_results() -> None:
    RESULTS_FILE.unlink(missing_ok=True)


def _fmt(value: float) -> str:
    return f"{value:,.0f}" if abs(value) >= 100 or float(value).is_integer() else f"{value:,.2f}"


def compare(a: dict, b: dict) -> list[tuple[str, str, str, str, str]]:
    """Rows of (metric name, A, B, change text, verdict) for metrics both runs have.

    verdict is "better", "worse" or "same". Changes inside the metric's noise thresholds
    count as the same, because that's normal run-to-run variation.
    """
    rows = []
    for key, (name, unit, higher_better) in METRICS.items():
        if key not in a["metrics"] or key not in b["metrics"]:
            continue
        va, vb = a["metrics"][key], b["metrics"][key]
        suffix = f" {unit}" if unit else ""
        if higher_better is None or va == 0:
            change, verdict = "", "same"
        else:
            pct = (vb - va) / abs(va) * 100
            improved = pct > 0 if higher_better else pct < 0
            measured = max(a["metrics"].get("_noise", {}).get(key, 0),
                           b["metrics"].get("_noise", {}).get(key, 0))
            rel_floor = max(REL_NOISE.get(key, 3), 1.5 * measured)
            if abs(pct) < rel_floor or abs(vb - va) < NOISE.get(key, 0):
                change, verdict = f"{pct:+.1f}%  ≈ same", "same"
            else:
                change = f"{pct:+.1f}%  {'better' if improved else 'worse'}"
                verdict = "better" if improved else "worse"
        rows.append((name, _fmt(va) + suffix, _fmt(vb) + suffix, change, verdict))
    return rows


# --------------------------------------------------------------------------- system test

def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return ordered[index]


def _spread(values: list[float]) -> float:
    """How much repeated measurements disagree, as a percent of their middle value."""
    middle = statistics.median(values)
    if middle == 0 or len(values) < 2:
        return 0.0
    return round((_percentile(values, 75) - _percentile(values, 25)) / middle * 100, 1)


def _busy_work(n: int = 200_000) -> int:
    x = 0
    for i in range(n):
        x = (x * 31 + i) & 0xFFFFFFFF
    return x


def system_test(log: Log) -> dict:
    """About 12 seconds. Close games and big downloads first for a fair result."""
    metrics: dict = {}
    noise: dict[str, float] = {}

    log("    1/4  timer wake-up delay (how quickly Windows wakes a waiting program)…")
    overshoot, deadline = [], time.perf_counter() + 6
    while len(overshoot) < 1500 and time.perf_counter() < deadline:
        start = time.perf_counter()
        time.sleep(0.001)
        overshoot.append(max(0.0, (time.perf_counter() - start) * 1000 - 1.0))
    metrics["sleep_avg"] = round(statistics.mean(overshoot), 3)
    chunk = max(1, len(overshoot) // 5)
    noise["sleep_avg"] = _spread([statistics.mean(overshoot[i:i + chunk])
                                  for i in range(0, chunk * 5, chunk)])
    metrics["sleep_p95"] = round(_percentile(overshoot, 95), 3)

    log("    2/4  CPU burst time (how fast the CPU ramps up from idle)…")
    bursts = []
    for _ in range(25):
        time.sleep(0.12)  # let the CPU drop back to idle, like between game frames
        start = time.perf_counter()
        _busy_work()
        bursts.append((time.perf_counter() - start) * 1000)
    metrics["cpu_burst"] = round(statistics.median(bursts), 2)
    noise["cpu_burst"] = _spread(bursts)

    log("    3/4  sustained CPU speed…")
    windows = []
    for warmup in (True, False, False, False, False, False):
        done, end = 0, time.perf_counter() + (0.5 if warmup else 0.6)
        while time.perf_counter() < end:
            _busy_work(20_000)
            done += 1
        if not warmup:
            windows.append(done)
    metrics["cpu_score"] = round(statistics.median(windows) / 0.6)
    noise["cpu_score"] = _spread(windows)

    log("    4/4  background load (what other programs are using)…")
    samples = [psutil.cpu_percent(interval=0.5) for _ in range(6)]
    metrics["bg_cpu"] = round(statistics.median(samples), 1)
    noise["bg_cpu"] = _spread(samples)
    metrics["processes"] = len(psutil.pids())
    metrics["ram_used"] = round(psutil.virtual_memory().used / 1024 ** 3, 2)

    metrics["_noise"] = noise
    return metrics


# --------------------------------------------------------------------------- in-game test

def presentmon_path() -> Path | None:
    bundled = Path(getattr(sys, "_MEIPASS", "")) / "PresentMon.exe"
    if getattr(sys, "frozen", False) and bundled.exists():
        return bundled
    local = APP_DIR / "PresentMon.exe"
    return local if local.exists() else None


def download_presentmon(log: Log) -> Path:
    target = APP_DIR / "PresentMon.exe"
    log(f"    downloading PresentMon {PRESENTMON_VERSION} from Intel's GitHub…")
    APP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".download")
    with urllib.request.urlopen(PRESENTMON_URL, timeout=60) as response, tmp.open("wb") as fh:
        shutil.copyfileobj(response, fh)
    digest = hashlib.sha256(tmp.read_bytes()).hexdigest()
    if PRESENTMON_SHA256 and digest.lower() != PRESENTMON_SHA256.lower():
        tmp.unlink(missing_ok=True)
        raise RuntimeError("downloaded PresentMon doesn't match the expected checksum")
    tmp.replace(target)
    return target


def game_running() -> bool:
    for proc in psutil.process_iter(["name"]):
        if (proc.info.get("name") or "").lower() == GAME_EXE.lower():
            return True
    return False


def _beep(freq: int) -> None:
    if IS_WINDOWS:
        import winsound
        winsound.Beep(freq, 180)


def frame_metrics(frame_times: list[float], seconds: float) -> dict:
    avg_ms = statistics.mean(frame_times)
    median = statistics.median(frame_times)
    stutters = sum(1 for ft in frame_times if ft > max(2.5 * median, median + 8))
    return {
        "fps_avg": round(1000 / avg_ms, 1),
        "fps_1low": round(1000 / _percentile(frame_times, 99), 1),
        "fps_01low": round(1000 / _percentile(frame_times, 99.9), 1),
        "frametime_avg": round(avg_ms, 2),
        "stutters": round(stutters / (seconds / 60), 1),
        "frames": len(frame_times),
    }


def read_frame_times(csv_path: Path) -> list[float]:
    frame_times = []
    with csv_path.open(newline="", encoding="utf-8", errors="replace") as fh:
        for row in csv.DictReader(fh):
            if (row.get("Application") or "").lower() != GAME_EXE.lower():
                continue
            value = row.get("MsBetweenPresents") or row.get("FrameTime")
            try:
                ms = float(value)
            except (TypeError, ValueError):
                continue
            if 0 < ms < 1000:
                frame_times.append(ms)
    return frame_times


def game_test(log: Log, seconds: int, countdown: int = 10) -> dict:
    require_windows()
    if not game_running():
        raise RuntimeError("Fortnite isn't running. Start it, then run the test again")
    exe = presentmon_path() or download_presentmon(log)

    log(f"    switch to Fortnite now, recording starts in {countdown} seconds (you'll hear a beep)")
    time.sleep(countdown)
    _beep(880)
    log(f"    recording for {seconds} seconds, play normally…")

    out_dir = Path(tempfile.mkdtemp(prefix="fpstoolkit-"))
    csv_path = out_dir / "frames.csv"
    try:
        rc, out = run([
            str(exe), "--process_name", GAME_EXE, "--output_file", str(csv_path),
            "--timed", str(seconds), "--terminate_after_timed", "--terminate_on_proc_exit",
            "--stop_existing_session", "--session_name", "FPSToolkit",
            "--no_console_stats", "--v1_metrics",
        ], timeout=seconds + 60)
        _beep(660)
        if not csv_path.exists():
            raise RuntimeError(f"PresentMon didn't record anything (exit code {rc}): {out.strip()[-300:]}")
        frame_times = read_frame_times(csv_path)
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)

    if len(frame_times) < 100:
        raise RuntimeError("too few frames recorded. Make sure Fortnite is on screen while recording")
    metrics = frame_metrics(frame_times, seconds)
    log(f"    {metrics['fps_avg']:.0f} FPS average, {metrics['fps_1low']:.0f} FPS 1% low, "
        f"{metrics['stutters']:.1f} stutters/min")
    return metrics

