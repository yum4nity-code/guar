#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ID = "GUARDIAN-E2-SPARSIFICATION-LAB-I-V1"
SEED = 20260918
BOOTSTRAPS = 4000
PERMUTATIONS = 4000
MIN_RISK = 0.01
H = 60

EXPECTED_HIST_SHA256 = "524f9a07e5e0980465d22abd70f4f672c7615b832ce8d82fefcb922276f73f3b"

YEARS = tuple(range(2005, 2026))
HIST_YEARS = set(range(2005, 2017))
MID_YEARS = set(range(2017, 2023))
RECENT_YEARS = {2023, 2024, 2025}

CANDIDATES = [
    {
        "id": "SP0_BASE_E2",
        "description": "Original E2 event set; no extra sparsification",
        "min_margin": 2,
        "min_active": 3,
        "unanimous": False,
        "active_exact": None,
    },
    {
        "id": "SP1_MARGIN3",
        "description": "Require absolute equal-weight vote margin >=3",
        "min_margin": 3,
        "min_active": 3,
        "unanimous": False,
        "active_exact": None,
    },
    {
        "id": "SP2_MARGIN4",
        "description": "Require absolute equal-weight vote margin >=4",
        "min_margin": 4,
        "min_active": 3,
        "unanimous": False,
        "active_exact": None,
    },
    {
        "id": "SP3_ACTIVE4",
        "description": "Require at least 4 active component votes",
        "min_margin": 2,
        "min_active": 4,
        "unanimous": False,
        "active_exact": None,
    },
    {
        "id": "SP4_ACTIVE4_MARGIN3",
        "description": "Require >=4 active votes and vote margin >=3",
        "min_margin": 3,
        "min_active": 4,
        "unanimous": False,
        "active_exact": None,
    },
    {
        "id": "SP5_UNANIMOUS",
        "description": "Require every active component vote to agree",
        "min_margin": 2,
        "min_active": 3,
        "unanimous": True,
        "active_exact": None,
    },
    {
        "id": "SP6_ACTIVE5",
        "description": "Require all five components active; original E2 margin rule retained",
        "min_margin": 2,
        "min_active": 5,
        "unanimous": False,
        "active_exact": 5,
    },
]

