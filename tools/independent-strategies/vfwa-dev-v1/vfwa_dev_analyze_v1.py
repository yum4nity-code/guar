#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ID = "VFWA-DEV-V1"
SEED = 20260918
BOOTSTRAPS = 4000
PERMUTATIONS = 4000
H = 30
MIN_RISK = 0.01
KILL_GROSS_MEAN_MIN = 0.15
KILL_DAILY_DELTA_MIN = 0.08


def fnum(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except Exception:
        return None


def truthy(x):
    return str(x).strip().lower() in {"true", "1", "yes"}


def percentile(values, q):
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return xs[lo]
    w = pos - lo
    return xs[lo] * (1 - w) + xs[hi] * w


def non_overlap(rows):
    out = []
    next_allowed = None
    for r in sorted(rows, key=lambda z: z["entry_time"]):
        if next_allowed is None or r["entry_time"] >= next_allowed:
            out.append(r)
            next_allowed = r["entry_time"] + timedelta(minutes=H)
    return out


def gross_barrier_r(r):
    winner = r.get("hit_1p0_winner")
    minute = r.get("hit_1p0_minute")
    if winner and minute is not None and minute <= H:
        if winner == "PLUS":
            return 1.0
        if winner in {"MINUS", "AMBIGUOUS"}:
            return -1.0
    x = r.get("endpoint_r")
    if x is None:
        return None
    # If neither +/-1R barrier was touched, endpoint should be inside [-1,+1].
    # Clamp only as an integrity guard against numeric/data anomalies.
    return max(-1.0, min(1.0, x))


def cost_adjusted_r(r, synthetic_cost_price):
    g = gross_barrier_r(r)
    risk = r.get("risk")
    if g is None or risk is None or risk <= 0:
        return None
    return g - synthetic_cost_price / risk


def stats(values):
    xs = [x for x in values if x is not None and math.isfinite(x)]
    if not xs:
        return {
            "n": 0,
            "mean_r": None,
            "median_r": None,
            "sum_r": None,
            "pf": None,
            "win_pct": None,
            "max_drawdown_r": None,
        }
    gw = sum(x for x in xs if x > 0)
    gl = -sum(x for x in xs if x < 0)
    cum = 0.0
    peak = 0.0
    dd = 0.0
    for x in xs:
        cum += x
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return {
        "n": len(xs),
        "mean_r": sum(xs) / len(xs),
        "median_r": statistics.median(xs),
        "sum_r": sum(xs),
        "pf": gw / gl if gl > 0 else None,
        "win_pct": 100.0 * sum(x > 0 for x in xs) / len(xs),
        "max_drawdown_r": dd,
    }


def daily_values(rows, vf):
    d = defaultdict(list)
    for r in rows:
        x = vf(r)
        if x is not None and math.isfinite(x):
            d[r["date"]].append(x)
    return d


def bootstrap_mean(rows, vf, seed):
    day = daily_values(rows, vf)
    blocks = [(len(v), sum(v)) for v in day.values() if v]
    if not blocks:
        return {"days": 0, "ci95": [None, None], "median": None}
    rng = random.Random(seed)
    means = []
    for _ in range(BOOTSTRAPS):
        chosen = rng.choices(blocks, k=len(blocks))
        n = sum(c for c, _ in chosen)
        s = sum(v for _, v in chosen)
        means.append(s / n if n else 0.0)
    return {
        "days": len(blocks),
        "ci95": [percentile(means, .025), percentile(means, .975)],
        "median": percentile(means, .5),
    }


def daily_delta(signal_rows, control_rows, vf, seed):
    ds = daily_values(signal_rows, vf)
    dc = daily_values(control_rows, vf)
    common = sorted(set(ds) & set(dc))
    vals = [
        (sum(ds[d]) / len(ds[d])) - (sum(dc[d]) / len(dc[d]))
        for d in common
    ]
    if not vals:
        return {
            "common_days": 0,
            "mean_daily_delta_r": None,
            "ci95": [None, None],
            "signflip_p_one_sided": None,
        }

    obs = sum(vals) / len(vals)
    rng = random.Random(seed)
    boots = []
    for _ in range(BOOTSTRAPS):
        z = rng.choices(vals, k=len(vals))
        boots.append(sum(z) / len(z))

    extreme = 0
    for _ in range(PERMUTATIONS):
        z = [x if rng.random() < .5 else -x for x in vals]
        if sum(z) / len(z) >= obs:
            extreme += 1

    return {
        "common_days": len(vals),
        "mean_daily_delta_r": obs,
        "ci95": [percentile(boots, .025), percentile(boots, .975)],
        "signflip_p_one_sided": (extreme + 1) / (PERMUTATIONS + 1),
    }


def bucket(x, cuts):
    if x is None:
        return "NA"
    for i, c in enumerate(cuts):
        if x < c:
            return i
    return len(cuts)


def matched_delta(signal_rows, control_rows):
    # Match on pre-registered observable anatomy/background, not on RV ratio
    # because signal (>1.5) and control (<=1.0) ratio domains do not overlap.
    def key(r):
        return (
            r["year"],
            r["session"],
            r["direction"],
            bucket(r.get("range_atr"), [1.35, 1.5, 1.75, 2.0, 2.5, 3.0]),
            bucket(r.get("wick_frac"), [.50, .55, .60, .70, .80]),
        )

    sg = defaultdict(list)
    cg = defaultdict(list)
    for r in signal_rows:
        x = gross_barrier_r(r)
        if x is not None:
            sg[key(r)].append(x)
    for r in control_rows:
        x = gross_barrier_r(r)
        if x is not None:
            cg[key(r)].append(x)

    num = 0.0
    den = 0
    strata = 0
    for k in set(sg) & set(cg):
        ns = len(sg[k])
        nc = len(cg[k])
        w = min(ns, nc)
        if not w:
            continue
        num += w * ((sum(sg[k]) / ns) - (sum(cg[k]) / nc))
        den += w
        strata += 1

    return {
        "matched_support": den,
        "matched_strata": strata,
        "weighted_mean_delta_r": num / den if den else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--output-dir", required=True, type=Path)
    args = ap.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    raw = defaultdict(list)
    input_rows = 0
    rejected_gap = defaultdict(int)
    rejected_risk = defaultdict(int)

    with args.input.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            input_rows += 1
            if row.get("family") != "VFWA":
                continue
            variant = row.get("variant")
            risk = fnum(row.get("risk"))
            if risk is None or risk < MIN_RISK:
                rejected_risk[variant] += 1
                continue
            if truthy(row.get("outcome_crossed_calendar_gap")):
                rejected_gap[variant] += 1
                continue

            t = datetime.fromisoformat(row["entry_time"])
            if t.year not in {2017, 2018, 2019, 2020}:
                raise RuntimeError(f"non-DEV row reached analyzer: {t.isoformat()}")

            z = {
                "entry_time": t,
                "date": t.date().isoformat(),
                "year": t.year,
                "session": row.get("session") or "UNKNOWN",
                "direction": row.get("direction"),
                "risk": risk,
                "ratio": fnum(row.get("event_strength")),
                "wick_frac": fnum(row.get("event_aux")),
                "range_atr": fnum(row.get("signal_bar_range_atr")),
                "endpoint_r": fnum(row.get("h30_endpoint_r")),
                "hit_1p0_winner": row.get("hit_1p0_winner") or None,
                "hit_1p0_minute": fnum(row.get("hit_1p0_minute")),
            }
            raw[variant].append(z)

    signal = non_overlap(raw["SIGNAL"])
    control = non_overlap(raw["CONTROL_LOW_FRONT"])

    gross_signal = stats([gross_barrier_r(r) for r in signal])
    gross_control = stats([gross_barrier_r(r) for r in control])
    net010_signal = stats([cost_adjusted_r(r, .10) for r in signal])
    net010_control = stats([cost_adjusted_r(r, .10) for r in control])
    net020_signal = stats([cost_adjusted_r(r, .20) for r in signal])
    net020_control = stats([cost_adjusted_r(r, .20) for r in control])

    boot_signal = bootstrap_mean(signal, gross_barrier_r, SEED)
    delta_gross = daily_delta(signal, control, gross_barrier_r, SEED + 1)
    match = matched_delta(signal, control)

    yearly = []
    for y in (2017, 2018, 2019, 2020):
        sy = [r for r in signal if r["year"] == y]
        cy = [r for r in control if r["year"] == y]
        yearly.append({
            "year": y,
            "signal_gross": stats([gross_barrier_r(r) for r in sy]),
            "control_gross": stats([gross_barrier_r(r) for r in cy]),
            "daily_delta": daily_delta(sy, cy, gross_barrier_r, SEED + y),
        })

    kill_checks = {
        "signal_gross_mean_r_ge_0p15": (
            gross_signal["mean_r"] is not None
            and gross_signal["mean_r"] >= KILL_GROSS_MEAN_MIN
        ),
        "daily_signal_minus_control_ge_0p08": (
            delta_gross["mean_daily_delta_r"] is not None
            and delta_gross["mean_daily_delta_r"] >= KILL_DAILY_DELTA_MIN
        ),
    }

    report = {
        "schema": 1,
        "status": "COMPLETE",
        "id": ID,
        "scope": "DEV_2017_2020_ONLY",
        "input_rows": input_rows,
        "primary_execution": "1R TP / 1R SL / 30m time-stop; ambiguous same-M1 TP+SL scored -1R",
        "non_overlap": "30 wall-clock minutes separately by variant",
        "signal": {
            "gross": gross_signal,
            "synthetic_cost_0p10_price_units": net010_signal,
            "synthetic_cost_0p20_price_units": net020_signal,
            "daily_block_bootstrap_gross": boot_signal,
        },
        "control": {
            "gross": gross_control,
            "synthetic_cost_0p10_price_units": net010_control,
            "synthetic_cost_0p20_price_units": net020_control,
        },
        "signal_vs_control_gross": delta_gross,
        "coarsened_matched_control_gross": match,
        "yearly": yearly,
        "rejected_gap": dict(rejected_gap),
        "rejected_risk": dict(rejected_risk),
        "kill_gate": {
            "thresholds": {
                "signal_gross_mean_r_min": KILL_GROSS_MEAN_MIN,
                "signal_minus_control_mean_daily_r_min": KILL_DAILY_DELTA_MIN,
            },
            "checks": kill_checks,
            "dev_pass": all(kill_checks.values()),
        },
        "protected_2021_2022_opened": False,
        "protected_2023_plus_opened": False,
        "protected_2026_opened": False,
    }

    out = args.output_dir / "vfwa_dev_analysis.json"
    out.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    print("=== VFWA DEV V1 ===")
    print(f"INPUT ROWS: {input_rows}")
    print("2021-2022 ACCESSED: FALSE")
    print("2023+ ACCESSED: FALSE")
    print("2026 ACCESSED: FALSE")
    print(
        f"SIGNAL | N={gross_signal['n']} | Gross MeanR={gross_signal['mean_r']} "
        f"PF={gross_signal['pf']} | Cost.10 MeanR={net010_signal['mean_r']} "
        f"| Cost.20 MeanR={net020_signal['mean_r']}"
    )
    print(
        f"CONTROL | N={gross_control['n']} | Gross MeanR={gross_control['mean_r']} "
        f"PF={gross_control['pf']}"
    )
    print(
        f"DELTA/day={delta_gross['mean_daily_delta_r']} | "
        f"CI95={delta_gross['ci95']} | "
        f"p={delta_gross['signflip_p_one_sided']}"
    )
    print(
        f"MATCHED delta={match['weighted_mean_delta_r']} "
        f"| support={match['matched_support']}"
    )
    print(
        f"KILL GATE | gross>=0.15={kill_checks['signal_gross_mean_r_ge_0p15']} "
        f"| delta/day>=0.08={kill_checks['daily_signal_minus_control_ge_0p08']} "
        f"| DEV_PASS={all(kill_checks.values())}"
    )
    print(f"REPORT: {out}")


if __name__ == "__main__":
    raise SystemExit(main())
