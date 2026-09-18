#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import itertools
import json
import math
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

UTC = timezone.utc
ID = "GUARDIAN-ATLAS-V-V1"

EXPECTED = {
    "data_loader_v1.py": "9129e1d48ce5b05ef997e9fa12b6e55a674c2be052e69ed94bc3fb4c235b2f6b",
    "preflight_v1.py": "d0e799d4a999683c4c1a46495b56a6720d40ab61058ffe2d36a1456ab69cd354",
    "night_atlas_v1.py": "edf95e275cdd49846fed81c06d180b5190e63c25de347137136b94e0f3674860",
}

FAMILIES = {
    "A5_01_RETURN_AUTOCORR_REGIME": {
        "kind": "DIRECTIONAL",
        "primary_horizon": 60,
        "secondary_horizons": [30, 120],
        "signal": "hourly, lag-1 Pearson autocorrelation of prior 24 completed M5 returns has abs(rho)>=0.25; follow latest return if rho>0, fade latest return if rho<0",
        "control": "same event and timestamp; opposite direction",
        "mechanism": "serial dependence regime, distinct from return-distribution moments",
    },
    "A5_02_ROBUST_LOCATION_SHIFT": {
        "kind": "DIRECTIONAL",
        "primary_horizon": 60,
        "secondary_horizons": [30, 120],
        "signal": "hourly, abs(median return)/(1.4826*MAD) >=0.35 over prior 24 completed M5 returns; follow median sign",
        "control": "same event and timestamp; opposite direction",
        "mechanism": "robust distribution-location shift rather than mean, EMA, or streak",
    },
    "A5_03_TAIL_COUNT_EXHAUSTION": {
        "kind": "DIRECTIONAL",
        "primary_horizon": 60,
        "secondary_horizons": [30, 120],
        "signal": "hourly, robust tails beyond median +/-1.5*(1.4826*MAD), at least 4 tails and absolute up/down tail-count imbalance >=3; trade opposite dominant tail side",
        "control": "same event and timestamp; trade with dominant tail side",
        "mechanism": "robust discrete tail-count imbalance / exhaustion, not skew-moment threshold",
    },
    "A5_04_PERMUTATION_ENTROPY": {
        "kind": "AMPLITUDE",
        "primary_horizon": 60,
        "secondary_horizons": [120],
        "signal": "hourly, normalized ordinal permutation entropy of prior 24 M5 returns (order 3) <=0.75",
        "control": "same hourly sampling, normalized permutation entropy >=0.95",
        "mechanism": "temporal disorder/complexity of returns",
        "primary_outcome": "max(h_MFE_R,h_MAE_R), non-directional max excursion",
    },
    "A5_05_VOL_CLUSTER_AUTOCORR": {
        "kind": "AMPLITUDE",
        "primary_horizon": 60,
        "secondary_horizons": [120],
        "signal": "hourly, lag-1 Pearson autocorrelation of squared prior 24 completed M5 returns >=0.25",
        "control": "same hourly sampling, abs squared-return autocorrelation <=0.05",
        "mechanism": "volatility clustering persistence rather than volatility level or vol-of-vol",
        "primary_outcome": "max(h_MFE_R,h_MAE_R), non-directional max excursion",
    },
    "A5_06_RANGE_RETENTION": {
        "kind": "AMPLITUDE",
        "primary_horizon": 60,
        "secondary_horizons": [120],
        "signal": "hourly, prior 12 completed M5 bars have close-travel / summed intrabar range <=0.35",
        "control": "same hourly sampling, close-travel / summed intrabar range >=0.65",
        "mechanism": "intrabar range generated but not retained by closes; distinct from net path efficiency",
        "primary_outcome": "max(h_MFE_R,h_MAE_R), non-directional max excursion",
    },
}

