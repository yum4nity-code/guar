#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path

ID = "AS2-CONVERSION-LAB-V1"
EXPECTED_INPUT_SHA256 = "18aece1bf876d598cbfa30b83ad3ca19659b787b99eb0e9949d100866a1e12c2"
SEED = 20260918
BOOTSTRAPS = 4000
PERMUTATIONS = 4000
MIN_RISK = 0.01
YEARS = (2023, 2024, 2025)
HORIZONS = (15, 30, 60)
LEVELS = (0.5, 1.0, 1.5, 2.0)
COSTS = (0.10, 0.20)

# Small, preregistered conversion set. No entry/filter changes.
CANDIDATES = (
    [{"id": f"TIME_H{h}", "kind": "TIME", "h": h, "level": None} for h in HORIZONS]
    + [
        {"id": f"BARRIER_{str(level).replace('.','p')}R_H{h}", "kind": "BARRIER", "h": h, "level": level}
        for h in HORIZONS for level in LEVELS
    ]
)

GATE = {
    "n_min": 500,
    "cost_010_pf_min": 1.10,
    "cost_010_mean_gt": 0.0,
    "cost_020_pf_min": 1.05,
    "cost_020_mean_gt": 0.0,
    "positive_years_min": 2,
    "signal_bootstrap_ci95_lower_gt": 0.0,
    "without_top1_mean_gt": 0.0,
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


def fint(x):
    try:
        return int(float(x))
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
    pos = (len(a) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return a[lo]
    w = pos - lo
    return a[lo] * (1 - w) + a[hi] * w


def stats(xs):
    xs = [x for x in xs if x is not None and math.isfinite(x)]
    if not xs:
        return {
            "n": 0, "mean_r": None, "median_r": None, "pf": None,
            "win_pct": None, "sum_r": None, "max_drawdown_r": None,
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
        "win_pct": 100.0 * sum(x > 0 for x in xs) / len(xs),
        "sum_r": sum(xs),
        "max_drawdown_r": maxdd,
    }


def key_level(level):
    return str(level).replace(".", "p")


def gross_outcome(row, cand):
    h = cand["h"]
    endpoint = row.get(f"h{h}_endpoint_r")
    if endpoint is None:
        return None

    if cand["kind"] == "TIME":
        return endpoint

    level = cand["level"]
    k = key_level(level)
    minute = row.get(f"hit_{k}_minute")
    winner = row.get(f"hit_{k}_winner")

    if minute is not None and minute <= h:
        # Conservative same-M1 convention: AMBIGUOUS is treated as the stop side.
        if winner == "PLUS":
            return level
        if winner in {"MINUS", "AMBIGUOUS"}:
            return -level
        raise RuntimeError(f"unexpected threshold winner: {winner!r}")

    return endpoint


def net_outcome(row, cand, cost_price):
    g = gross_outcome(row, cand)
    risk = row.get("risk")
    if g is None or risk in (None, 0):
        return None
    return g - cost_price / risk


def by_day(rows, value_fn):
    d = defaultdict(list)
    for r in rows:
        x = value_fn(r)
        if x is not None and math.isfinite(x):
            d[r["date"]].append(x)
    return d


def signflip_mean(rows, value_fn, seed):
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
        "ci95": [pct(vals, .025), pct(vals, .975)],
        "median": pct(vals, .5),
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
            "common_days": 0, "mean_daily_delta": None,
            "ci95": [None, None], "p_one_sided": None,
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


def matched_delta(sig, ctl, value_fn):
    sm = {r["entry_time"]: value_fn(r) for r in sig}
    cm = {r["entry_time"]: value_fn(r) for r in ctl}
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


def concentration(values):
    xs = [x for x in values if x is not None and math.isfinite(x)]
    if not xs:
        return {"mean_r_without_top_1pct_winners": None}

    remove_n = max(1, math.ceil(.01 * len(xs)))
    order = sorted(range(len(xs)), key=lambda i: xs[i], reverse=True)
    excluded = set(order[:remove_n])
    rem = [x for i, x in enumerate(xs) if i not in excluded]
    return {
        "mean_r_without_top_1pct_winners": (
            sum(rem) / len(rem) if rem else None
        )
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


def parse_row(row):
    t = row["entry_time"]
    y = int(row["year"])
    z = {
        "entry_time": t,
        "date": t[:10],
        "year": y,
        "risk": fnum(row.get("risk")),
    }

    for h in (5, 15, 30, 60):
        for field in (
            "endpoint_r", "mfe_r", "mae_r",
            "minutes_to_mfe", "minutes_to_mae", "mfe_before_mae",
        ):
            v = row.get(f"h{h}_{field}")
            if field in {"minutes_to_mfe", "minutes_to_mae"}:
                z[f"h{h}_{field}"] = fint(v)
            elif field == "mfe_before_mae":
                s = str(v).strip().lower()
                z[f"h{h}_{field}"] = (
                    True if s == "true" else False if s == "false" else None
                )
            else:
                z[f"h{h}_{field}"] = fnum(v)

    for level in LEVELS:
        k = key_level(level)
        z[f"hit_{k}_winner"] = (row.get(f"hit_{k}_winner") or "").strip()
        z[f"hit_{k}_minute"] = fint(row.get(f"hit_{k}_minute"))

    return z


def path_diagnostics(sig):
    horizons = []
    for h in (5, 15, 30, 60):
        mfes = [r[f"h{h}_mfe_r"] for r in sig if r[f"h{h}_mfe_r"] is not None]
        maes = [r[f"h{h}_mae_r"] for r in sig if r[f"h{h}_mae_r"] is not None]
        tmfe = [r[f"h{h}_minutes_to_mfe"] for r in sig if r[f"h{h}_minutes_to_mfe"] is not None]
        tmae = [r[f"h{h}_minutes_to_mae"] for r in sig if r[f"h{h}_minutes_to_mae"] is not None]
        order = [r[f"h{h}_mfe_before_mae"] for r in sig if r[f"h{h}_mfe_before_mae"] is not None]

        mmfe = sum(mfes) / len(mfes) if mfes else None
        mmae = sum(maes) / len(maes) if maes else None
        horizons.append({
            "horizon": h,
            "mean_mfe_r": mmfe,
            "mean_mae_r": mmae,
            "mfe_mae_ratio_of_means": (
                mmfe / mmae if mmfe is not None and mmae not in (None, 0) else None
            ),
            "mfe_before_mae_pct": (
                100.0 * sum(order) / len(order) if order else None
            ),
            "minutes_to_mfe": {
                "median": statistics.median(tmfe) if tmfe else None,
                "p25": pct(tmfe, .25),
                "p75": pct(tmfe, .75),
            },
            "minutes_to_mae": {
                "median": statistics.median(tmae) if tmae else None,
                "p25": pct(tmae, .25),
                "p75": pct(tmae, .75),
            },
        })

    touches = []
    for h in HORIZONS:
        for level in LEVELS:
            k = key_level(level)
            counts = {"PLUS": 0, "MINUS": 0, "AMBIGUOUS": 0, "NONE": 0}
            mins = {"PLUS": [], "MINUS": [], "AMBIGUOUS": []}
            for r in sig:
                m = r[f"hit_{k}_minute"]
                w = r[f"hit_{k}_winner"]
                if m is None or m > h:
                    counts["NONE"] += 1
                elif w in counts:
                    counts[w] += 1
                    mins[w].append(m)
                else:
                    counts["NONE"] += 1

            n = len(sig)
            touches.append({
                "horizon": h,
                "level_r": level,
                "n": n,
                "plus_pct": 100.0 * counts["PLUS"] / n if n else None,
                "minus_pct": 100.0 * counts["MINUS"] / n if n else None,
                "ambiguous_pct": 100.0 * counts["AMBIGUOUS"] / n if n else None,
                "none_pct": 100.0 * counts["NONE"] / n if n else None,
                "median_plus_minute": (
                    statistics.median(mins["PLUS"]) if mins["PLUS"] else None
                ),
                "median_minus_minute": (
                    statistics.median(mins["MINUS"]) if mins["MINUS"] else None
                ),
            })
    return {"horizons": horizons, "threshold_touches": touches}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--output-dir", required=True, type=Path)
    args = ap.parse_args()

    actual = sha256_file(args.input)
    if actual != EXPECTED_INPUT_SHA256:
        raise RuntimeError(
            f"input SHA256 mismatch: expected {EXPECTED_INPUT_SHA256}, got {actual}"
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)

    signal = []
    control = []
    total = 0

    with args.input.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            total += 1
            if row.get("family") != "AS2_SKEW_VOV":
                continue
            if truthy(row.get("outcome_crossed_calendar_gap")):
                continue

            z = parse_row(row)
            if z["risk"] is None or z["risk"] < MIN_RISK:
                continue
            if z["year"] not in YEARS:
                raise RuntimeError(f"unexpected year in conversion input: {z['year']}")

            var = row.get("variant")
            if var == "SIGNAL":
                signal.append(z)
            elif var == "CONTROL_OPPOSITE":
                control.append(z)

    if len(signal) != len(control):
        raise RuntimeError(
            f"signal/control raw count mismatch: {len(signal)} vs {len(control)}"
        )

    preliminary = {}
    sig_p = {}
    delta_p = {}

    for i, cand in enumerate(CANDIDATES):
        vf = lambda r, c=cand: net_outcome(r, c, .10)
        s_test = signflip_mean(signal, vf, SEED + i * 100)
        d_test = daily_delta(signal, control, vf, SEED + i * 100 + 1)
        preliminary[cand["id"]] = (s_test, d_test)
        sig_p[cand["id"]] = s_test["p_one_sided"]
        delta_p[cand["id"]] = d_test["p_one_sided"]

    sig_q = bh(sig_p)
    delta_q = bh(delta_p)

    results = []

    for i, cand in enumerate(CANDIDATES):
        vf10 = lambda r, c=cand: net_outcome(r, c, .10)

        sig10_values = [vf10(r) for r in signal]
        sig20_values = [net_outcome(r, cand, .20) for r in signal]
        ctl10_values = [vf10(r) for r in control]

        s10 = stats(sig10_values)
        s20 = stats(sig20_values)
        c10 = stats(ctl10_values)

        boot = bootstrap_signal(signal, vf10, SEED + i * 1000)
        conc = concentration(sig10_values)
        d = daily_delta(signal, control, vf10, SEED + i * 1000 + 1)
        matched = matched_delta(signal, control, vf10)

        yearly = []
        positive_years = 0
        yearly_means = []
        for y in YEARS:
            sy = [r for r in signal if r["year"] == y]
            cy = [r for r in control if r["year"] == y]
            ys = stats([vf10(r) for r in sy])
            yc = stats([vf10(r) for r in cy])
            yd = daily_delta(
                sy, cy, vf10, SEED + i * 10000 + y
            )
            if ys["mean_r"] is not None:
                yearly_means.append(ys["mean_r"])
                if ys["mean_r"] > 0:
                    positive_years += 1
            yearly.append({
                "year": y,
                "signal_cost_010": ys,
                "control_cost_010": yc,
                "delta_cost_010": yd,
            })

        checks = {
            "n": s10["n"] >= GATE["n_min"],
            "cost_010_pf": s10["pf"] is not None and s10["pf"] >= GATE["cost_010_pf_min"],
            "cost_010_mean": s10["mean_r"] is not None and s10["mean_r"] > GATE["cost_010_mean_gt"],
            "cost_020_pf": s20["pf"] is not None and s20["pf"] >= GATE["cost_020_pf_min"],
            "cost_020_mean": s20["mean_r"] is not None and s20["mean_r"] > GATE["cost_020_mean_gt"],
            "positive_years": positive_years >= GATE["positive_years_min"],
            "signal_bootstrap": boot["ci95"][0] is not None and boot["ci95"][0] > GATE["signal_bootstrap_ci95_lower_gt"],
            "without_top1": (
                conc["mean_r_without_top_1pct_winners"] is not None
                and conc["mean_r_without_top_1pct_winners"] > GATE["without_top1_mean_gt"]
            ),
            "signal_bh_q": sig_q[cand["id"]] is not None and sig_q[cand["id"]] <= GATE["signal_bh_q_max"],
            "delta_mean": d["mean_daily_delta"] is not None and d["mean_daily_delta"] > GATE["delta_mean_gt"],
            "delta_ci95": d["ci95"][0] is not None and d["ci95"][0] > GATE["delta_ci95_lower_gt"],
            "delta_bh_q": delta_q[cand["id"]] is not None and delta_q[cand["id"]] <= GATE["delta_bh_q_max"],
            "matched_delta": (
                matched["weighted_mean_delta"] is not None
                and matched["weighted_mean_delta"] > GATE["matched_delta_gt"]
            ),
        }

        result = {
            "candidate": cand,
            "signal_cost_010": {**s10, **conc, "bootstrap_daily": boot},
            "signal_cost_020": s20,
            "control_cost_010": c10,
            "signal_test": {
                **preliminary[cand["id"]][0],
                "bh_q": sig_q[cand["id"]],
            },
            "signal_vs_control": {
                **d,
                "bh_q": delta_q[cand["id"]],
            },
            "matched_control": matched,
            "positive_years": positive_years,
            "minimum_year_mean_010": min(yearly_means) if yearly_means else None,
            "yearly": yearly,
            "gate_checks": checks,
            "conversion_pass": all(checks.values()),
        }
        results.append(result)

    strict = [r for r in results if r["conversion_pass"]]
    selected = None
    if strict:
        # Frozen selection rule: robustness first, then stress performance,
        # then lower drawdown, then deterministic candidate id.
        strict.sort(
            key=lambda r: (
                r["minimum_year_mean_010"],
                r["signal_cost_020"]["mean_r"],
                -r["signal_cost_010"]["max_drawdown_r"],
                r["candidate"]["id"],
            ),
            reverse=True,
        )
        selected = strict[0]["candidate"]["id"]

    report = {
        "schema": 1,
        "status": "COMPLETE",
        "id": ID,
        "source": "Assembly I signals.csv only; no market payload reread",
        "input_sha256": actual,
        "input_rows_total": total,
        "as2_signal_rows": len(signal),
        "as2_control_rows": len(control),
        "entry_rule_frozen": "AS2_SKEW_VOV unchanged",
        "candidate_count": len(CANDIDATES),
        "candidate_set": CANDIDATES,
        "ambiguity_policy": "same-M1 PLUS+MINUS touch is conservatively scored as MINUS",
        "cost_semantics": "subtract 0.10/0.20 absolute XAUUSD price units divided by row risk",
        "development_period": "2023-2025 already contaminated by Assembly I",
        "protected_2026_opened": False,
        "gate": GATE,
        "selection_rule": "among strict passes maximize minimum yearly .10 MeanR; then .20 MeanR; then lower .10 max drawdown; then deterministic id",
        "path_diagnostics": path_diagnostics(signal),
        "candidates": results,
        "strict_pass_count": len(strict),
        "selected_for_final_2026": selected,
    }

    out = args.output_dir / "as2_conversion_analysis.json"
    out.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    print("=== AS2 CONVERSION LAB V1 ===")
    print(f"INPUT SHA256: {actual}")
    print(f"AS2 SIGNAL / CONTROL: {len(signal)} / {len(control)}")
    print("MARKET PAYLOAD REREAD: FALSE")
    print("2026 ACCESSED: FALSE")

    ordered = sorted(
        results,
        key=lambda r: (
            r["conversion_pass"],
            r["signal_cost_010"]["mean_r"] if r["signal_cost_010"]["mean_r"] is not None else -999,
        ),
        reverse=True,
    )
    for r in ordered:
        c = r["candidate"]["id"]
        s10 = r["signal_cost_010"]
        s20 = r["signal_cost_020"]
        d = r["signal_vs_control"]
        st = r["signal_test"]
        print(
            f"{c} | N={s10['n']} | .10 MeanR={s10['mean_r']} PF={s10['pf']} "
            f"| .20 MeanR={s20['mean_r']} PF={s20['pf']} "
            f"| Years+={r['positive_years']}/3 | PASS={r['conversion_pass']}"
        )
        print(
            f"  signal q={st['bh_q']} | delta/day={d['mean_daily_delta']} "
            f"CI95={d['ci95']} delta q={d['bh_q']} "
            f"| withoutTop1={s10['mean_r_without_top_1pct_winners']}"
        )

    print(f"STRICT PASS COUNT: {len(strict)}")
    print(f"SELECTED FOR FINAL 2026: {selected}")
    print(f"REPORT: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
