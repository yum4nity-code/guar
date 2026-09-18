#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import json
import math
import sys
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

UTC = timezone.utc
ID = "GUARDIAN-ATLAS-II-V1"
EXPECTED = {
    "data_loader_v1.py": "9129e1d48ce5b05ef997e9fa12b6e55a674c2be052e69ed94bc3fb4c235b2f6b",
    "preflight_v1.py": "d0e799d4a999683c4c1a46495b56a6720d40ab61058ffe2d36a1456ab69cd354",
    "night_atlas_v1.py": "edf95e275cdd49846fed81c06d180b5190e63c25de347137136b94e0f3674860",
}
FAMILIES = {
    "A2_01_ORDER_INTRABAR": {"source": "Codex-F18", "primary_horizon": 5, "secondary_horizons": [15], "signal": "unique M1 low before unique M1 high => LONG, inverse => SHORT", "control": "same event time, completed M5 candle-body direction"},
    "A2_02_PATH_EFFICIENCY": {"source": "Codex-F22", "primary_horizon": 60, "secondary_horizons": [120], "signal": "12-M5 path efficiency E>=0.5, continue net direction", "control": "same hourly sampling with E<0.5, continue net direction"},
    "A2_03_SCALE_CONFLICT": {"source": "Codex-F21", "primary_horizon": 60, "secondary_horizons": [120], "signal": "105-minute slow move and last 15-minute move oppose; continue slow move", "control": "same M15 sampling when slow and fast moves align; evaluate slow direction"},
    "A2_04_MAJORITY_AMPLITUDE_CONFLICT": {"source": "Codex-F25", "primary_horizon": 30, "secondary_horizons": [60], "signal": "12-M5 sign majority opposes net displacement; follow sign majority", "control": "12-M5 majority aligns with net; evaluate mean-reversion direction -sign(net)"},
    "A2_05_FROZEN_CENTER_RETURN": {"source": "Codex-F26", "primary_horizon": 30, "secondary_horizons": [60], "signal": "first crossing of frozen 4h center after >=1 ATR excursion; continue through center", "control": "first center crossing in block before any >=1 ATR excursion"},
    "A2_06_ROUND5_REJECTION": {"source": "Claude-F20", "primary_horizon": 15, "secondary_horizons": [5, 30], "signal": "first entry into <=0.15 ATR zone around true multiple of 5; direction away from level", "control": "same rule around placebo levels offset by +2.5"},
}
GATES = {
    "cheap_fail": {"full_n_min": 100, "full_cost_010_mean_r_gt": 0.0, "control_delta_gt": 0.0, "development_2017_2020_mean_r_gt": 0.0, "internal_holdout_2021_2022_mean_r_gt": 0.0},
    "freeze_candidate_strict": {"full_n_min": 200, "holdout_n_min": 60, "full_cost_010_pf_min": 1.10, "full_cost_010_mean_r_gt": 0.0, "full_cost_020_pf_min": 1.05, "full_cost_020_mean_r_gt": 0.0, "positive_years_min": 4, "all_leave_one_year_out_mean_r_gt": 0.0, "daily_bootstrap_signal_ci95_lower_gt": 0.0, "mean_r_without_top1pct_winners_gt": 0.0, "holdout_cost_010_pf_min": 1.05, "holdout_cost_010_mean_r_gt": 0.0, "holdout_cost_020_mean_r_gt": 0.0, "control_common_days_min": 30, "matched_control_support_min": 30, "matched_control_delta_gt": 0.0, "control_delta_mean_r_gt": 0.0, "control_delta_ci95_lower_gt": 0.0, "holdout_control_delta_gt": 0.0, "all_leave_one_year_out_control_delta_gt": 0.0, "bh_q_max": 0.10},
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

class Atlas2:
    def __init__(self, na, writer):
        self.na = na
        self.base = na.Atlas(writer)
        self.m1_recent = deque(maxlen=5)
        self.last_m5_meta = None
        self.center = None
        self.just_gap = False

    def __getattr__(self, name):
        return getattr(self.base, name)

    def snapshot(self, *args, **kwargs):
        row = self.base.snapshot(*args, **kwargs)
        row["job_id"] = ID
        return row

    def add(self, t, entry, side, family, variant, strength=None, aux=None, comp_ratio=None, comp_pct=None):
        na = self.na
        if not (na.DISCOVERY_START <= t < na.END_EXCLUSIVE - na.timedelta(minutes=na.MAX_HORIZON_MIN)):
            return
        row = self.snapshot(t, entry, side, family, variant, strength, aux, comp_ratio, comp_pct)
        self.base.pending.append(na.Pending(row, side, entry, float(row["risk"])))
        self.base.signals += 1
        key = f"{family}|{variant}"
        self.base.family_counts[key] = self.base.family_counts.get(key, 0) + 1

    def _intrabar_meta(self, boundary):
        xs = list(self.m1_recent)
        if len(xs) != 5:
            return None
        for i in range(1, 5):
            if xs[i].timestamp - xs[i - 1].timestamp != self.na.timedelta(minutes=1):
                return None
        if xs[-1].available_at != boundary:
            return None
        hi = max(b.high for b in xs)
        lo = min(b.low for b in xs)
        his = [i for i, b in enumerate(xs) if b.high == hi]
        los = [i for i, b in enumerate(xs) if b.low == lo]
        if len(his) != 1 or len(los) != 1 or his[0] == los[0]:
            return None
        return {"j_high": his[0] + 1, "j_low": los[0] + 1}

    def _center_logic(self, t, entry, hist, atr):
        if t.minute == 0 and t.hour % 4 == 0:
            if len(hist) >= 48:
                closes = [b.close for b in hist[-48:]]
                center = sum(closes) / len(closes)
                self.center = {"start": t, "m": center, "atr0": atr, "excursion_side": 0, "excursion_time": None, "max_abs_z": 0.0, "signal_done": False, "control_done": False, "last_side": sign(closes[-1] - center), "last_close": closes[-1]}
            else:
                self.center = None
            return
        c = self.center
        if not c:
            return
        if t - c["start"] >= self.na.timedelta(hours=4):
            self.center = None
            return
        sb = hist[-1]
        z = sb.close - c["m"]
        cur_side = sign(z)
        prev_side = c["last_side"]
        c["max_abs_z"] = max(c["max_abs_z"], abs(z))
        if c["excursion_side"] == 0 and abs(z) >= c["atr0"]:
            c["excursion_side"] = cur_side
            c["excursion_time"] = t
        crossed = prev_side != 0 and cur_side != 0 and prev_side != cur_side
        approach = abs(sb.close - c["last_close"]) / atr if atr > 0 else None
        if c["excursion_side"] != 0 and not c["signal_done"]:
            s = c["excursion_side"]
            if s * z <= 0:
                duration = (t - c["excursion_time"]).total_seconds() / 60.0 if c["excursion_time"] is not None else None
                self.add(t, entry, -s, "A2_05_FROZEN_CENTER_RETURN", "SIGNAL", strength=c["max_abs_z"] / c["atr0"] if c["atr0"] > 0 else None, aux=duration)
                c["signal_done"] = True
        elif c["excursion_side"] == 0 and crossed and not c["control_done"]:
            self.add(t, entry, cur_side, "A2_05_FROZEN_CENTER_RETURN", "CONTROL_NO_EXCURSION", strength=abs(z) / atr if atr > 0 else None, aux=approach)
            c["control_done"] = True
        if cur_side != 0:
            c["last_side"] = cur_side
        c["last_close"] = sb.close

    def detect(self, t, entry):
        hist = list(self.base.m5_hist)
        if len(hist) < 193:
            return
        if any(x is None for x in (self.base.atr5.value, self.base.rsi1.value, self.base.rsi5.value, self.base.rsi15.value)):
            return
        sb = hist[-1]
        atr = float(self.base.atr5.value)
        if self.just_gap:
            if t.minute == 0 and t.hour % 4 == 0:
                self._center_logic(t, entry, hist, atr)
            return
        for ts in (sb.available_at, self.base.rsi1.available_at, self.base.rsi5.available_at, self.base.rsi15.available_at, self.base.atr5.available_at):
            if ts is not None and ts > t:
                raise self.na.AdmissionError("lookahead")

        meta = self.last_m5_meta
        if meta is not None:
            jh, jl = meta["j_high"], meta["j_low"]
            d = 1 if jl < jh else -1
            strength = (jh - jl) / 4.0
            self.add(t, entry, d, "A2_01_ORDER_INTRABAR", "SIGNAL", strength=strength, aux=self.na.safe_div(sb.close - sb.low, sb.high - sb.low))
            body_d = sign(sb.close - sb.open)
            if body_d:
                self.add(t, entry, body_d, "A2_01_ORDER_INTRABAR", "CONTROL_BODY", strength=strength, aux=self.na.safe_div(sb.close - sb.low, sb.high - sb.low))

        closes = [b.close for b in hist]
        if t.minute == 0 and len(closes) >= 13:
            rs = [closes[i] - closes[i - 1] for i in range(len(closes) - 12, len(closes))]
            net = sum(rs)
            path = sum(abs(x) for x in rs)
            d = sign(net)
            if d and path > 0:
                e = abs(net) / path
                variant = "SIGNAL" if e >= 0.5 else "CONTROL_LOW_EFF"
                self.add(t, entry, d, "A2_02_PATH_EFFICIENCY", variant, strength=e, aux=abs(net) / atr)

        if t.minute % 15 == 0 and len(closes) >= 25:
            bslow = closes[-4] - closes[-25]
            fast = closes[-1] - closes[-4]
            d = sign(bslow)
            if d and fast != 0:
                variant = "SIGNAL" if bslow * fast < 0 else "CONTROL_ALIGNED"
                self.add(t, entry, d, "A2_03_SCALE_CONFLICT", variant, strength=abs(fast) / atr, aux=abs(bslow) / atr)

        if t.minute == 0 and len(closes) >= 13:
            rs = [closes[i] - closes[i - 1] for i in range(len(closes) - 12, len(closes))]
            nvote = sum(sign(x) for x in rs)
            net = sum(rs)
            if nvote and net:
                if nvote * net < 0:
                    d, variant = sign(nvote), "SIGNAL"
                else:
                    d, variant = -sign(net), "CONTROL_ALIGNED"
                self.add(t, entry, d, "A2_04_MAJORITY_AMPLITUDE_CONFLICT", variant, strength=abs(nvote) / 12.0, aux=abs(net) / atr)

        self._center_logic(t, entry, hist, atr)

        if len(hist) >= 2:
            prev_close = hist[-2].close
            true_level = round(sb.close / 5.0) * 5.0
            placebo_level = round((sb.close - 2.5) / 5.0) * 5.0 + 2.5
            zone = 0.15 * atr
            true_now = abs(sb.close - true_level) <= zone and sb.close != true_level
            true_prev = abs(prev_close - true_level) <= zone
            plac_now = abs(sb.close - placebo_level) <= zone and sb.close != placebo_level
            plac_prev = abs(prev_close - placebo_level) <= zone
            sig = true_now and not true_prev
            ctl = plac_now and not plac_prev
            if not (sig and ctl):
                if sig:
                    d = sign(sb.close - true_level)
                    if d:
                        self.add(t, entry, d, "A2_06_ROUND5_REJECTION", "SIGNAL", strength=abs(sb.close - true_level) / atr, aux=true_level)
                elif ctl:
                    d = sign(sb.close - placebo_level)
                    if d:
                        self.add(t, entry, d, "A2_06_ROUND5_REJECTION", "CONTROL_PLACEBO", strength=abs(sb.close - placebo_level) / atr, aux=placebo_level)

    def on_m1(self, bar):
        prev = self.base.prev_m1_timestamp
        self.just_gap = False
        if prev is not None:
            gap_minutes = int((bar.timestamp - prev).total_seconds() // 60)
            if gap_minutes > 1:
                self.m1_recent.clear()
                self.just_gap = True
        self.last_m5_meta = self._intrabar_meta(bar.timestamp) if bar.timestamp.minute % 5 == 0 else None
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
        self.m1_recent.append(bar)

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
    pf.update({"id": ID, "market_passes_planned": 1, "families": FAMILIES, "gates": GATES, "dependency_sha256": hashes, "protected_2023_plus_opened": False, "protected_2026_opened": False})
    if not args.execute:
        print(json.dumps(pf, indent=2, sort_keys=True, allow_nan=False))
        return 0
    if args.output_dir.exists():
        raise na.AdmissionError(f"refusing overwrite: {args.output_dir}")
    args.output_dir.mkdir(parents=True)
    spec = {"schema": 1, "id": ID, "status": "PREREGISTERED_BEFORE_MARKET_PASS", "discovery_start": na.DISCOVERY_START.isoformat(), "development_end_exclusive": "2021-01-01T00:00:00+00:00", "internal_holdout_start": "2021-01-01T00:00:00+00:00", "discovery_end_exclusive": na.END_EXCLUSIVE.isoformat(), "families": FAMILIES, "gates": GATES, "standard_horizons": list(na.HORIZONS), "costs": list(na.COSTS), "risk_definition": "1.5*WilderATR14_M5", "min_risk_analysis": 0.01, "gap_outcomes_excluded_analysis": True, "non_overlap": "wall-clock primary horizon per family+variant after gap/risk filters", "multiple_testing": "Benjamini-Hochberg across six primary signal-vs-control tests", "protected_2023_plus_opened": False, "protected_2026_opened": False, "engine_sha256": sha256_file(Path(__file__).resolve()), "dependency_sha256": hashes}
    (args.output_dir / "preregistered_spec.json").write_text(json.dumps(spec, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    csvp = args.output_dir / "signals.csv"
    rows = list(na.admitted_rows(args.manifest))
    with csvp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=na.FIELDNAMES)
        writer.writeheader()
        atlas = Atlas2(na, writer)
        for i, row in enumerate(rows, 1):
            for bar in na.decode_dukascopy_m1_day(row):
                if not (na.WARMUP_START <= bar.timestamp < na.END_EXCLUSIVE):
                    raise na.AdmissionError("bar outside hard wall")
                atlas.on_m1(bar)
            if i % 100 == 0 or i == len(rows):
                print(json.dumps({"status": "PROGRESS", "days_done": i, "days_total": len(rows), "signals": atlas.base.signals, "pending": len(atlas.base.pending), "date": row["date"], "protected_2023_plus_opened": False, "protected_2026_opened": False}, allow_nan=False), flush=True)
        discarded = len(atlas.base.pending)
        atlas.base.pending = []
    summary = {"schema": 1, "status": "COMPLETE", "id": ID, "signals_created": atlas.base.signals, "signals_written": atlas.base.written, "discarded_unfinished_at_end": discarded, "family_counts": atlas.base.family_counts, "signals_csv_sha256": sha256_file(csvp), "engine_sha256": sha256_file(Path(__file__).resolve()), "dependency_sha256": hashes, "market_passes": 1, "protected_2023_plus_opened": False, "protected_2026_opened": False}
    (args.output_dir / "engine_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, allow_nan=False))

if __name__ == "__main__":
    raise SystemExit(main())
