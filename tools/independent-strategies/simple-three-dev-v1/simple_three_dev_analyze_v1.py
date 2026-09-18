#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ID = "SIMPLE-THREE-DEV-V1"
SEED = 20260918
BOOTSTRAPS = 4000
PERMUTATIONS = 4000
MIN_RISK = 0.01

CFG = {
    "S1_VFWA": {
        "h": 30,
        "control": "CONTROL_LOW_FRONT",
        "delta_cost_r": 0.00,
        "pass": {
            "gross_mean_min": 0.15,
            "delta_daily_min": 0.08,
        },
    },
    "S2_FWSD": {
        "h": 15,
        "control": "CONTROL_OFF_WINDOW",
        "delta_cost_r": 0.10,
        "pass": {
            "net010_mean_gt": 0.00,
            "delta_daily_min": 0.06,
            "p_lt": 0.01,
        },
    },
    "S3_SCFB": {
        "h": 60,
        "control": "CONTROL_ALIGNED",
        "delta_cost_r": 0.10,
        "pass": {
            "gross_mean_min": 0.15,
            "net010_mean_gt": 0.00,
            "delta_daily_min": 0.05,
            "p_lt": 0.01,
        },
    },
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


def net_r(r, cost_r):
    x = r.get("exit_r")
    if x is None:
        return None
    return x - cost_r


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
            "best_r": None,
            "worst_r": None,
        }
    gw = sum(x for x in xs if x > 0)
    gl = -sum(x for x in xs if x < 0)
    cum = peak = maxdd = 0.0
    for x in xs:
        cum += x
        peak = max(peak, cum)
        maxdd = max(maxdd, peak - cum)
    return {
        "n": len(xs),
        "mean_r": sum(xs) / len(xs),
        "median_r": statistics.median(xs),
        "sum_r": sum(xs),
        "pf": gw / gl if gl > 0 else None,
        "win_pct": 100.0 * sum(x > 0 for x in xs) / len(xs),
        "max_drawdown_r": maxdd,
        "best_r": max(xs),
        "worst_r": min(xs),
    }


def daily_values(rows, vf):
    out = defaultdict(list)
    for r in rows:
        x = vf(r)
        if x is not None and math.isfinite(x):
            out[r["date"]].append(x)
    return out


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
        z = [x if rng.random() < 0.5 else -x for x in vals]
        if sum(z) / len(z) >= obs:
            extreme += 1

    return {
        "common_days": len(vals),
        "mean_daily_delta_r": obs,
        "ci95": [percentile(boots, .025), percentile(boots, .975)],
        "signflip_p_one_sided": (extreme + 1) / (PERMUTATIONS + 1),
    }


def daily_block_bootstrap(rows, vf, seed):
    d = daily_values(rows, vf)
    blocks = [(len(v), sum(v)) for v in d.values() if v]
    if not blocks:
        return {"days": 0, "ci95": [None, None], "median": None}
    rng = random.Random(seed)
    vals = []
    for _ in range(BOOTSTRAPS):
        z = rng.choices(blocks, k=len(blocks))
        n = sum(c for c, _ in z)
        s = sum(v for _, v in z)
        vals.append(s / n if n else 0.0)
    return {
        "days": len(blocks),
        "ci95": [percentile(vals, .025), percentile(vals, .975)],
        "median": percentile(vals, .5),
    }


def fwsd_timing(rows):
    touches = [r for r in rows if r.get("exit_reason") == "EMA_TOUCH"]
    touch_minutes = [r["exit_minute"] for r in touches if r.get("exit_minute") is not None]
    all_minutes = [r["exit_minute"] for r in rows if r.get("exit_minute") is not None]
    return {
        "touch_rate_pct": 100.0 * len(touches) / len(rows) if rows else None,
        "mean_minutes_to_ema_touch_conditional": (
            sum(touch_minutes) / len(touch_minutes) if touch_minutes else None
        ),
        "median_minutes_to_ema_touch_conditional": (
            statistics.median(touch_minutes) if touch_minutes else None
        ),
        "mean_exit_minute_including_time_stop": (
            sum(all_minutes) / len(all_minutes) if all_minutes else None
        ),
    }


