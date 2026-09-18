#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path

ID = "AS2-EXECUTION-LAB-III-V1"
SEED = 20260918
BOOTSTRAPS = 4000
PERMUTATIONS = 4000

YEARS = tuple(range(2017, 2026))
EARLY_YEARS = set(range(2017, 2023))
LATE_YEARS = {2023, 2024, 2025}

CANDIDATE_IDS = [
    "BASELINE_TIME_H60",
    "SL_ONLY_1p5R_H60",
    "SL_ONLY_2R_H60",
    "SL_ONLY_2p5R_H60",
    "SL_ONLY_3R_H60",
    "BE_AFTER_1p5R_SL2p5R_H60",
    "BE_AFTER_2R_SL2p5R_H60",
]

GATE = {
    "n_min": 1500,
    "cost_010_pf_min": 1.10,
    "cost_010_mean_gt": 0.0,
    "cost_020_pf_min": 1.05,
    "cost_020_mean_gt": 0.0,
    "positive_years_min": 6,
    "early_mean_gt": 0.0,
    "late_mean_gt": 0.0,
    "signal_bootstrap_ci95_lower_gt": 0.0,
    "without_top1_mean_gt": 0.0,
    "all_leave_one_year_out_mean_gt": 0.0,
    "signal_bh_q_max": 0.10,
    "delta_mean_gt": 0.0,
    "delta_ci95_lower_gt": 0.0,
    "delta_bh_q_max": 0.10,
    "matched_delta_gt": 0.0,
}

BASELINE_EXPECTED = {
    "n": 6936,
    "mean_010": 0.08269375012888354,
    "pf_010": 1.1218405392699233,
    "mean_020": -0.035528535280275136,
}


def fnum(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except Exception:
        return None


def truthy(x):
    return str(x).strip().lower() in {"true", "1", "yes"}


def pct(xs, q):
    if not xs:
        return None
    a = sorted(xs)
    if len(a) == 1:
        return a[0]
    p = (len(a) - 1) * q
    lo = int(math.floor(p))
    hi = int(math.ceil(p))
    if lo == hi:
        return a[lo]
    w = p - lo
    return a[lo] * (1 - w) + a[hi] * w


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


def by_day(rows, field):
    d = defaultdict(list)
    for r in rows:
        x = r.get(field)
        if x is not None and math.isfinite(x):
            d[r["date"]].append(x)
    return d


def signal_signflip(rows, field, seed):
    d = by_day(rows, field)
    vals = [sum(v) / len(v) for v in d.values() if v]

    if not vals:
        return {"days": 0, "mean_daily_r": None, "p_one_sided": None}

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


def bootstrap_signal(rows, field, seed):
    d = by_day(rows, field)
    blocks = [(len(v), sum(v)) for v in d.values() if v]

    if not blocks:
        return {"days": 0, "ci95": [None, None], "median": None}

    rng = random.Random(seed)
    values = []

    for _ in range(BOOTSTRAPS):
        z = rng.choices(blocks, k=len(blocks))
        n = sum(a for a, _ in z)
        s = sum(b for _, b in z)
        values.append(s / n if n else 0.0)

    return {
        "days": len(blocks),
        "ci95": [pct(values, .025), pct(values, .975)],
        "median": pct(values, .5),
    }


def daily_delta(signal, control, field, seed):
    a = by_day(signal, field)
    b = by_day(control, field)
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
        "ci95": [pct(boots, .025), pct(boots, .975)],
        "p_one_sided": (extreme + 1) / (PERMUTATIONS + 1),
    }


