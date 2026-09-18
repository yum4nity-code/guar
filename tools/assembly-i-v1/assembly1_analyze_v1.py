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

ID = "GUARDIAN-ASSEMBLY-I-V1"
SEED = 20260918
BOOTSTRAPS = 4000
PERMUTATIONS = 4000
MIN_RISK = 0.01
PRIMARY_H = 60
SECONDARY = (30, 120)

STRATEGIES = [
    {"family": "AS1_SKEW_KURT", "control": "CONTROL_OPPOSITE"},
    {"family": "AS2_SKEW_VOV", "control": "CONTROL_OPPOSITE"},
    {"family": "AS3_TAIL_KURT", "control": "CONTROL_OPPOSITE"},
]

GATE = {
    "oos_n_min": 60,
    "cost_010_pf_min": 1.10,
    "cost_010_mean_r_gt": 0.0,
    "cost_020_pf_min": 1.05,
    "cost_020_mean_r_gt": 0.0,
    "positive_years_min": 2,
    "daily_bootstrap_signal_ci95_lower_gt": 0.0,
    "mean_r_without_top1pct_winners_gt": 0.0,
    "control_delta_gt": 0.0,
    "control_delta_ci95_lower_gt": 0.0,
    "matched_control_delta_gt": 0.0,
    "bh_q_max": 0.10,
}


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


def non_overlap(rows, horizon):
    out = []
    next_allowed = None
    for r in sorted(rows, key=lambda z: z["entry_time"]):
        if next_allowed is None or r["entry_time"] >= next_allowed:
            out.append(r)
            next_allowed = r["entry_time"] + timedelta(minutes=horizon)
    return out


def rvalue(r, horizon, cost_price):
    raw = r.get(f"h{horizon}_endpoint_r")
    risk = r.get("risk")
    if raw is None or risk in (None, 0):
        return None
    return raw - cost_price / risk


def stats(values):
    xs = [x for x in values if x is not None and math.isfinite(x)]
    if not xs:
        return {
            "n": 0, "mean_r": None, "median_r": None, "pf": None,
            "sum_r": None, "win_pct": None, "max_drawdown_r": None,
        }

    gross_win = sum(x for x in xs if x > 0)
    gross_loss = -sum(x for x in xs if x < 0)
    cum = peak = max_dd = 0.0
    for x in xs:
        cum += x
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    return {
        "n": len(xs),
        "mean_r": sum(xs) / len(xs),
        "median_r": statistics.median(xs),
        "pf": gross_win / gross_loss if gross_loss > 0 else None,
        "sum_r": sum(xs),
        "win_pct": 100.0 * sum(x > 0 for x in xs) / len(xs),
        "max_drawdown_r": max_dd,
    }


def daily_values(rows, value_fn):
    out = defaultdict(list)
    for r in rows:
        x = value_fn(r)
        if x is not None and math.isfinite(x):
            out[r["date"]].append(x)
    return out


def bootstrap_signal(rows, value_fn, seed):
    day = daily_values(rows, value_fn)
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


def daily_delta(signal_rows, control_rows, value_fn, seed):
    ds = daily_values(signal_rows, value_fn)
    dc = daily_values(control_rows, value_fn)
    common = sorted(set(ds) & set(dc))
    diffs = [
        sum(ds[d]) / len(ds[d]) - sum(dc[d]) / len(dc[d])
        for d in common
    ]
    if not diffs:
        return {
            "common_days": 0,
            "mean_daily_delta": None,
            "ci95": [None, None],
            "signflip_p_one_sided": None,
        }

    obs = sum(diffs) / len(diffs)
    rng = random.Random(seed)

    boots = []
    for _ in range(BOOTSTRAPS):
        z = rng.choices(diffs, k=len(diffs))
        boots.append(sum(z) / len(z))

    extreme = 0
    for _ in range(PERMUTATIONS):
        z = [x if rng.random() < .5 else -x for x in diffs]
        if sum(z) / len(z) >= obs:
            extreme += 1

    return {
        "common_days": len(diffs),
        "mean_daily_delta": obs,
        "ci95": [percentile(boots, .025), percentile(boots, .975)],
        "signflip_p_one_sided": (extreme + 1) / (PERMUTATIONS + 1),
    }


