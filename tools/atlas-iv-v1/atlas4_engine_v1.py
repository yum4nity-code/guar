#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import json
import math
import statistics
import sys
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

UTC = timezone.utc
ID = "GUARDIAN-ATLAS-IV-V1"

EXPECTED = {
    "data_loader_v1.py": "9129e1d48ce5b05ef997e9fa12b6e55a674c2be052e69ed94bc3fb4c235b2f6b",
    "preflight_v1.py": "d0e799d4a999683c4c1a46495b56a6720d40ab61058ffe2d36a1456ab69cd354",
    "night_atlas_v1.py": "edf95e275cdd49846fed81c06d180b5190e63c25de347137136b94e0f3674860",
}

FAMILIES = {
    "A4_01_BIPOWER_JUMP_REVERSAL": {
        "kind": "DIRECTIONAL",
        "primary_horizon": 30,
        "secondary_horizons": [15, 60],
        "signal": "completed M5 return >=3.0 bipower-sigma versus prior 24 M5 returns; trade opposite jump",
        "control": "same event and timestamp; trade with jump",
        "mechanism": "discontinuous-return proxy / post-jump reversal",
    },
    "A4_02_REALIZED_SKEW_REVERSAL": {
        "kind": "DIRECTIONAL",
        "primary_horizon": 60,
        "secondary_horizons": [30, 120],
        "signal": "hourly, abs standardized skew of prior 24 completed M5 returns >=1.0; trade opposite skew sign",
        "control": "same event and timestamp; trade with skew sign",
        "mechanism": "distributional asymmetry rather than streak or single shock",
    },
    "A4_03_VOL_OF_VOL": {
        "kind": "AMPLITUDE",
        "primary_horizon": 60,
        "secondary_horizons": [120],
        "signal": "hourly, coefficient of variation of four 30m realized-variance blocks >=0.75",
        "control": "same hourly sampling, coefficient of variation <=0.35",
        "mechanism": "volatility-of-volatility state",
        "primary_outcome": "max(h_MFE_R,h_MAE_R), non-directional max excursion",
    },
    "A4_04_REALIZED_KURTOSIS": {
        "kind": "AMPLITUDE",
        "primary_horizon": 60,
        "secondary_horizons": [120],
        "signal": "hourly, excess kurtosis of prior 24 completed M5 returns >=3.0",
        "control": "same hourly sampling, abs excess kurtosis <=0.5",
        "mechanism": "tail-shape state independent of direction",
        "primary_outcome": "max(h_MFE_R,h_MAE_R), non-directional max excursion",
    },
    "A4_05_CLOSE_LOCATION_PRESSURE": {
        "kind": "DIRECTIONAL",
        "primary_horizon": 30,
        "secondary_horizons": [15, 60],
        "signal": "hourly, abs mean close-location value of prior 12 completed M5 bars >=0.35; follow sign",
        "control": "same event and timestamp; trade opposite close-location pressure",
        "mechanism": "persistent closes near bar extremes as pressure proxy",
    },
    "A4_06_INTRADAY_SEASONALITY": {
        "kind": "DIRECTIONAL",
        "primary_horizon": 60,
        "secondary_horizons": [30, 120],
        "signal": "hourly, follow sign of rolling mean of last 60 prior same-UTC-hour normalized returns; minimum 40 observations",
        "control": "same event and timestamp; trade opposite historical same-hour sign",
        "mechanism": "causal recurring intraday return seasonality",
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


class Atlas4:
    def __init__(self, na, writer):
        self.na = na
        self.base = na.Atlas(writer)
        self.same_hour = {h: deque(maxlen=60) for h in range(24)}
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
        for a, b in zip(bars, bars[1:]):
            if a.available_at is None or b.available_at is None:
                return False
            if (b.available_at - a.available_at).total_seconds() != 300:
                return False
        return True

    @staticmethod
    def returns_from_bars(bars):
        return [bars[i].close - bars[i - 1].close for i in range(1, len(bars))]

    def jump_reversal(self, t, entry, hist, atr):
        bars = hist[-27:]
        if len(bars) != 27 or not self.contiguous(bars):
            return
        r0 = bars[-1].close - bars[-2].close
        if r0 == 0:
            return
        base_bars = bars[:-1]
        rs = self.returns_from_bars(base_bars)
        if len(rs) != 24:
            return
        products = [abs(rs[i]) * abs(rs[i - 1]) for i in range(1, len(rs))]
        if not products:
            return
        bv = (math.pi / 2.0) * (sum(products) / len(products))
        if bv <= 0:
            return
        sigma = math.sqrt(bv)
        z = abs(r0) / sigma
        if z >= 3.0:
            d = sgn(r0)
            self.add(t, entry, -d, "A4_01_BIPOWER_JUMP_REVERSAL", "SIGNAL", strength=z, aux=abs(r0) / atr)
            self.add(t, entry, d, "A4_01_BIPOWER_JUMP_REVERSAL", "CONTROL_CONT", strength=z, aux=abs(r0) / atr)

    def realized_skew(self, t, entry, hist, atr):
        if t.minute != 0:
            return
        bars = hist[-25:]
        if len(bars) != 25 or not self.contiguous(bars):
            return
        rs = self.returns_from_bars(bars)
        if len(rs) != 24:
            return
        mu = sum(rs) / len(rs)
        var = sum((r - mu) ** 2 for r in rs) / len(rs)
        if var <= 0:
            return
        sd = math.sqrt(var)
        skew = sum(((r - mu) / sd) ** 3 for r in rs) / len(rs)
        if abs(skew) >= 1.0:
            d = sgn(skew)
            self.add(t, entry, -d, "A4_02_REALIZED_SKEW_REVERSAL", "SIGNAL", strength=abs(skew), aux=sd / atr)
            self.add(t, entry, d, "A4_02_REALIZED_SKEW_REVERSAL", "CONTROL_CONT", strength=abs(skew), aux=sd / atr)

    def amplitude_states(self, t, entry, hist, atr):
        if t.minute != 0:
            return
        bars = hist[-25:]
        if len(bars) != 25 or not self.contiguous(bars):
            return
        rs = self.returns_from_bars(bars)
        if len(rs) != 24:
            return

        rv = [sum(r * r for r in rs[i:i + 6]) for i in (0, 6, 12, 18)]
        mean_rv = sum(rv) / 4.0
        if mean_rv > 0:
            cv = statistics.pstdev(rv) / mean_rv
            total_rv_atr = sum(rv) / (atr * atr) if atr > 0 else None
            if cv >= 0.75:
                self.add(t, entry, 1, "A4_03_VOL_OF_VOL", "SIGNAL", strength=cv, aux=total_rv_atr)
            elif cv <= 0.35:
                self.add(t, entry, 1, "A4_03_VOL_OF_VOL", "CONTROL_LOW_VOV", strength=cv, aux=total_rv_atr)

        mu = sum(rs) / len(rs)
        var = sum((r - mu) ** 2 for r in rs) / len(rs)
        if var > 0:
            sd = math.sqrt(var)
            excess = sum(((r - mu) / sd) ** 4 for r in rs) / len(rs) - 3.0
            aux = sd / atr if atr > 0 else None
            if excess >= 3.0:
                self.add(t, entry, 1, "A4_04_REALIZED_KURTOSIS", "SIGNAL", strength=excess, aux=aux)
            elif abs(excess) <= 0.5:
                self.add(t, entry, 1, "A4_04_REALIZED_KURTOSIS", "CONTROL_NORMAL_KURT", strength=abs(excess), aux=aux)

    def close_location_pressure(self, t, entry, hist, atr):
        if t.minute != 0:
            return
        bars = hist[-12:]
        if len(bars) != 12 or not self.contiguous(bars):
            return
        clv = []
        for b in bars:
            rng = b.high - b.low
            if rng > 0:
                clv.append((2.0 * b.close - b.high - b.low) / rng)
        if len(clv) < 10:
            return
        m = sum(clv) / len(clv)
        if abs(m) >= 0.35:
            d = sgn(m)
            self.add(t, entry, d, "A4_05_CLOSE_LOCATION_PRESSURE", "SIGNAL", strength=abs(m), aux=(bars[-1].close - bars[0].open) / atr)
            self.add(t, entry, -d, "A4_05_CLOSE_LOCATION_PRESSURE", "CONTROL_OPPOSITE", strength=abs(m), aux=(bars[-1].close - bars[0].open) / atr)

    def seasonality(self, t, entry, hist, atr):
        if t.minute != 0:
            return

        bucket = self.same_hour[t.hour]
        if len(bucket) >= 40:
            m = sum(bucket) / len(bucket)
            if m != 0:
                d = sgn(m)
                dispersion = statistics.pstdev(bucket) if len(bucket) > 1 else 0.0
                self.add(t, entry, d, "A4_06_INTRADAY_SEASONALITY", "SIGNAL", strength=abs(m), aux=dispersion)
                self.add(t, entry, -d, "A4_06_INTRADAY_SEASONALITY", "CONTROL_OPPOSITE", strength=abs(m), aux=dispersion)

        bars = hist[-12:]
        if len(bars) != 12 or not self.contiguous(bars) or atr <= 0:
            return
        completed_hour = (t.hour - 1) % 24
        normalized_return = (bars[-1].close - bars[0].open) / atr
        self.same_hour[completed_hour].append(normalized_return)

    def detect(self, t, entry):
        hist = list(self.base.m5_hist)
        if len(hist) < 193:
            return
        if any(x is None for x in (self.base.atr5.value, self.base.rsi1.value, self.base.rsi5.value, self.base.rsi15.value)):
            return
        atr = float(self.base.atr5.value)
        if atr <= 0:
            return
        if self.just_gap:
            return
        for ts in (hist[-1].available_at, self.base.rsi1.available_at, self.base.rsi5.available_at, self.base.rsi15.available_at, self.base.atr5.available_at):
            if ts is not None and ts > t:
                raise self.na.AdmissionError("lookahead")

        self.jump_reversal(t, entry, hist, atr)
        self.realized_skew(t, entry, hist, atr)
        self.amplitude_states(t, entry, hist, atr)
        self.close_location_pressure(t, entry, hist, atr)
        self.seasonality(t, entry, hist, atr)

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
                    p.row["outcome_max_gap_minutes"] = max(int(p.row.get("outcome_max_gap_minutes") or 0), gap_minutes - 1)

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
        atlas = Atlas4(na, writer)

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