def scfb_excursion(rows):
    mfes = [r["mfe_r"] for r in rows if r.get("mfe_r") is not None]
    maes = [r["mae_r"] for r in rows if r.get("mae_r") is not None]
    mean_mfe = sum(mfes) / len(mfes) if mfes else None
    mean_mae = sum(maes) / len(maes) if maes else None
    return {
        "mean_mfe_r": mean_mfe,
        "mean_mae_r": mean_mae,
        "mfe_mae_ratio_of_means": (
            mean_mfe / mean_mae
            if mean_mfe is not None and mean_mae not in (None, 0)
            else None
        ),
    }


def exit_reasons(rows):
    return dict(Counter(r.get("exit_reason") or "UNKNOWN" for r in rows))


def evaluate_gate(fam, gross, net010, delta):
    p = delta["signflip_p_one_sided"]
    d = delta["mean_daily_delta_r"]

    if fam == "S1_VFWA":
        checks = {
            "gross_mean_ge_0p15": gross["mean_r"] is not None and gross["mean_r"] >= 0.15,
            "daily_delta_ge_0p08": d is not None and d >= 0.08,
        }

    elif fam == "S2_FWSD":
        checks = {
            "net010_mean_gt_0": net010["mean_r"] is not None and net010["mean_r"] > 0.0,
            "daily_delta_ge_0p06": d is not None and d >= 0.06,
            "p_lt_0p01": p is not None and p < 0.01,
        }

    elif fam == "S3_SCFB":
        checks = {
            "gross_mean_ge_0p15": gross["mean_r"] is not None and gross["mean_r"] >= 0.15,
            "net010_mean_gt_0": net010["mean_r"] is not None and net010["mean_r"] > 0.0,
            "daily_delta_ge_0p05": d is not None and d >= 0.05,
            "p_lt_0p01": p is not None and p < 0.01,
        }

    else:
        raise RuntimeError(f"unknown family: {fam}")

    return checks, all(checks.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--output-dir", required=True, type=Path)
    args = ap.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    raw = defaultdict(lambda: defaultdict(list))
    input_rows = 0
    rejected_gap = defaultdict(int)
    rejected_risk = defaultdict(int)

    with args.input.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            input_rows += 1
            fam = row.get("family")
            variant = row.get("variant")
            if fam not in CFG:
                continue

            risk = fnum(row.get("risk"))
            if risk is None or risk < MIN_RISK:
                rejected_risk[(fam, variant)] += 1
                continue

            if truthy(row.get("outcome_crossed_calendar_gap")):
                rejected_gap[(fam, variant)] += 1
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
                "exit_r": fnum(row.get("strategy_exit_r")),
                "exit_price": fnum(row.get("strategy_exit_price")),
                "exit_minute": fnum(row.get("strategy_exit_minute")),
                "exit_reason": row.get("strategy_exit_reason") or None,
                "mfe_r": fnum(row.get("strategy_mfe_r")),
                "mae_r": fnum(row.get("strategy_mae_r")),
                "ema_touch_minute": fnum(row.get("ema_touch_minute")),
                "event_strength": fnum(row.get("event_strength")),
                "event_aux": fnum(row.get("event_aux")),
            }
            raw[fam][variant].append(z)

    report = {
        "schema": 1,
        "status": "COMPLETE",
        "id": ID,
        "scope": "DEV_2017_2020_ONLY",
        "input_rows": input_rows,
        "cost_unit": "R",
        "protected_2021_2022_opened": False,
        "protected_2023_plus_opened": False,
        "protected_2026_opened": False,
        "strategies": [],
    }

    for idx, fam in enumerate(("S1_VFWA", "S2_FWSD", "S3_SCFB")):
        cfg = CFG[fam]
        signal = non_overlap(raw[fam]["SIGNAL"], cfg["h"])
        control = non_overlap(raw[fam][cfg["control"]], cfg["h"])

        gross = stats([net_r(r, 0.00) for r in signal])
        net005 = stats([net_r(r, 0.05) for r in signal])
        net010 = stats([net_r(r, 0.10) for r in signal])
        net015 = stats([net_r(r, 0.15) for r in signal])
        net020 = stats([net_r(r, 0.20) for r in signal])
        control010 = stats([net_r(r, 0.10) for r in control])

        delta_cost = cfg["delta_cost_r"]
        vf = lambda r, c=delta_cost: net_r(r, c)
        d = daily_delta(signal, control, vf, SEED + idx * 1000)
        boot = daily_block_bootstrap(signal, vf, SEED + idx * 1000 + 1)

        gate_checks, dev_pass = evaluate_gate(fam, gross, net010, d)

        yearly = []
        for y in (2017, 2018, 2019, 2020):
            sy = [r for r in signal if r["year"] == y]
            cy = [r for r in control if r["year"] == y]
            yearly.append({
                "year": y,
                "signal_gross": stats([net_r(r, 0.00) for r in sy]),
                "signal_cost_010": stats([net_r(r, 0.10) for r in sy]),
                "control_cost_010": stats([net_r(r, 0.10) for r in cy]),
                "daily_delta_at_preregistered_cost": daily_delta(
                    sy, cy, vf, SEED + idx * 1000 + y
                ),
            })

        item = {
            "family": fam,
            "primary_horizon_min": cfg["h"],
            "signal_n_after_nonoverlap": len(signal),
            "control_n_after_nonoverlap": len(control),
            "signal": {
                "cost_0p00": gross,
                "cost_0p05": net005,
                "cost_0p10": net010,
                "cost_0p15": net015,
                "cost_0p20": net020,
                "bootstrap_at_delta_cost": boot,
                "exit_reasons": exit_reasons(signal),
            },
            "control": {
                "cost_0p10": control010,
                "exit_reasons": exit_reasons(control),
            },
            "daily_delta": {
                "cost_r": delta_cost,
                **d,
            },
            "yearly": yearly,
            "sieve": {
                "checks": gate_checks,
                "dev_pass": dev_pass,
                "holdout_eligible_after_cold_audit": dev_pass,
                "holdout_opened_automatically": False,
            },
            "rejected_gap_signal": rejected_gap[(fam, "SIGNAL")],
            "rejected_risk_signal": rejected_risk[(fam, "SIGNAL")],
        }

        if fam == "S2_FWSD":
            item["timing"] = {
                "signal": fwsd_timing(signal),
                "control": fwsd_timing(control),
            }

        if fam == "S3_SCFB":
            item["excursion"] = {
                "signal": scfb_excursion(signal),
                "control": scfb_excursion(control),
            }

        report["strategies"].append(item)

    out = args.output_dir / "simple_three_dev_analysis.json"
    out.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    print("=== SIMPLE THREE DEV V1 ===")
    print(f"INPUT ROWS: {input_rows}")
    print("2021-2022 ACCESSED: FALSE")
    print("2023+ ACCESSED: FALSE")
    print("2026 ACCESSED: FALSE")

    for item in report["strategies"]:
        fam = item["family"]
        s0 = item["signal"]["cost_0p00"]
        s10 = item["signal"]["cost_0p10"]
        c10 = item["control"]["cost_0p10"]
        d = item["daily_delta"]
        print(
            f"{fam} | N={s0['n']} | Gross MeanR={s0['mean_r']} PF={s0['pf']} "
            f"| .10 MeanR={s10['mean_r']} PF={s10['pf']} "
            f"| Control .10 MeanR={c10['mean_r']}"
        )
        print(
            f"  Delta@{d['cost_r']}R/day={d['mean_daily_delta_r']} "
            f"| CI95={d['ci95']} | p={d['signflip_p_one_sided']} "
            f"| DEV_PASS={item['sieve']['dev_pass']}"
        )
        if fam == "S2_FWSD":
            tm = item["timing"]["signal"]
            print(
                f"  EMA touch rate={tm['touch_rate_pct']}% "
                f"| mean touch min={tm['mean_minutes_to_ema_touch_conditional']}"
            )
        if fam == "S3_SCFB":
            ex = item["excursion"]["signal"]
            print(
                f"  MFE={ex['mean_mfe_r']} | MAE={ex['mean_mae_r']} "
                f"| MFE/MAE={ex['mfe_mae_ratio_of_means']}"
            )

    print(f"REPORT: {out}")


if __name__ == "__main__":
    raise SystemExit(main())
