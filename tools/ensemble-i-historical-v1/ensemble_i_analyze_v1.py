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

ID = "GUARDIAN-ENSEMBLE-I-HISTORICAL-HOLDOUT-V1"
SEED = 20260918
BOOTSTRAPS = 4000
PERMUTATIONS = 4000
MIN_RISK = 0.01
H = 60

FAMILIES = [
    "E1_CONSENSUS_2PLUS_4",
    "E2_DIVERSE_SCORE_5",
    "E3_AS2_CONFIRMED",
]

YEARS = tuple(range(2005, 2017))
EARLY_YEARS = set(range(2005, 2011))
LATE_YEARS = set(range(2011, 2017))

GATE = {
    "n_min": 300,
    "cost_010_pf_min": 1.10,
    "cost_010_mean_gt": 0.0,
    "cost_020_pf_min": 1.05,
    "cost_020_mean_gt": 0.0,
    "positive_years_min": 9,
    "early_2005_2010_mean_gt": 0.0,
    "late_2011_2016_mean_gt": 0.0,
    "signal_bootstrap_ci95_lower_gt": 0.0,
    "without_top1_mean_gt": 0.0,
    "all_leave_one_year_out_mean_gt": 0.0,
    "signal_bh_q_max": 0.10,
    "delta_mean_gt": 0.0,
    "delta_ci95_lower_gt": 0.0,
    "delta_bh_q_max": 0.10,
    "matched_delta_gt": 0.0,
}