GATES = {
    "directional_cheap_fail": {
        "full_n_min": 100,
        "full_cost_010_mean_r_gt": 0.0,
        "control_delta_gt": 0.0,
        "development_2017_2020_mean_r_gt": 0.0,
        "internal_holdout_2021_2022_mean_r_gt": 0.0,
    },
    "directional_freeze_candidate_strict": {
        "full_n_min": 200,
        "holdout_n_min": 60,
        "full_cost_010_pf_min": 1.10,
        "full_cost_010_mean_r_gt": 0.0,
        "full_cost_020_pf_min": 1.05,
        "full_cost_020_mean_r_gt": 0.0,
        "positive_years_min": 4,
        "all_leave_one_year_out_mean_r_gt": 0.0,
        "daily_bootstrap_signal_ci95_lower_gt": 0.0,
        "mean_r_without_top1pct_winners_gt": 0.0,
        "holdout_cost_010_pf_min": 1.05,
        "holdout_cost_010_mean_r_gt": 0.0,
        "holdout_cost_020_mean_r_gt": 0.0,
        "control_common_days_min": 30,
        "matched_control_support_min": 30,
        "matched_control_delta_gt": 0.0,
        "control_delta_mean_r_gt": 0.0,
        "control_delta_ci95_lower_gt": 0.0,
        "holdout_control_delta_gt": 0.0,
        "all_leave_one_year_out_control_delta_gt": 0.0,
        "bh_q_max": 0.10,
    },
    "amplitude_phenomenon_candidate": {
        "full_n_min": 500,
        "holdout_n_min": 150,
        "full_relative_uplift_pct_min": 5.0,
        "daily_delta_ci95_lower_gt": 0.0,
        "holdout_delta_gt": 0.0,
        "all_leave_one_year_out_delta_gt": 0.0,
        "matched_control_support_min": 100,
        "matched_control_delta_gt": 0.0,
        "bh_q_max": 0.10,
    },
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_local(campaign: Path):
    got = {}
    for name, expected in EXPECTED.items():
        p = campaign / name
        if not p.is_file():
            raise RuntimeError(f"missing audited dependency/reference: {p}")
        actual = sha256_file(p)
        got[name] = actual
        if actual != expected:
            raise RuntimeError(f"hash mismatch {name}: expected {expected}, got {actual}")
    return got


def sgn(x):
    return 1 if x > 0 else -1 if x < 0 else 0


def median(xs):
    return statistics.median(xs)


def pearson(x, y):
    if len(x) != len(y) or len(x) < 3:
        return None
    mx = sum(x) / len(x)
    my = sum(y) / len(y)
    vx = sum((a - mx) ** 2 for a in x)
    vy = sum((b - my) ** 2 for b in y)
    if vx <= 0 or vy <= 0:
        return None
    cov = sum((a - mx) * (b - my) for a, b in zip(x, y))
    return cov / math.sqrt(vx * vy)


class Atlas5:
    def __init__(self, na, writer):
        self.na = na
        self.base = na.Atlas(writer)
        self.just_gap = False

    def __getattr__(self, name):
        return getattr(self.base, name)

    def snapshot(self, *args, **kwargs):
        row = self.base.snapshot(*args, **kwargs)
        row["job_id"] = ID
        return row

    def add(self, t, entry, side, family, variant, strength=None, aux=None):
        na = self.na
        if not (na.DISCOVERY_START <= t < na.END_EXCLUSIVE - na.timedelta(minutes=na.MAX_HORIZON_MIN)):
            return
        row = self.snapshot(t, entry, side, family, variant, strength, aux, None, None)
        self.base.pending.append(na.Pending(row, side, entry, float(row["risk"])))
        self.base.signals += 1
        key = f"{family}|{variant}"
        self.base.family_counts[key] = self.base.family_counts.get(key, 0) + 1

    @staticmethod
    def contiguous(bars):
        if len(bars) < 2:
            return True
        return all(
            a.available_at is not None
            and b.available_at is not None
            and (b.available_at - a.available_at).total_seconds() == 300
            for a, b in zip(bars, bars[1:])
        )

    @staticmethod
    def returns_from_bars(bars):
        return [bars[i].close - bars[i - 1].close for i in range(1, len(bars))]

    def return_autocorr(self, t, entry, hist, atr):
        if t.minute != 0:
            return
        bars = hist[-25:]
        if len(bars) != 25 or not self.contiguous(bars):
            return
        rs = self.returns_from_bars(bars)
        rho = pearson(rs[:-1], rs[1:])
        if rho is None or abs(rho) < 0.25:
            return
        last_dir = sgn(rs[-1])
        if last_dir == 0:
            return
        side = last_dir if rho > 0 else -last_dir
        self.add(t, entry, side, "A5_01_RETURN_AUTOCORR_REGIME", "SIGNAL", strength=abs(rho), aux=rs[-1] / atr)
        self.add(t, entry, -side, "A5_01_RETURN_AUTOCORR_REGIME", "CONTROL_OPPOSITE", strength=abs(rho), aux=rs[-1] / atr)

    def robust_location(self, t, entry, hist, atr):
        if t.minute != 0:
            return
        bars = hist[-25:]
        if len(bars) != 25 or not self.contiguous(bars):
            return
        rs = self.returns_from_bars(bars)
        m = median(rs)
        mad = median([abs(r - m) for r in rs])
        scale = 1.4826 * mad
        if scale <= 0:
            return
        z = abs(m) / scale
        if z < 0.35 or m == 0:
            return
        side = sgn(m)
        self.add(t, entry, side, "A5_02_ROBUST_LOCATION_SHIFT", "SIGNAL", strength=z, aux=(sum(rs) / len(rs)) / atr)
        self.add(t, entry, -side, "A5_02_ROBUST_LOCATION_SHIFT", "CONTROL_OPPOSITE", strength=z, aux=(sum(rs) / len(rs)) / atr)

    def tail_count_exhaustion(self, t, entry, hist, atr):
        if t.minute != 0:
            return
        bars = hist[-25:]
        if len(bars) != 25 or not self.contiguous(bars):
            return
        rs = self.returns_from_bars(bars)
        m = median(rs)
        mad = median([abs(r - m) for r in rs])
        scale = 1.4826 * mad
        if scale <= 0:
            return
        up = sum(r > m + 1.5 * scale for r in rs)
        dn = sum(r < m - 1.5 * scale for r in rs)
        if up + dn < 4 or abs(up - dn) < 3:
            return
        dominant = 1 if up > dn else -1
        imbalance = abs(up - dn) / (up + dn)
        self.add(t, entry, -dominant, "A5_03_TAIL_COUNT_EXHAUSTION", "SIGNAL", strength=imbalance, aux=(up + dn) / 24.0)
        self.add(t, entry, dominant, "A5_03_TAIL_COUNT_EXHAUSTION", "CONTROL_CONT", strength=imbalance, aux=(up + dn) / 24.0)

    @staticmethod
    def ordinal_pattern3(a, b, c):
        vals = [a, b, c]
        if len(set(vals)) < 3:
            return None
        return tuple(sorted(range(3), key=lambda i: vals[i]))

    def permutation_entropy(self, t, entry, hist, atr):
        if t.minute != 0:
            return
        bars = hist[-25:]
        if len(bars) != 25 or not self.contiguous(bars):
            return
        rs = self.returns_from_bars(bars)
        patterns = []
        for i in range(len(rs) - 2):
            p = self.ordinal_pattern3(rs[i], rs[i + 1], rs[i + 2])
            if p is not None:
                patterns.append(p)
        if len(patterns) < 18:
            return
        counts = Counter(patterns)
        probs = [n / len(patterns) for n in counts.values()]
        h = -sum(p * math.log(p) for p in probs) / math.log(6.0)
        aux = statistics.pstdev(rs) / atr if atr > 0 else None
        if h <= 0.75:
            self.add(t, entry, 1, "A5_04_PERMUTATION_ENTROPY", "SIGNAL", strength=h, aux=aux)
        elif h >= 0.95:
            self.add(t, entry, 1, "A5_04_PERMUTATION_ENTROPY", "CONTROL_HIGH_ENTROPY", strength=h, aux=aux)

    def vol_cluster_autocorr(self, t, entry, hist, atr):
        if t.minute != 0:
            return
        bars = hist[-25:]
        if len(bars) != 25 or not self.contiguous(bars):
            return
        rs = self.returns_from_bars(bars)
        sq = [r * r for r in rs]
        rho = pearson(sq[:-1], sq[1:])
        if rho is None:
            return
        aux = math.sqrt(sum(sq) / len(sq)) / atr if atr > 0 else None
        if rho >= 0.25:
            self.add(t, entry, 1, "A5_05_VOL_CLUSTER_AUTOCORR", "SIGNAL", strength=rho, aux=aux)
        elif abs(rho) <= 0.05:
            self.add(t, entry, 1, "A5_05_VOL_CLUSTER_AUTOCORR", "CONTROL_NEUTRAL_AUTOCORR", strength=abs(rho), aux=aux)

    def range_retention(self, t, entry, hist, atr):
        if t.minute != 0:
            return
        bars = hist[-12:]
        if len(bars) != 12 or not self.contiguous(bars):
            return
        ranges = [b.high - b.low for b in bars]
        total_range = sum(ranges)
        if total_range <= 0:
            return
        close_travel = sum(abs(bars[i].close - bars[i - 1].close) for i in range(1, len(bars)))
        retention = close_travel / total_range
        aux = total_range / (12.0 * atr) if atr > 0 else None
        if retention <= 0.35:
            self.add(t, entry, 1, "A5_06_RANGE_RETENTION", "SIGNAL", strength=retention, aux=aux)
        elif retention >= 0.65:
            self.add(t, entry, 1, "A5_06_RANGE_RETENTION", "CONTROL_HIGH_RETENTION", strength=retention, aux=aux)

    def detect(self, t, entry):
        hist = list(self.base.m5_hist)
        if len(hist) < 193:
            return
        if any(x is None for x in (self.base.atr5.value, self.base.rsi1.value, self.base.rsi5.value, self.base.rsi15.value)):
            return
        atr = float(self.base.atr5.value)
        if atr <= 0 or self.just_gap:
            return
        for ts in (
            hist[-1].available_at,
            self.base.rsi1.available_at,
            self.base.rsi5.available_at,
            self.base.rsi15.available_at,
            self.base.atr5.available_at,
        ):
            if ts is not None and ts > t:
                raise self.na.AdmissionError("lookahead")

        self.return_autocorr(t, entry, hist, atr)
        self.robust_location(t, entry, hist, atr)
        self.tail_count_exhaustion(t, entry, hist, atr)
        self.permutation_entropy(t, entry, hist, atr)
        self.vol_cluster_autocorr(t, entry, hist, atr)
        self.range_retention(t, entry, hist, atr)

    def on_m1(self, bar):
        prev = self.base.prev_m1_timestamp
        self.just_gap = False
        if prev is not None:
            gap_minutes = int((bar.timestamp - prev).total_seconds() // 60)
            if gap_minutes > 1:
                self.just_gap = True

        self.base.roll_day(bar)
        if self.base.prev_m1_timestamp is not None:
            gap_minutes = int((bar.timestamp - self.base.prev_m1_timestamp).total_seconds() // 60)
            if gap_minutes > 1:
                for p in self.base.pending:
                    p.row["outcome_crossed_calendar_gap"] = True
                    p.row["outcome_max_gap_minutes"] = max(
                        int(p.row.get("outcome_max_gap_minutes") or 0),
                        gap_minutes - 1,
                    )

        if bar.timestamp.minute % 5 == 0:
            self.detect(bar.timestamp, bar.open)

        self.base.process_pending(bar)
        self.base.rsi1.update(bar.close, bar.available_at)
        self.base.atr1.update(bar)

        b5 = self.base.agg5.push(bar)
        if b5:
            self.base.after_m5(b5)

        b15 = self.base.agg15.push(bar)
        if b15:
            self.base.rsi15.update(b15.close, b15.available_at)
            self.base.atr15.update(b15)

        self.base.update_day(bar)
        self.base.prev_m1_timestamp = bar.timestamp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True, type=Path)
    ap.add_argument("--standards", required=True, type=Path)
    ap.add_argument("--output-dir", required=True, type=Path)
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args()

    campaign = Path(__file__).resolve().parent
    hashes = verify_local(campaign)
    sys.path.insert(0, str(campaign))
    na = importlib.import_module("night_atlas_v1")

    if na.DISCOVERY_START != datetime(2017, 1, 1, tzinfo=UTC):
        raise RuntimeError("Night Atlas discovery start mismatch")
    if na.END_EXCLUSIVE != datetime(2023, 1, 1, tzinfo=UTC):
        raise RuntimeError("Night Atlas end wall mismatch")

    pf = na.preflight(args.manifest, args.standards)
    pf.update({
        "id": ID,
        "market_passes_planned": 1,
        "families": FAMILIES,
        "gates": GATES,
        "dependency_sha256": hashes,
        "protected_2023_plus_opened": False,
        "protected_2026_opened": False,
    })

    if not args.execute:
        print(json.dumps(pf, indent=2, sort_keys=True, allow_nan=False))
        return 0

    if args.output_dir.exists():
        raise na.AdmissionError(f"refusing overwrite: {args.output_dir}")
    args.output_dir.mkdir(parents=True)

    spec = {
        "schema": 1,
        "id": ID,
        "status": "PREREGISTERED_BEFORE_MARKET_PASS",
        "discovery_start": na.DISCOVERY_START.isoformat(),
        "development_end_exclusive": "2021-01-01T00:00:00+00:00",
        "internal_holdout_start": "2021-01-01T00:00:00+00:00",
        "discovery_end_exclusive": na.END_EXCLUSIVE.isoformat(),
        "families": FAMILIES,
        "gates": GATES,
        "standard_horizons": list(na.HORIZONS),
        "costs_directional_only": list(na.COSTS),
        "risk_definition": "1.5*WilderATR14_M5",
        "amplitude_primary_metric": "max(h_MFE_R,h_MAE_R)",
        "amplitude_secondary_metric": "abs(h_endpoint_R)",
        "min_risk_analysis": 0.01,
        "gap_outcomes_excluded_analysis": True,
        "feature_windows_must_be_contiguous": True,
        "non_overlap": "wall-clock primary horizon per family+variant after gap/risk filters",
        "multiple_testing": "Benjamini-Hochberg across all six primary signal-vs-control tests",
        "secondary_horizons": "descriptive only; cannot rescue a failed primary",
        "posthoc_parameter_tuning": "forbidden under Atlas V family IDs",
        "protected_2023_plus_opened": False,
        "protected_2026_opened": False,
        "engine_sha256": sha256_file(Path(__file__).resolve()),
        "dependency_sha256": hashes,
    }
    (args.output_dir / "preregistered_spec.json").write_text(
        json.dumps(spec, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    csvp = args.output_dir / "signals.csv"
    rows = list(na.admitted_rows(args.manifest))

    with csvp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=na.FIELDNAMES)
        writer.writeheader()
        atlas = Atlas5(na, writer)

        for i, row in enumerate(rows, 1):
            for bar in na.decode_dukascopy_m1_day(row):
                if not (na.WARMUP_START <= bar.timestamp < na.END_EXCLUSIVE):
                    raise na.AdmissionError("bar outside hard wall")
                atlas.on_m1(bar)

            if i % 100 == 0 or i == len(rows):
                print(json.dumps({
                    "status": "PROGRESS",
                    "days_done": i,
                    "days_total": len(rows),
                    "signals": atlas.base.signals,
                    "pending": len(atlas.base.pending),
                    "date": row["date"],
                    "protected_2023_plus_opened": False,
                    "protected_2026_opened": False,
                }, allow_nan=False), flush=True)

        discarded = len(atlas.base.pending)
        atlas.base.pending = []

    summary = {
        "schema": 1,
        "status": "COMPLETE",
        "id": ID,
        "signals_created": atlas.base.signals,
        "signals_written": atlas.base.written,
        "discarded_unfinished_at_end": discarded,
        "family_counts": atlas.base.family_counts,
        "signals_csv_sha256": sha256_file(csvp),
        "engine_sha256": sha256_file(Path(__file__).resolve()),
        "dependency_sha256": hashes,
        "market_passes": 1,
        "protected_2023_plus_opened": False,
        "protected_2026_opened": False,
    }
    (args.output_dir / "engine_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, allow_nan=False))


if __name__ == "__main__":
    raise SystemExit(main())