def matched_delta(signal, control, field):
    sm = {r["entry_time"]: r[field] for r in signal}
    cm = {r["entry_time"]: r[field] for r in control}

    keys = [
        k for k in sm.keys() & cm.keys()
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
    ids = sorted(
        range(len(xs)),
        key=lambda i: xs[i],
        reverse=True,
    )[:n_remove]
    excluded = set(ids)
    rem = [x for i, x in enumerate(xs) if i not in excluded]

    return {
        "mean_r_without_top_1pct_winners": (
            sum(rem) / len(rem) if rem else None
        ),
    }


def bh(pmap):
    vals = sorted((p, k) for k, p in pmap.items() if p is not None)
    m = len(vals)
    out = {k: None for k in pmap}

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
    input_rows = 0
    rejected_gap = defaultdict(int)

    with args.input.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            input_rows += 1
            cand = row.get("candidate")
            var = row.get("variant")

            if cand not in CANDIDATE_IDS:
                continue

            if truthy(row.get("crossed_calendar_gap")):
                rejected_gap[(cand, var)] += 1
                continue

            year = int(row["year"])
            if year not in YEARS:
                raise RuntimeError(f"unexpected year: {year}")

            t = row["entry_time"]
            raw[cand][var].append({
                "entry_time": t,
                "date": t[:10],
                "year": year,
                "gross_r": fnum(row.get("gross_r")),
                "r_cost_010": fnum(row.get("r_cost_010")),
                "r_cost_020": fnum(row.get("r_cost_020")),
                "exit_minute": int(row["exit_minute"]),
                "exit_reason": row.get("exit_reason"),
                "be_activated": truthy(row.get("be_activated")),
            })

    for cand in CANDIDATE_IDS:
        ns = len(raw[cand]["SIGNAL"])
        nc = len(raw[cand]["CONTROL_OPPOSITE"])
        if ns != nc:
            raise RuntimeError(
                f"{cand} signal/control count mismatch: {ns} vs {nc}"
            )

    # Hard reproduction check against Lab II TIME_H60.
    base = raw["BASELINE_TIME_H60"]["SIGNAL"]
    base_s10 = stats([r["r_cost_010"] for r in base])
    base_s20 = stats([r["r_cost_020"] for r in base])

    if base_s10["n"] != BASELINE_EXPECTED["n"]:
        raise RuntimeError(
            f"baseline N mismatch: {base_s10['n']} vs {BASELINE_EXPECTED['n']}"
        )

    for label, actual, expected in (
        ("mean_010", base_s10["mean_r"], BASELINE_EXPECTED["mean_010"]),
        ("pf_010", base_s10["pf"], BASELINE_EXPECTED["pf_010"]),
        ("mean_020", base_s20["mean_r"], BASELINE_EXPECTED["mean_020"]),
    ):
        if actual is None or abs(actual - expected) > 1e-12:
            raise RuntimeError(
                f"baseline reproduction mismatch {label}: {actual} vs {expected}"
            )

    prelim = {}
    signal_p = {}
    delta_p = {}

    for i, cand in enumerate(CANDIDATE_IDS):
        sig = raw[cand]["SIGNAL"]
        ctl = raw[cand]["CONTROL_OPPOSITE"]

        st = signal_signflip(sig, "r_cost_010", SEED + i * 100)
        dt = daily_delta(
            sig,
            ctl,
            "r_cost_010",
            SEED + i * 100 + 1,
        )

        prelim[cand] = (st, dt)
        signal_p[cand] = st["p_one_sided"]
        delta_p[cand] = dt["p_one_sided"]

    signal_q = bh(signal_p)
    delta_q = bh(delta_p)

    results = []

    for i, cand in enumerate(CANDIDATE_IDS):
        sig = raw[cand]["SIGNAL"]
        ctl = raw[cand]["CONTROL_OPPOSITE"]

        s10 = stats([r["r_cost_010"] for r in sig])
        s20 = stats([r["r_cost_020"] for r in sig])
        c10 = stats([r["r_cost_010"] for r in ctl])

        boot = bootstrap_signal(
            sig,
            "r_cost_010",
            SEED + i * 1000,
        )
        conc = concentration([r["r_cost_010"] for r in sig])
        d = prelim[cand][1]
        matched = matched_delta(sig, ctl, "r_cost_010")

        early = stats([
            r["r_cost_010"]
            for r in sig
            if r["year"] in EARLY_YEARS
        ])
        late = stats([
            r["r_cost_010"]
            for r in sig
            if r["year"] in LATE_YEARS
        ])

        yearly = []
        positive_years = 0
        yearly_means = []

        for year in YEARS:
            sy = [r for r in sig if r["year"] == year]
            cy = [r for r in ctl if r["year"] == year]

            ys = stats([r["r_cost_010"] for r in sy])
            yc = stats([r["r_cost_010"] for r in cy])
            yd = daily_delta(
                sy,
                cy,
                "r_cost_010",
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
            lx = stats([r["r_cost_010"] for r in sx])
            ok = lx["mean_r"] is not None and lx["mean_r"] > 0
            loo_all_positive = loo_all_positive and ok
            loo.append({
                "excluded_year": excluded,
                "signal_cost_010": lx,
                "positive": ok,
            })

        reasons = Counter(r["exit_reason"] for r in sig)
        exit_minutes = [r["exit_minute"] for r in sig]

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
                and early["mean_r"] > GATE["early_mean_gt"]
            ),
            "late_mean": (
                late["mean_r"] is not None
                and late["mean_r"] > GATE["late_mean_gt"]
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
                signal_q[cand] is not None
                and signal_q[cand] <= GATE["signal_bh_q_max"]
            ),
            "delta_mean": (
                d["mean_daily_delta"] is not None
                and d["mean_daily_delta"] > GATE["delta_mean_gt"]
            ),
            "delta_ci95": (
                d["ci95"][0] is not None
                and d["ci95"][0] > GATE["delta_ci95_lower_gt"]
            ),
            "delta_bh_q": (
                delta_q[cand] is not None
                and delta_q[cand] <= GATE["delta_bh_q_max"]
            ),
            "matched_delta": (
                matched["weighted_mean_delta"] is not None
                and matched["weighted_mean_delta"]
                > GATE["matched_delta_gt"]
            ),
        }

        results.append({
            "candidate": cand,
            "signal_cost_010": {
                **s10,
                **conc,
                "bootstrap_daily": boot,
            },
            "signal_cost_020": s20,
            "control_cost_010": c10,
            "signal_test": {
                **prelim[cand][0],
                "bh_q": signal_q[cand],
            },
            "signal_vs_control": {
                **d,
                "bh_q": delta_q[cand],
            },
            "matched_control": matched,
            "early_2017_2022": early,
            "late_2023_2025": late,
            "positive_years": positive_years,
            "minimum_year_mean_010": (
                min(yearly_means) if yearly_means else None
            ),
            "yearly": yearly,
            "leave_one_year_out": loo,
            "exit_reason_counts": dict(reasons),
            "exit_minute_median": (
                statistics.median(exit_minutes)
                if exit_minutes else None
            ),
            "rejected_gap_signal": rejected_gap[(cand, "SIGNAL")],
            "gate_checks": checks,
            "strict_pass": all(checks.values()),
        })

    strict = [r for r in results if r["strict_pass"]]
    selected = None

    if strict:
        strict.sort(
            key=lambda r: (
                r["minimum_year_mean_010"],
                r["signal_cost_020"]["mean_r"],
                -r["signal_cost_010"]["max_drawdown_r"],
                r["candidate"],
            ),
            reverse=True,
        )
        selected = strict[0]["candidate"]

    report = {
        "schema": 1,
        "status": "COMPLETE",
        "id": ID,
        "input_rows": input_rows,
        "baseline_reproduction_check": {
            "status": "PASS",
            "expected": BASELINE_EXPECTED,
            "actual": {
                "n": base_s10["n"],
                "mean_010": base_s10["mean_r"],
                "pf_010": base_s10["pf"],
                "mean_020": base_s20["mean_r"],
            },
        },
        "candidate_count": len(CANDIDATE_IDS),
        "candidate_ids": CANDIDATE_IDS,
        "development_period": "2017-2025 contaminated/development",
        "protected_2026_opened": False,
        "multiple_testing": {
            "signal_tests": (
                "BH across 7 candidate daily-mean sign-flip tests"
            ),
            "delta_tests": (
                "BH across 7 signal-minus-control daily-delta sign-flip tests"
            ),
        },
        "gate": GATE,
        "selection_rule": (
            "among strict passes maximize minimum yearly .10 MeanR; "
            "then .20 MeanR; then lower .10 max drawdown; then id"
        ),
        "candidates": results,
        "strict_pass_count": len(strict),
        "selected_for_final_2026": selected,
    }

    out = args.output_dir / "as2_execution_lab_iii_analysis.json"
    out.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    print("=== AS2 EXECUTION LAB III V1 ===")
    print(f"INPUT ROWS: {input_rows}")
    print("BASELINE REPRODUCTION: PASS")
    print("DEVELOPMENT: 2017-2025")
    print("2026 ACCESSED: FALSE")

    ordered = sorted(
        results,
        key=lambda r: (
            r["strict_pass"],
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
            f"{r['candidate']} | N={s10['n']} "
            f"| .10 MeanR={s10['mean_r']} PF={s10['pf']} "
            f"| .20 MeanR={s20['mean_r']} PF={s20['pf']} "
            f"| Years+={r['positive_years']}/9 "
            f"| Early={r['early_2017_2022']['mean_r']} "
            f"| Late={r['late_2023_2025']['mean_r']} "
            f"| PASS={r['strict_pass']}"
        )
        print(
            f"  signal q={st['bh_q']} "
            f"| delta/day={d['mean_daily_delta']} "
            f"CI95={d['ci95']} delta q={d['bh_q']} "
            f"| withoutTop1={s10['mean_r_without_top_1pct_winners']}"
        )

    print(f"STRICT PASS COUNT: {len(strict)}")
    print(f"SELECTED FOR FINAL 2026: {selected}")
    print(f"REPORT: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
