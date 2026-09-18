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
from datetime import datetime, timezone
from pathlib import Path

UTC = timezone.utc
ID = "VFWA-DEV-V1"
DEV_START = datetime(2017, 1, 1, tzinfo=UTC)
DEV_END_EXCLUSIVE = datetime(2021, 1, 1, tzinfo=UTC)
PRIMARY_HORIZON = 30

EXPECTED = {
    "data_loader_v1.py": "9129e1d48ce5b05ef997e9fa12b6e55a674c2be052e69ed94bc3fb4c235b2f6b",
    "preflight_v1.py": "d0e799d4a999683c4c1a46495b56a6720d40ab61058ffe2d36a1456ab69cd354",
    "night_atlas_v1.py": "edf95e275cdd49846fed81c06d180b5190e63c25de347137136b94e0f3674860",
}

SPEC = {
    "id": ID,
    "strategy": "VOLATILITY_FRONT_WICK_ABSORPTION",
    "scope": "DEV_ONLY",
    "warmup_start": "2016-11-01T00:00:00+00:00",
    "dev_start": DEV_START.isoformat(),
    "dev_end_exclusive": DEV_END_EXCLUSIVE.isoformat(),
    "feature": {
        "volatility_front": "RV_recent6 / RV_prior6, where each RV is sum of squared completed M5 close-to-close returns",
        "signal_threshold": "> 1.50",
        "control_threshold": "<= 1.00",
        "grey_zone": "(1.00, 1.50] ignored",
        "signal_bar_range": ">= 1.20 * causal Wilder ATR14 M5",
        "wick_fraction": "exactly one of upper/lower wick >= 0.45 of signal-bar range",
        "ambiguous_both_wicks": "skip",
    },
    "entry": "first M1 open immediately after the completed M5 signal bar (causal executable proxy for entry-at-close)",
    "direction": "opposite qualifying wick",
    "risk": "1.5 * causal Wilder ATR14 M5 at signal time = 1R",
    "exit": "first touch +1R TP or -1R SL; otherwise time-stop at 30 admitted market minutes",
    "same_m1_tp_sl_ambiguity": "score as -1R (conservative worst case)",
    "control": "same range/wick anatomy and direction rule, but volatility-front ratio <= 1.00",
    "non_overlap": "30 wall-clock minutes, separately for SIGNAL and CONTROL in analysis",
    "kill_gate_dev_only": {
        "signal_gross_mean_r_min": 0.15,
        "signal_minus_control_mean_daily_r_min": 0.08,
        "logic": "PASS only if BOTH thresholds are met; no threshold tuning after result",
    },
    "synthetic_costs_reported": [0.00, 0.10, 0.20],
    "protected_2021_2022_opened": False,
    "protected_2023_plus_opened": False,
    "protected_2026_opened": False,
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
            raise RuntimeError(f"missing audited dependency: {p}")
        actual = sha256_file(p)
        got[name] = actual
        if actual != expected:
            raise RuntimeError(f"hash mismatch {name}: expected {expected}, got {actual}")
    return got


class VFWAAtlas:
    def __init__(self, na, writer):
        self.na = na
        self.base = na.Atlas(writer)
        self.writer = writer

    def snapshot(self, t, entry, side, family, variant, strength, aux):
        row = self.base.snapshot(t, entry, side, family, variant, strength, aux, None, None)
        row["job_id"] = ID
        row["protected_2023_plus_opened"] = False
        row["protected_2026_opened"] = False
        return row

    @staticmethod
    def _rv_ratio(hist):
        closes = [b.close for b in hist]
        if len(closes) < 13:
            return None
        rs = [closes[i] - closes[i - 1] for i in range(len(closes) - 12, len(closes))]
        if len(rs) != 12:
            return None
        prior = sum(r * r for r in rs[:6])
        recent = sum(r * r for r in rs[6:])
        if prior <= 0:
            return None
        return recent / prior

    def add(self, t, entry, side, variant, ratio, wick_frac):
        na = self.na
        if not (DEV_START <= t < DEV_END_EXCLUSIVE - na.timedelta(minutes=PRIMARY_HORIZON)):
            return
        row = self.snapshot(t, entry, side, "VFWA", variant, ratio, wick_frac)
        self.base.pending.append(na.Pending(row, side, entry, float(row["risk"])))
        self.base.signals += 1
        key = f"VFWA|{variant}"
        self.base.family_counts[key] = self.base.family_counts.get(key, 0) + 1

    def detect(self, t, entry):
        hist = list(self.base.m5_hist)
        if len(hist) < 193 or self.base.atr5.value is None or self.base.atr5.value <= 0:
            return
        sb = hist[-1]
        atr = float(self.base.atr5.value)
        if sb.available_at is not None and sb.available_at > t:
            raise self.na.AdmissionError("lookahead: signal M5 unavailable")
        if self.base.atr5.available_at is not None and self.base.atr5.available_at > t:
            raise self.na.AdmissionError("lookahead: ATR unavailable")

        ratio = self._rv_ratio(hist)
        if ratio is None:
            return

        rng = sb.high - sb.low
        if rng <= 0 or rng < 1.20 * atr:
            return
        upper = sb.high - max(sb.open, sb.close)
        lower = min(sb.open, sb.close) - sb.low
        upper_frac = upper / rng
        lower_frac = lower / rng
        upper_ok = upper_frac >= 0.45
        lower_ok = lower_frac >= 0.45

        if upper_ok == lower_ok:
            return

        side = -1 if upper_ok else 1
        wick_frac = upper_frac if upper_ok else lower_frac

        if ratio > 1.50:
            self.add(t, entry, side, "SIGNAL", ratio, wick_frac)
        elif ratio <= 1.00:
            self.add(t, entry, side, "CONTROL_LOW_FRONT", ratio, wick_frac)

    def process_pending(self, bar):
        na = self.na
        keep = []
        for p in self.base.pending:
            p.age += 1
            fav = max((bar.high - p.entry) if p.side > 0 else (p.entry - bar.low), 0.0)
            adv = max((p.entry - bar.low) if p.side > 0 else (bar.high - p.entry), 0.0)

            if fav > p.mfe:
                p.mfe = fav
                p.mfe_minute = p.age
            if adv > p.mae:
                p.mae = adv
                p.mae_minute = p.age

            level = 1.0
            if not p.threshold_done[level]:
                plus = fav >= p.risk
                minus = adv >= p.risk
                if plus or minus:
                    p.row["hit_1p0_winner"] = "AMBIGUOUS" if plus and minus else "PLUS" if plus else "MINUS"
                    p.row["hit_1p0_minute"] = p.age
                    p.threshold_done[level] = True

            if p.age == PRIMARY_HORIZON:
                pnl = p.side * (bar.close - p.entry)
                h = PRIMARY_HORIZON
                p.row[f"h{h}_endpoint_price"] = bar.close
                p.row[f"h{h}_endpoint_pnl_price"] = pnl
                p.row[f"h{h}_endpoint_r"] = pnl / p.risk
                p.row[f"h{h}_mfe_price"] = p.mfe
                p.row[f"h{h}_mfe_r"] = p.mfe / p.risk
                p.row[f"h{h}_mae_price"] = p.mae
                p.row[f"h{h}_mae_r"] = p.mae / p.risk
                p.row[f"h{h}_minutes_to_mfe"] = p.mfe_minute
                p.row[f"h{h}_minutes_to_mae"] = p.mae_minute
                p.row[f"h{h}_mfe_before_mae"] = (
                    None
                    if p.mfe_minute is None or p.mae_minute is None or p.mfe_minute == p.mae_minute
                    else p.mfe_minute < p.mae_minute
                )
                for c in na.COSTS:
                    p.row[f"h{h}_r_cost_{c:.2f}"] = (pnl - c) / p.risk
                self.writer.writerow(p.row)
                self.base.written += 1
            else:
                keep.append(p)
        self.base.pending = keep

    def on_m1(self, bar):
        na = self.na
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

        self.process_pending(bar)
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


def admitted_dev_rows(na, manifest: Path):
    for row in na.admitted_rows(manifest):
        d = datetime.fromisoformat(row["date"]).replace(tzinfo=UTC)
        if d < DEV_END_EXCLUSIVE:
            yield row


def preflight(na, manifest: Path, standards: Path, hashes):
    for p in (
        standards / "GUARDIAN_RESEARCH_PROTOCOL_V1.md",
        standards / "TRADE_OBSERVATION_SCHEMA_V1.json",
        standards / "guardian_observation_v1.py",
    ):
        if not p.is_file():
            raise na.AdmissionError(f"missing research standard: {p}")
    rows = list(admitted_dev_rows(na, manifest))
    return {
        "status": "PREFLIGHT_ONLY",
        "id": ID,
        "days_including_warmup": len(rows),
        "dev_start": DEV_START.isoformat(),
        "dev_end_exclusive": DEV_END_EXCLUSIVE.isoformat(),
        "spec": SPEC,
        "dependency_sha256": hashes,
        "protected_2021_2022_opened": False,
        "protected_2023_plus_opened": False,
        "protected_2026_opened": False,
    }


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

    pf = preflight(na, args.manifest, args.standards, hashes)
    if not args.execute:
        print(json.dumps(pf, indent=2, sort_keys=True, allow_nan=False))
        return 0

    if args.output_dir.exists():
        raise na.AdmissionError(f"refusing overwrite: {args.output_dir}")
    args.output_dir.mkdir(parents=True)

    spec = dict(SPEC)
    spec.update({
        "schema": 1,
        "status": "PREREGISTERED_BEFORE_DEV_PASS",
        "engine_sha256": sha256_file(Path(__file__).resolve()),
        "dependency_sha256": hashes,
    })
    (args.output_dir / "preregistered_spec.json").write_text(
        json.dumps(spec, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    rows = list(admitted_dev_rows(na, args.manifest))
    csvp = args.output_dir / "signals.csv"

    with csvp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=na.FIELDNAMES)
        writer.writeheader()
        atlas = VFWAAtlas(na, writer)

        for i, row in enumerate(rows, 1):
            d = datetime.fromisoformat(row["date"]).replace(tzinfo=UTC)
            for bar in na.decode_dukascopy_m1_day(row):
                if not (na.WARMUP_START <= bar.timestamp < DEV_END_EXCLUSIVE):
                    raise na.AdmissionError("bar outside DEV hard wall")
                atlas.on_m1(bar)

            if i % 100 == 0 or i == len(rows):
                print(json.dumps({
                    "status": "PROGRESS",
                    "days_done": i,
                    "days_total": len(rows),
                    "signals": atlas.base.signals,
                    "pending": len(atlas.base.pending),
                    "date": row["date"],
                    "protected_2021_2022_opened": False,
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
        "discarded_unfinished_at_dev_end": discarded,
        "family_counts": atlas.base.family_counts,
        "signals_csv_sha256": sha256_file(csvp),
        "engine_sha256": sha256_file(Path(__file__).resolve()),
        "dependency_sha256": hashes,
        "market_passes": 1,
        "dev_start": DEV_START.isoformat(),
        "dev_end_exclusive": DEV_END_EXCLUSIVE.isoformat(),
        "protected_2021_2022_opened": False,
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