def matched_delta(signal_rows, control_rows, value_fn):
    sm = {r["entry_time"]: value_fn(r) for r in signal_rows}
    cm = {r["entry_time"]: value_fn(r) for r in control_rows}
    keys = [
        k for k in sm.keys() & cm.keys()
        if sm[k] is not None and cm[k] is not None
    ]
    return {
        "matched_support": len(keys),
        "matched_strata": len(keys),
        "weighted_mean_delta": (
            sum(sm[k] - cm[k] for k in keys) / len(keys)
            if keys else None
        ),
    }


def concentration(values):
    xs = [x for x in values if x is not None and math.isfinite(x)]
    if not xs:
        return {"mean_r_without_top_1pct_winners": None}

    n_remove = max(1, math.ceil(.01 * len(xs)))
    indices = sorted(range(len(xs)), key=lambda i: xs[i], reverse=True)[:n_remove]
    excluded = set(indices)
    remaining = [x for i, x in enumerate(xs) if i not in excluded]

    return {
        "mean_r_without_top_1pct_winners": (
            sum(remaining) / len(remaining) if remaining else None
        ),
    }


def bh(pmap):
    values = sorted((p, k) for k, p in pmap.items() if p is not None)
    m = len(values)
    out = {k: None for k in pmap}
    if not m:
        return out

    tmp = []
    for i, (p, key) in enumerate(values, 1):
        tmp.append((key, min(1.0, p * m / i)))

    running = 1.0
    for key, q in reversed(tmp):
        running = min(running, q)
        out[key] = running
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--output-dir", required=True, type=Path)
    args = ap.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    cfg = {x["family"]: x for x in STRATEGIES}
    raw = defaultdict(lambda: defaultdict(list))
    input_rows = 0
    rejected_gap = defaultdict(int)
    rejected_risk = defaultdict(int)

    with args.input.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            input_rows += 1
            fam = row.get("family")
            var = row.get("variant")
            if fam not in cfg:
                continue

            risk = fnum(row.get("risk"))
            if risk is None or risk < MIN_RISK:
                rejected_risk[(fam, var)] += 1
                continue

            if truthy(row.get("outcome_crossed_calendar_gap")):
                rejected_gap[(fam, var)] += 1
                continue

            t = datetime.fromisoformat(row["entry_time"])
            if t.year not in {2023, 2024, 2025}:
                raise RuntimeError(f"row outside Assembly I OOS: {t.isoformat()}")

            z = {
                "entry_time": t,
                "date": t.date().isoformat(),
                "year": t.year,
                "risk": risk,
                "event_strength": fnum(row.get("event_strength")),
                "event_aux": fnum(row.get("event_aux")),
            }
            for h in {PRIMARY_H, *SECONDARY}:
                for fld in ("endpoint_r", "mfe_r", "mae_r"):
                    z[f"h{h}_{fld}"] = fnum(row.get(f"h{h}_{fld}"))
            raw[fam][var].append(z)

    prepared = {}
    pmap = {}

    for i, item in enumerate(STRATEGIES):
        fam = item["family"]
        signal = non_overlap(raw[fam]["SIGNAL"], PRIMARY_H)
        control = non_overlap(raw[fam][item["control"]], PRIMARY_H)
        prepared[fam] = (signal, control)
        vf = lambda r: rvalue(r, PRIMARY_H, .10)
        d = daily_delta(signal, control, vf, SEED + i * 100)
        pmap[fam] = d["signflip_p_one_sided"]

    qmap = bh(pmap)

    report = {
        "schema": 1,
        "status": "COMPLETE",
        "id": ID,
        "scope": "EXTERNAL_OOS_2023_2025",
        "input_rows": input_rows,
        "cost_semantics": "legacy Atlas: 0.10/0.20 absolute XAUUSD price units divided by risk",
        "protected_2023_2025_opened": True,
        "protected_2026_opened": False,
        "strategies": [],
    }

    for i, item in enumerate(STRATEGIES):
        fam = item["family"]
        signal, control = prepared[fam]

        s10 = stats([rvalue(r, PRIMARY_H, .10) for r in signal])
        s20 = stats([rvalue(r, PRIMARY_H, .20) for r in signal])
        c10 = stats([rvalue(r, PRIMARY_H, .10) for r in control])

        vf = lambda r: rvalue(r, PRIMARY_H, .10)
        d = daily_delta(signal, control, vf, SEED + i * 100)
        d["bh_q"] = qmap[fam]

        boot = bootstrap_signal(signal, vf, SEED + i * 1000)
        matched = matched_delta(signal, control, vf)
        conc = concentration([rvalue(r, PRIMARY_H, .10) for r in signal])

        positive_years = 0
        yearly = []
        for year in (2023, 2024, 2025):
            sy = [r for r in signal if r["year"] == year]
            cy = [r for r in control if r["year"] == year]
            ys10 = stats([rvalue(r, PRIMARY_H, .10) for r in sy])
            if ys10["mean_r"] is not None and ys10["mean_r"] > 0:
                positive_years += 1
            yearly.append({
                "year": year,
                "signal_cost_010": ys10,
                "signal_cost_020": stats([rvalue(r, PRIMARY_H, .20) for r in sy]),
                "control_cost_010": stats([rvalue(r, PRIMARY_H, .10) for r in cy]),
                "control_delta_cost_010": daily_delta(
                    sy, cy, vf, SEED + i * 1000 + year
                ),
            })

        secondary = []
        for h in SECONDARY:
            ss = non_overlap(raw[fam]["SIGNAL"], h)
            cc = non_overlap(raw[fam][item["control"]], h)
            vf2 = lambda r, hh=h: rvalue(r, hh, .10)
            secondary.append({
                "horizon": h,
                "signal_cost_010": stats([rvalue(r, h, .10) for r in ss]),
                "signal_cost_020": stats([rvalue(r, h, .20) for r in ss]),
                "control_cost_010": stats([rvalue(r, h, .10) for r in cc]),
                "control_delta_cost_010": daily_delta(
                    ss, cc, vf2, SEED + i * 5000 + h
                ),
            })

        checks = {
            "oos_n": s10["n"] >= 60,
            "cost_010_pf": s10["pf"] is not None and s10["pf"] >= 1.10,
            "cost_010_mean": s10["mean_r"] is not None and s10["mean_r"] > 0.0,
            "cost_020_pf": s20["pf"] is not None and s20["pf"] >= 1.05,
            "cost_020_mean": s20["mean_r"] is not None and s20["mean_r"] > 0.0,
            "positive_years": positive_years >= 2,
            "signal_bootstrap": (
                boot["ci95"][0] is not None and boot["ci95"][0] > 0.0
            ),
            "without_top1": (
                conc["mean_r_without_top_1pct_winners"] is not None
                and conc["mean_r_without_top_1pct_winners"] > 0.0
            ),
            "control_delta": (
                d["mean_daily_delta"] is not None and d["mean_daily_delta"] > 0.0
            ),
            "control_bootstrap": (
                d["ci95"][0] is not None and d["ci95"][0] > 0.0
            ),
            "matched_delta": (
                matched["weighted_mean_delta"] is not None
                and matched["weighted_mean_delta"] > 0.0
            ),
            "bh_q": qmap[fam] is not None and qmap[fam] <= 0.10,
        }

        strategy_report = {
            "family": fam,
            "primary_horizon": PRIMARY_H,
            "signal_cost_010": {**s10, **conc, "bootstrap_daily": boot},
            "signal_cost_020": s20,
            "control_cost_010": c10,
            "signal_vs_control": d,
            "matched_control": matched,
            "positive_years": positive_years,
            "yearly": yearly,
            "secondary_horizons_descriptive_only": secondary,
            "oos_gate_checks": checks,
            "external_oos_pass": all(checks.values()),
            "rejected_gap_signal": rejected_gap[(fam, "SIGNAL")],
            "rejected_risk_signal": rejected_risk[(fam, "SIGNAL")],
        }
        report["strategies"].append(strategy_report)

    out = args.output_dir / "assembly1_analysis.json"
    out.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    print("=== GUARDIAN ASSEMBLY I V1 ===")
    print(f"INPUT ROWS: {input_rows}")
    print("2023-2025 ACCESSED: TRUE")
    print("2026 ACCESSED: FALSE")

    for it in report["strategies"]:
        s10 = it["signal_cost_010"]
        s20 = it["signal_cost_020"]
        d = it["signal_vs_control"]
        print(
            f"{it['family']} | H=60 | N={s10['n']} "
            f"| .10 MeanR={s10['mean_r']} PF={s10['pf']} "
            f"| .20 MeanR={s20['mean_r']} PF={s20['pf']} "
            f"| Years+={it['positive_years']}/3"
        )
        print(
            f"  Delta/day={d['mean_daily_delta']} | CI95={d['ci95']} "
            f"| p={d['signflip_p_one_sided']} | BHq={d['bh_q']}"
        )
        print(
            f"  WithoutTop1={s10['mean_r_without_top_1pct_winners']} "
            f"| BootstrapCI={s10['bootstrap_daily']['ci95']} "
            f"| OOS_PASS={it['external_oos_pass']}"
        )

    print(f"REPORT: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