def fnum(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except Exception:
        return None


def truthy(x):
    return str(x).strip().lower() in {"true", "1", "yes"}


def percentile(xs, q):
    if not xs:
        return None
    a = sorted(xs)
    if len(a) == 1:
        return a[0]
    pos = (len(a) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return a[lo]
    w = pos - lo
    return a[lo] * (1.0 - w) + a[hi] * w


def stats(xs):
    xs = [x for x in xs if x is not None and math.isfinite(x)]
    if not xs:
        return {
            "n": 0,
            "mean_r": None,
            "median_r": None,
            "pf": None,
            "sum_r": None,
            "win_pct": None,
            "max_drawdown_r": None,
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
        "pf": gw / gl if gl > 0 else None,
        "sum_r": sum(xs),
        "win_pct": 100.0 * sum(x > 0 for x in xs) / len(xs),
        "max_drawdown_r": maxdd,
    }


def non_overlap(rows, horizon):
    out = []
    next_allowed = None
    for r in sorted(rows, key=lambda z: z["entry_time"]):
        if next_allowed is None or r["entry_time"] >= next_allowed:
            out.append(r)
            next_allowed = r["entry_time"] + timedelta(minutes=horizon)
    return out


def rval(row, cost_price):
    raw = row.get("h60_endpoint_r")
    risk = row.get("risk")
    if raw is None or risk in (None, 0):
        return None
    return raw - cost_price / risk


def by_day(rows, value_fn):
    out = defaultdict(list)
    for r in rows:
        x = value_fn(r)
        if x is not None and math.isfinite(x):
            out[r["date"]].append(x)
    return out


def signal_signflip(rows, value_fn, seed):
    d = by_day(rows, value_fn)
    vals = [sum(v) / len(v) for v in d.values() if v]
    if not vals:
        return {
            "days": 0,
            "mean_daily_r": None,
            "p_one_sided": None,
        }

    obs = sum(vals) / len(vals)
    rng = random.Random(seed)
    extreme = 0

    for _ in range(PERMUTATIONS):
        z = [x if rng.random() < .5 else -x for x in vals]
        if sum(z) / len(z) >= obs:
            extreme += 1

    return {
        "days": len(vals),
        "mean_daily_r": obs,
        "p_one_sided": (extreme + 1) / (PERMUTATIONS + 1),
    }


def bootstrap_signal(rows, value_fn, seed):
    d = by_day(rows, value_fn)
    blocks = [(len(v), sum(v)) for v in d.values() if v]
    if not blocks:
        return {"days": 0, "ci95": [None, None], "median": None}

    rng = random.Random(seed)
    vals = []

    for _ in range(BOOTSTRAPS):
        z = rng.choices(blocks, k=len(blocks))
        n = sum(a for a, _ in z)
        s = sum(b for _, b in z)
        vals.append(s / n if n else 0.0)

    return {
        "days": len(blocks),
        "ci95": [percentile(vals, .025), percentile(vals, .975)],
        "median": percentile(vals, .5),
    }


def daily_delta(sig, ctl, value_fn, seed):
    a = by_day(sig, value_fn)
    b = by_day(ctl, value_fn)
    common = sorted(set(a) & set(b))

    ds = [
        sum(a[d]) / len(a[d]) - sum(b[d]) / len(b[d])
        for d in common
    ]

    if not ds:
        return {
            "common_days": 0,
            "mean_daily_delta": None,
            "ci95": [None, None],
            "p_one_sided": None,
        }

    obs = sum(ds) / len(ds)
    rng = random.Random(seed)

    boots = []
    for _ in range(BOOTSTRAPS):
        z = rng.choices(ds, k=len(ds))
        boots.append(sum(z) / len(z))

    extreme = 0
    for _ in range(PERMUTATIONS):
        z = [x if rng.random() < .5 else -x for x in ds]
        if sum(z) / len(z) >= obs:
            extreme += 1

    return {
        "common_days": len(ds),
        "mean_daily_delta": obs,
        "ci95": [
            percentile(boots, .025),
            percentile(boots, .975),
        ],
        "p_one_sided": (extreme + 1) / (PERMUTATIONS + 1),
    }


def matched_delta(sig, ctl, value_fn):
    sm = {r["entry_time"]: value_fn(r) for r in sig}
    cm = {r["entry_time"]: value_fn(r) for r in ctl}

    keys = [
        k
        for k in sm.keys() & cm.keys()
        if sm[k] is not None and cm[k] is not None
    ]

    return {
        "matched_support": len(keys),
        "weighted_mean_delta": (
            sum(sm[k] - cm[k] for k in keys) / len(keys)
            if keys else None
        ),
    }


def concentration(xs):
    xs = [x for x in xs if x is not None and math.isfinite(x)]
    if not xs:
        return {"mean_r_without_top_1pct_winners": None}

    n_remove = max(1, math.ceil(.01 * len(xs)))
    idx = sorted(
        range(len(xs)),
        key=lambda i: xs[i],
        reverse=True,
    )[:n_remove]
    excluded = set(idx)
    rem = [x for i, x in enumerate(xs) if i not in excluded]

    return {
        "mean_r_without_top_1pct_winners": (
            sum(rem) / len(rem) if rem else None
        ),
    }


def bh(pmap):
    vals = sorted((p, key) for key, p in pmap.items() if p is not None)
    m = len(vals)
    out = {key: None for key in pmap}

    if not m:
        return out

    tmp = []
    for i, (p, key) in enumerate(vals, 1):
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

    raw = defaultdict(lambda: defaultdict(list))
    rejected_gap = defaultdict(int)
    rejected_risk = defaultdict(int)
    input_rows = 0

    with args.input.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            input_rows += 1

            fam = row.get("family")
            var = row.get("variant")
            if fam not in FAMILIES:
                continue

            risk = fnum(row.get("risk"))
            if risk is None or risk < MIN_RISK:
                rejected_risk[(fam, var)] += 1
                continue

            if truthy(row.get("outcome_crossed_calendar_gap")):
                rejected_gap[(fam, var)] += 1
                continue

            t = datetime.fromisoformat(row["entry_time"])

            if not (
                datetime(2005, 3, 1, tzinfo=t.tzinfo)
                <= t
                < datetime(2017, 1, 1, tzinfo=t.tzinfo)
            ):
                raise RuntimeError(
                    f"row outside historical holdout: {t.isoformat()}"
                )

            z = {
                "entry_time": t,
                "date": t.date().isoformat(),
                "year": t.year,
                "risk": risk,
                "h60_endpoint_r": fnum(row.get("h60_endpoint_r")),
            }
            raw[fam][var].append(z)

    prepared = {}
    sig_p = {}
    delta_p = {}
    prelim = {}

    for i, fam in enumerate(FAMILIES):
        sig = non_overlap(raw[fam]["SIGNAL"], H)
        ctl = non_overlap(raw[fam]["CONTROL_OPPOSITE"], H)

        vf = lambda r: rval(r, .10)

        st = signal_signflip(sig, vf, SEED + i * 100)
        dt = daily_delta(sig, ctl, vf, SEED + i * 100 + 1)

        prepared[fam] = (sig, ctl)
        prelim[fam] = (st, dt)
        sig_p[fam] = st["p_one_sided"]
        delta_p[fam] = dt["p_one_sided"]

    sig_q = bh(sig_p)
    delta_q = bh(delta_p)

    results = []

    for i, fam in enumerate(FAMILIES):
        sig, ctl = prepared[fam]

        s10_vals = [rval(r, .10) for r in sig]
        s20_vals = [rval(r, .20) for r in sig]
        c10_vals = [rval(r, .10) for r in ctl]

        s10 = stats(s10_vals)
        s20 = stats(s20_vals)
        c10 = stats(c10_vals)

        vf10 = lambda r: rval(r, .10)

        boot = bootstrap_signal(sig, vf10, SEED + i * 1000)
        conc = concentration(s10_vals)
        dt = prelim[fam][1]
        matched = matched_delta(sig, ctl, vf10)

        early = stats([
            rval(r, .10)
            for r in sig
            if r["year"] in EARLY_YEARS
        ])
        late = stats([
            rval(r, .10)
            for r in sig
            if r["year"] in LATE_YEARS
        ])

        yearly = []
        positive_years = 0
        yearly_means = []

        for year in YEARS:
            sy = [r for r in sig if r["year"] == year]
            cy = [r for r in ctl if r["year"] == year]

            ys = stats([rval(r, .10) for r in sy])
            yc = stats([rval(r, .10) for r in cy])
            yd = daily_delta(
                sy,
                cy,
                vf10,
                SEED + i * 10000 + year,
            )

            if ys["mean_r"] is not None:
                yearly_means.append(ys["mean_r"])
                if ys["mean_r"] > 0:
                    positive_years += 1

            yearly.append({
                "year": year,
                "signal_cost_010": ys,
                "control_cost_010": yc,
                "delta_cost_010": yd,
            })

        loo = []
        loo_all_positive = True

        for excluded in YEARS:
            sx = [r for r in sig if r["year"] != excluded]
            lx = stats([rval(r, .10) for r in sx])
            ok = lx["mean_r"] is not None and lx["mean_r"] > 0
            loo_all_positive = loo_all_positive and ok

            loo.append({
                "excluded_year": excluded,
                "signal_cost_010": lx,
                "positive": ok,
            })

        checks = {
            "n": s10["n"] >= GATE["n_min"],
            "cost_010_pf": (
                s10["pf"] is not None
                and s10["pf"] >= GATE["cost_010_pf_min"]
            ),
            "cost_010_mean": (
                s10["mean_r"] is not None
                and s10["mean_r"] > GATE["cost_010_mean_gt"]
            ),
            "cost_020_pf": (
                s20["pf"] is not None
                and s20["pf"] >= GATE["cost_020_pf_min"]
            ),
            "cost_020_mean": (
                s20["mean_r"] is not None
                and s20["mean_r"] > GATE["cost_020_mean_gt"]
            ),
            "positive_years": (
                positive_years >= GATE["positive_years_min"]
            ),
            "early_mean": (
                early["mean_r"] is not None
                and early["mean_r"]
                > GATE["early_2005_2010_mean_gt"]
            ),
            "late_mean": (
                late["mean_r"] is not None
                and late["mean_r"]
                > GATE["late_2011_2016_mean_gt"]
            ),
            "signal_bootstrap": (
                boot["ci95"][0] is not None
                and boot["ci95"][0]
                > GATE["signal_bootstrap_ci95_lower_gt"]
            ),
            "without_top1": (
                conc["mean_r_without_top_1pct_winners"] is not None
                and conc["mean_r_without_top_1pct_winners"]
                > GATE["without_top1_mean_gt"]
            ),
            "loo_signal": loo_all_positive,
            "signal_bh_q": (
                sig_q[fam] is not None
                and sig_q[fam] <= GATE["signal_bh_q_max"]
            ),
            "delta_mean": (
                dt["mean_daily_delta"] is not None
                and dt["mean_daily_delta"] > GATE["delta_mean_gt"]
            ),
            "delta_ci95": (
                dt["ci95"][0] is not None
                and dt["ci95"][0] > GATE["delta_ci95_lower_gt"]
            ),
            "delta_bh_q": (
                delta_q[fam] is not None
                and delta_q[fam] <= GATE["delta_bh_q_max"]
            ),
            "matched_delta": (
                matched["weighted_mean_delta"] is not None
                and matched["weighted_mean_delta"]
                > GATE["matched_delta_gt"]
            ),
        }

        results.append({
            "family": fam,
            "signal_cost_010": {
                **s10,
                **conc,
                "bootstrap_daily": boot,
            },
            "signal_cost_020": s20,
            "control_cost_010": c10,
            "signal_test": {
                **prelim[fam][0],
                "bh_q": sig_q[fam],
            },
            "signal_vs_control": {
                **dt,
                "bh_q": delta_q[fam],
            },
            "matched_control": matched,
            "early_2005_2010": early,
            "late_2011_2016": late,
            "positive_years": positive_years,
            "minimum_year_mean_010": (
                min(yearly_means) if yearly_means else None
            ),
            "yearly": yearly,
            "leave_one_year_out": loo,
            "rejected_gap_signal": rejected_gap[(fam, "SIGNAL")],
            "rejected_risk_signal": rejected_risk[(fam, "SIGNAL")],
            "gate_checks": checks,
            "historical_holdout_pass": all(checks.values()),
        })

    passed = [r for r in results if r["historical_holdout_pass"]]
    selected = None

    if passed:
        passed.sort(
            key=lambda r: (
                r["minimum_year_mean_010"],
                r["signal_cost_020"]["mean_r"],
                -r["signal_cost_010"]["max_drawdown_r"],
                r["family"],
            ),
            reverse=True,
        )
        selected = passed[0]["family"]

    report = {
        "schema": 1,
        "status": "COMPLETE",
        "id": ID,
        "scope": "UNTOUCHED_HISTORICAL_HOLDOUT_2005_03_TO_2016_12",
        "input_rows": input_rows,
        "family_count": len(FAMILIES),
        "families": FAMILIES,
        "historical_2005_2016_opened": True,
        "development_2017_2025_reread": False,
        "protected_2026_opened": False,
        "gate": GATE,
        "multiple_testing": {
            "signal": "BH across 3 absolute signal tests",
            "delta": "BH across 3 signal-minus-control tests",
        },
        "selection_rule": (
            "among historical-holdout passes maximize minimum yearly .10 "
            "MeanR; then .20 MeanR; then lower .10 max drawdown; then id"
        ),
        "results": results,
        "historical_holdout_pass_count": len(passed),
        "selected_for_final_2026": selected,
    }

    out = args.output_dir / "ensemble_i_historical_analysis.json"
    out.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    print("=== GUARDIAN ENSEMBLE I HISTORICAL HOLDOUT V1 ===")
    print(f"INPUT ROWS: {input_rows}")
    print("HISTORICAL 2005-2016 ACCESSED: TRUE")
    print("2017-2025 REREAD: FALSE")
    print("2026 ACCESSED: FALSE")

    ordered = sorted(
        results,
        key=lambda r: (
            r["historical_holdout_pass"],
            r["signal_cost_010"]["mean_r"]
            if r["signal_cost_010"]["mean_r"] is not None
            else -999,
        ),
        reverse=True,
    )

    for r in ordered:
        s10 = r["signal_cost_010"]
        s20 = r["signal_cost_020"]
        d = r["signal_vs_control"]
        st = r["signal_test"]

        print(
            f"{r['family']} | N={s10['n']} "
            f"| .10 MeanR={s10['mean_r']} PF={s10['pf']} "
            f"| .20 MeanR={s20['mean_r']} PF={s20['pf']} "
            f"| Years+={r['positive_years']}/12 "
            f"| Early={r['early_2005_2010']['mean_r']} "
            f"| Late={r['late_2011_2016']['mean_r']} "
            f"| PASS={r['historical_holdout_pass']}"
        )
        print(
            f"  signal q={st['bh_q']} "
            f"| delta/day={d['mean_daily_delta']} "
            f"CI95={d['ci95']} delta q={d['bh_q']} "
            f"| withoutTop1={s10['mean_r_without_top_1pct_winners']}"
        )

    print(f"HISTORICAL HOLDOUT PASS COUNT: {len(passed)}")
    print(f"SELECTED FOR FINAL 2026: {selected}")
    print(f"REPORT: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
