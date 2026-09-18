#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

UTC = timezone.utc
ID = "GUARDIAN-ATLAS-III-V1"

EXPECTED = {
    "data_loader_v1.py": "9129e1d48ce5b05ef997e9fa12b6e55a674c2be052e69ed94bc3fb4c235b2f6b",
    "preflight_v1.py": "d0e799d4a999683c4c1a46495b56a6720d40ab61058ffe2d36a1456ab69cd354",
    "night_atlas_v1.py": "edf95e275cdd49846fed81c06d180b5190e63c25de347137136b94e0f3674860",
}

FAMILIES = {
    "A3_01_SEMIVARIANCE_IMBALANCE": {
        "source": "Codex-F20",
        "kind": "AMPLITUDE",
        "primary_horizon": 60,
        "secondary_horizons": [120],
        "signal": "12 completed M5 returns; downside semivariance > upside semivariance",
        "control": "same hourly sampling; upside semivariance > downside semivariance",
        "primary_outcome": "max(h_MFE_R,h_MAE_R), non-directional max absolute excursion from entry",
    },
    "A3_02_VOLATILITY_FRONT": {
        "source": "Codex-F24",
        "kind": "AMPLITUDE",
        "primary_horizon": 30,
        "secondary_horizons": [60],
        "signal": "last 6 M5 squared returns > first 6 within the prior hour",
        "control": "same hourly sampling; last-half variance < first-half variance",
        "primary_outcome": "max(h_MFE_R,h_MAE_R), non-directional max absolute excursion from entry",
    },
    "A3_03_LOW_OCCUPANCY_ZONE": {
        "source": "Codex-F28",
        "kind": "DIRECTIONAL",
        "primary_horizon": 15,
        "secondary_horizons": [30],
        "signal": "first adjacent-cell entry during hour into interior cell with zero closes in frozen prior-48-M5 range",
        "control": "first adjacent-cell entry during hour into interior cell with exactly one close in frozen prior-48-M5 range",
    },
    "A3_04_FVG_GEOMETRY": {
        "source": "Claude-F28",
        "kind": "DIRECTIONAL",
        "primary_horizon": 15,
        "secondary_horizons": [30, 60],
        "signal": "three-bar non-overlap geometry; continue gap direction",
        "control": "same event and timestamp; trade opposite gap direction",
    },
    "A3_05_MIDPOINT_RANGE_GRAVITY": {
        "source": "V3-F28",
        "kind": "DIRECTIONAL",
        "primary_horizon": 30,
        "secondary_horizons": [15, 60, 120],
        "signal": "cross frozen prior-48-M5 midpoint and close at least 0.20 ATR beyond; continue crossing direction",
        "control": "same geometry at 25% or 75% interior range level, excluding bars that trigger midpoint signal",
    },
    "A3_06_MEAN_REVERT_STRETCH": {
        "source": "Claude-F18",
        "kind": "DIRECTIONAL",
        "primary_horizon": 30,
        "secondary_horizons": [15, 60],
        "signal": "abs((M5 close - causal EMA20 M5)/ATR14 M5) >= 2; trade toward EMA",
        "control": "same event and timestamp; trade away from EMA",
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


def sign(x):
    return 1 if x > 0 else -1 if x < 0 else 0


class Atlas3:
    def __init__(self, na, writer):
        self.na = na
        self.base = na.Atlas(writer)
        self.ema20 = None
        self.ema_alpha = 2.0 / 21.0
        self.low_occ = None
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
    def _cell_of(price, lo, hi):
        if hi <= lo or price < lo or price > hi:
            return None
        w = (hi - lo) / 8.0
        if w <= 0:
            return None
        if price == hi:
            return 7
        idx = int((price - lo) / w)
        return max(0, min(7, idx))

    def _freeze_low_occ(self, t, hist, atr):
        prior = hist[-48:]
        lo = min(b.low for b in prior)
        hi = max(b.high for b in prior)
        if hi <= lo:
            self.low_occ = None
            return
        counts = [0] * 8
        for b in prior:
            idx = self._cell_of(b.close, lo, hi)
            if idx is not None:
                counts[idx] += 1
        self.low_occ = {
            "start": t,
            "lo": lo,
            "hi": hi,
            "counts": counts,
            "last_cell": self._cell_of(prior[-1].close, lo, hi),
            "signal_done": False,
            "control_done": False,
            "range_atr": (hi - lo) / atr if atr > 0 else None,
        }

    def _low_occ_logic(self, t, entry, hist, atr):
        if t.minute == 0:
            self._freeze_low_occ(t, hist, atr)
            return
        st = self.low_occ
        if not st or t - st["start"] >= self.na.timedelta(hours=1):
            return
        cur = self._cell_of(hist[-1].close, st["lo"], st["hi"])
        prev = st["last_cell"]
        if cur is not None and prev is not None and abs(cur - prev) == 1 and 1 <= cur <= 6:
            d = sign(cur - prev)
            occ = st["counts"][cur]
            if occ == 0 and not st["signal_done"]:
                self.add(t, entry, d, "A3_03_LOW_OCCUPANCY_ZONE", "SIGNAL", strength=st["range_atr"], aux=0.0)
                st["signal_done"] = True
            elif occ == 1 and not st["control_done"]:
                self.add(t, entry, d, "A3_03_LOW_OCCUPANCY_ZONE", "CONTROL_OCC1", strength=st["range_atr"], aux=1.0)
                st["control_done"] = True
        if cur is not None:
            st["last_cell"] = cur

    def _hourly_amplitude_families(self, t, entry, hist, atr):
        if t.minute != 0 or len(hist) < 13:
            return
        closes = [b.close for b in hist]
        rs = [closes[i] - closes[i - 1] for i in range(len(closes) - 12, len(closes))]
        if len(rs) != 12:
            return

        vplus = sum(r * r for r in rs if r > 0)
        vminus = sum(r * r for r in rs if r < 0)
        total = vplus + vminus
        if total > 0:
            s = (vminus - vplus) / total
            if s != 0:
                variant = "SIGNAL" if s > 0 else "CONTROL_UPSIDE_DOM"
                self.add(
                    t,
                    entry,
                    1,
                    "A3_01_SEMIVARIANCE_IMBALANCE",
                    variant,
                    strength=abs(s),
                    aux=total / (atr * atr) if atr > 0 else None,
                )

        v1 = sum(r * r for r in rs[:6])
        v2 = sum(r * r for r in rs[6:])
        denom = v1 + v2
        if denom > 0:
            q = (v2 - v1) / denom
            if q != 0:
                variant = "SIGNAL" if q > 0 else "CONTROL_FRONTLOADED"
                self.add(
                    t,
                    entry,
                    1,
                    "A3_02_VOLATILITY_FRONT",
                    variant,
                    strength=abs(q),
                    aux=denom / (atr * atr) if atr > 0 else None,
                )

    def _fvg(self, t, entry, hist, atr):
        a = hist[-3]
        c = hist[-1]
        if c.low > a.high:
            gap = c.low - a.high
            self.add(t, entry, 1, "A3_04_FVG_GEOMETRY", "SIGNAL", strength=gap / atr, aux=(c.close - a.close) / atr)
            self.add(t, entry, -1, "A3_04_FVG_GEOMETRY", "CONTROL_OPPOSITE", strength=gap / atr, aux=(c.close - a.close) / atr)
        elif c.high < a.low:
            gap = a.low - c.high
            self.add(t, entry, -1, "A3_04_FVG_GEOMETRY", "SIGNAL", strength=gap / atr, aux=(a.close - c.close) / atr)
            self.add(t, entry, 1, "A3_04_FVG_GEOMETRY", "CONTROL_OPPOSITE", strength=gap / atr, aux=(a.close - c.close) / atr)

    def _midpoint(self, t, entry, hist, atr):
        prior = hist[-49:-1]
        if len(prior) != 48:
            return
        lo = min(b.low for b in prior)
        hi = max(b.high for b in prior)
        width = hi - lo
        if width <= 0:
            return
        prev_close = hist[-2].close
        cur_close = hist[-1].close
        mid = lo + 0.5 * width
        d = sign(cur_close - mid)
        crossed_mid = (prev_close - mid) * (cur_close - mid) < 0
        midpoint_signal = crossed_mid and d != 0 and abs(cur_close - mid) >= 0.20 * atr
        if midpoint_signal:
            self.add(
                t,
                entry,
                d,
                "A3_05_MIDPOINT_RANGE_GRAVITY",
                "SIGNAL",
                strength=abs(cur_close - mid) / atr,
                aux=width / atr,
            )
            return

        candidates = []
        for frac in (0.25, 0.75):
            level = lo + frac * width
            dd = sign(cur_close - level)
            crossed = (prev_close - level) * (cur_close - level) < 0
            if crossed and dd != 0 and abs(cur_close - level) >= 0.20 * atr:
                candidates.append((abs(cur_close - level), level, dd))
        if candidates:
            _, level, dd = min(candidates, key=lambda x: x[0])
            self.add(
                t,
                entry,
                dd,
                "A3_05_MIDPOINT_RANGE_GRAVITY",
                "CONTROL_QUARTER",
                strength=abs(cur_close - level) / atr,
                aux=width / atr,
            )

    def _stretch(self, t, entry, hist, atr):
        if self.ema20 is None:
            return
        stretch = (hist[-1].close - self.ema20) / atr
        if abs(stretch) >= 2.0:
            d = sign(stretch)
            self.add(t, entry, -d, "A3_06_MEAN_REVERT_STRETCH", "SIGNAL", strength=abs(stretch), aux=None)
            self.add(t, entry, d, "A3_06_MEAN_REVERT_STRETCH", "CONTROL_CONT", strength=abs(stretch), aux=None)

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
            if t.minute == 0:
                self._freeze_low_occ(t, hist, atr)
            return
        for ts in (hist[-1].available_at, self.base.rsi1.available_at, self.base.rsi5.available_at, self.base.rsi15.available_at, self.base.atr5.available_at):
            if ts is not None and ts > t:
                raise self.na.AdmissionError("lookahead")

        self._hourly_amplitude_families(t, entry, hist, atr)
        self._low_occ_logic(t, entry, hist, atr)
        self._fvg(t, entry, hist, atr)
        self._midpoint(t, entry, hist, atr)
        self._stretch(t, entry, hist, atr)

    def after_m5(self, b):
        self.base.after_m5(b)
        if self.ema20 is None:
            self.ema20 = b.close
        else:
            self.ema20 = self.ema_alpha * b.close + (1.0 - self.ema_alpha) * self.ema20

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
            self.after_m5(b5)

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
        atlas = Atlas3(na, writer)

        for i, row in enumerate(rows, 1):
            for bar in na.decode_dukascopy_m1_day(row):
                if not (na.WARMUP_START <= bar.timestamp < na.END_EXCLUSIVE):
                    raise na.AdmissionError("bar outside hard wall")
                atlas.on_m1(bar)

            if i % 100 == 0 or i == len(rows):
                print(
                    json.dumps({
                        "status": "PROGRESS",
                        "days_done": i,
                        "days_total": len(rows),
                        "signals": atlas.base.signals,
                        "pending": len(atlas.base.pending),
                        "date": row["date"],
                        "protected_2023_plus_opened": False,
                        "protected_2026_opened": False,
                    }, allow_nan=False),
                    flush=True,
                )

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