GATE = {
    "n_min": 500,
    "historical_n_min": 250,
    "mid_n_min": 150,
    "recent_n_min": 75,
    "cost_010_pf_min": 1.10,
    "cost_010_mean_gt": 0.0,
    "cost_020_pf_min": 1.05,
    "cost_020_mean_gt": 0.0,
    "positive_years_min": 15,
    "historical_mean_gt": 0.0,
    "mid_mean_gt": 0.0,
    "recent_mean_gt": 0.0,
    "signal_bootstrap_ci95_lower_gt": 0.0,
    "without_top1_mean_gt": 0.0,
    "all_leave_one_year_out_mean_gt": 0.0,
    "signal_bh_q_max": 0.10,
    "delta_mean_gt": 0.0,
    "delta_ci95_lower_gt": 0.0,
    "delta_bh_q_max": 0.10,
    "matched_delta_gt": 0.0,
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


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
    raw = row["h60_endpoint_r"]
    risk = row["risk"]
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
        "ci95": [percentile(boots, .025), percentile(boots, .975)],
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


def candidate_match(row, c):
    margin = row["vote_margin"]
    active = row["active_n"]

    if active < c["min_active"] or margin < c["min_margin"]:
        return False

    if c["active_exact"] is not None and active != c["active_exact"]:
        return False

    if c["unanimous"] and margin != active:
        return False

    return True


def parse_file(path: Path, expected_period: str):
    rows = []
    seen_patterns = Counter()

    with path.open(newline="", encoding="utf-8") as f:
        for raw in csv.DictReader(f):
            if raw.get("family") != "E2_DIVERSE_SCORE_5":
                continue

            var = raw.get("variant")
            if var not in {"SIGNAL", "CONTROL_OPPOSITE"}:
                continue

            risk = fnum(raw.get("risk"))
            endpoint = fnum(raw.get("h60_endpoint_r"))
            margin_f = fnum(raw.get("event_strength"))
            active_f = fnum(raw.get("event_aux"))

            if risk is None or endpoint is None or margin_f is None or active_f is None:
                continue

            margin = int(round(margin_f))
            active = int(round(active_f))

            if (
                abs(margin_f - margin) > 1e-9
                or abs(active_f - active) > 1e-9
            ):
                raise RuntimeError(
                    f"non-integer vote metadata: margin={margin_f}, active={active_f}"
                )

            if (
                active < 3
                or active > 5
                or margin < 2
                or margin > active
                or (active - margin) % 2 != 0
            ):
                raise RuntimeError(
                    f"invalid E2 vote metadata: margin={margin}, active={active}"
                )

            t = datetime.fromisoformat(raw["entry_time"])
            year = t.year

            if expected_period == "historical":
                if not (2005 <= year <= 2016):
                    raise RuntimeError(
                        f"historical input contains unexpected year {year}"
                    )
            elif expected_period == "extension":
                if not (2017 <= year <= 2025):
                    raise RuntimeError(
                        f"extension input contains unexpected year {year}"
                    )
            else:
                raise RuntimeError("bad expected_period")

            z = {
                "entry_time": t,
                "date": t.date().isoformat(),
                "year": year,
                "variant": var,
                "risk": risk,
                "h60_endpoint_r": endpoint,
                "vote_margin": margin,
                "active_n": active,
                "crossed_gap": truthy(raw.get("outcome_crossed_calendar_gap")),
            }
            rows.append(z)

            if var == "SIGNAL":
                seen_patterns[(active, margin)] += 1

    return rows, seen_patterns


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--historical", required=True, type=Path)
    ap.add_argument("--extension", required=True, type=Path)
    ap.add_argument("--output-dir", required=True, type=Path)
    args = ap.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    actual_hist_sha = sha256_file(args.historical)
    if actual_hist_sha != EXPECTED_HIST_SHA256:
        raise RuntimeError(
            f"historical signals hash mismatch: expected {EXPECTED_HIST_SHA256}, got {actual_hist_sha}"
        )

    hist, hist_patterns = parse_file(args.historical, "historical")
    ext, ext_patterns = parse_file(args.extension, "extension")

    combined = hist + ext

    if not combined:
        raise RuntimeError("no E2 rows found")

    raw = defaultdict(lambda: defaultdict(list))
    rejected_gap = defaultdict(int)
    rejected_risk = defaultdict(int)

    for row in combined:
        for c in CANDIDATES:
            if not candidate_match(row, c):
                continue

            key = c["id"]

            if row["risk"] < MIN_RISK:
                rejected_risk[(key, row["variant"])] += 1
                continue

            if row["crossed_gap"]:
                rejected_gap[(key, row["variant"])] += 1
                continue

            raw[key][row["variant"]].append(row)

    prepared = {}
    prelim = {}
    signal_p = {}
    delta_p = {}

    for i, c in enumerate(CANDIDATES):
        key = c["id"]

        sig = non_overlap(raw[key]["SIGNAL"], H)
        ctl = non_overlap(raw[key]["CONTROL_OPPOSITE"], H)

        if len(sig) != len(ctl):
            raise RuntimeError(
                f"{key} signal/control count mismatch after filtering: {len(sig)} vs {len(ctl)}"
            )

        sig_times = [r["entry_time"] for r in sig]
        ctl_times = [r["entry_time"] for r in ctl]
        if sig_times != ctl_times:
            raise RuntimeError(f"{key} signal/control timestamps differ")

        vf = lambda r: rval(r, .10)

        st = signal_signflip(sig, vf, SEED + i * 100)
        dt = daily_delta(sig, ctl, vf, SEED + i * 100 + 1)

        prepared[key] = (sig, ctl)
        prelim[key] = (st, dt)
        signal_p[key] = st["p_one_sided"]
        delta_p[key] = dt["p_one_sided"]

    signal_q = bh(signal_p)
    delta_q = bh(delta_p)

    results = []

    for i, c in enumerate(CANDIDATES):
        key = c["id"]
        sig, ctl = prepared[key]

        s10_vals = [rval(r, .10) for r in sig]
        s20_vals = [rval(r, .20) for r in sig]
        c10_vals = [rval(r, .10) for r in ctl]

        s10 = stats(s10_vals)
        s20 = stats(s20_vals)
        c10 = stats(c10_vals)

        vf10 = lambda r: rval(r, .10)

        boot = bootstrap_signal(sig, vf10, SEED + i * 1000)
        conc = concentration(s10_vals)
        dt = prelim[key][1]
        matched = matched_delta(sig, ctl, vf10)

        hist_sig = [r for r in sig if r["year"] in HIST_YEARS]
        mid_sig = [r for r in sig if r["year"] in MID_YEARS]
        recent_sig = [r for r in sig if r["year"] in RECENT_YEARS]

        hist_stats = stats([rval(r, .10) for r in hist_sig])
        mid_stats = stats([rval(r, .10) for r in mid_sig])
        recent_stats = stats([rval(r, .10) for r in recent_sig])

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
            "historical_n": hist_stats["n"] >= GATE["historical_n_min"],
            "mid_n": mid_stats["n"] >= GATE["mid_n_min"],
            "recent_n": recent_stats["n"] >= GATE["recent_n_min"],
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
            "historical_mean": (
                hist_stats["mean_r"] is not None
                and hist_stats["mean_r"] > GATE["historical_mean_gt"]
            ),
            "mid_mean": (
                mid_stats["mean_r"] is not None
                and mid_stats["mean_r"] > GATE["mid_mean_gt"]
            ),
            "recent_mean": (
                recent_stats["mean_r"] is not None
                and recent_stats["mean_r"] > GATE["recent_mean_gt"]
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
                signal_q[key] is not None
                and signal_q[key] <= GATE["signal_bh_q_max"]
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
                delta_q[key] is not None
                and delta_q[key] <= GATE["delta_bh_q_max"]
            ),
            "matched_delta": (
                matched["weighted_mean_delta"] is not None
                and matched["weighted_mean_delta"]
                > GATE["matched_delta_gt"]
            ),
        }

        subperiod_means = [
            hist_stats["mean_r"],
            mid_stats["mean_r"],
            recent_stats["mean_r"],
        ]
        worst_subperiod = (
            min(x for x in subperiod_means if x is not None)
            if all(x is not None for x in subperiod_means)
            else None
        )

        results.append({
            "candidate": c,
            "signal_cost_010": {
                **s10,
                **conc,
                "bootstrap_daily": boot,
            },
            "signal_cost_020": s20,
            "control_cost_010": c10,
            "signal_test": {
                **prelim[key][0],
                "bh_q": signal_q[key],
            },
            "signal_vs_control": {
                **dt,
                "bh_q": delta_q[key],
            },
            "matched_control": matched,
            "historical_2005_2016": hist_stats,
            "mid_2017_2022": mid_stats,
            "recent_2023_2025": recent_stats,
            "worst_subperiod_mean_010": worst_subperiod,
            "positive_years": positive_years,
            "minimum_year_mean_010": (
                min(yearly_means) if yearly_means else None
            ),
            "yearly": yearly,
            "leave_one_year_out": loo,
            "rejected_gap_signal": rejected_gap[(key, "SIGNAL")],
            "rejected_risk_signal": rejected_risk[(key, "SIGNAL")],
            "gate_checks": checks,
            "strict_pass": all(checks.values()),
        })

    strict = [r for r in results if r["strict_pass"]]
    selected = None

    if strict:
        strict.sort(
            key=lambda r: (
                r["worst_subperiod_mean_010"],
                r["signal_cost_020"]["mean_r"],
                -r["signal_cost_010"]["max_drawdown_r"],
                r["candidate"]["id"],
            ),
            reverse=True,
        )
        selected = strict[0]["candidate"]["id"]

    pattern_counts = {
        "historical_signal": {
            f"active{a}_margin{m}": n
            for (a, m), n in sorted(hist_patterns.items())
        },
        "extension_signal": {
            f"active{a}_margin{m}": n
            for (a, m), n in sorted(ext_patterns.items())
        },
    }

    report = {
        "schema": 1,
        "status": "COMPLETE",
        "id": ID,
        "historical_input_sha256": actual_hist_sha,
        "extension_input_sha256": sha256_file(args.extension),
        "candidate_count": len(CANDIDATES),
        "candidates_frozen": CANDIDATES,
        "pattern_counts": pattern_counts,
        "development_scope": "2005-2025 all contaminated/development",
        "historical_2005_2016_reread_by_this_lab": False,
        "extension_2017_2025_generated_by_this_lab": True,
        "protected_2026_opened": False,
        "gate": GATE,
        "multiple_testing": {
            "signal": "BH across 7 absolute signal tests",
            "delta": "BH across 7 signal-minus-opposite-control tests",
        },
        "selection_rule": (
            "among strict passes maximize the worst aggregate subperiod "
            "MeanR across 2005-2016, 2017-2022, 2023-2025; then .20 MeanR; "
            "then lower .10 max drawdown; then deterministic id"
        ),
        "results": results,
        "strict_pass_count": len(strict),
        "selected_for_final_2026": selected,
    }

    out = args.output_dir / "e2_sparsification_analysis.json"
    out.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    print("=== GUARDIAN E2 SPARSIFICATION LAB I V1 ===")
    print(f"HISTORICAL SHA256: {actual_hist_sha}")
    print(f"EXTENSION SHA256: {sha256_file(args.extension)}")
    print("DEVELOPMENT: 2005-2025")
    print("2005-2016 MARKET REREAD BY THIS LAB: FALSE")
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
        key = r["candidate"]["id"]
        s10 = r["signal_cost_010"]
        s20 = r["signal_cost_020"]
        d = r["signal_vs_control"]
        st = r["signal_test"]

        print(
            f"{key} | N={s10['n']} "
            f"| .10 MeanR={s10['mean_r']} PF={s10['pf']} "
            f"| .20 MeanR={s20['mean_r']} PF={s20['pf']} "
            f"| Years+={r['positive_years']}/21 "
            f"| Hist={r['historical_2005_2016']['mean_r']} "
            f"| Mid={r['mid_2017_2022']['mean_r']} "
            f"| Recent={r['recent_2023_2025']['mean_r']} "
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
