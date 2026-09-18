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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

UTC = timezone.utc
ID = "SIMPLE-THREE-DEV-V1"
DEV_START = datetime(2017, 1, 1, tzinfo=UTC)
DEV_END_EXCLUSIVE = datetime(2021, 1, 1, tzinfo=UTC)

EXPECTED = {
    "data_loader_v1.py": "9129e1d48ce5b05ef997e9fa12b6e55a674c2be052e69ed94bc3fb4c235b2f6b",
    "preflight_v1.py": "d0e799d4a999683c4c1a46495b56a6720d40ab61058ffe2d36a1456ab69cd354",
    "night_atlas_v1.py": "edf95e275cdd49846fed81c06d180b5190e63c25de347137136b94e0f3674860",
}

FAMILY_HORIZONS = {
    "S1_VFWA": 30,
    "S2_FWSD": 15,
    "S3_SCFB": 60,
}

CUSTOM_FIELDS = [
    "strategy_exit_r",
    "strategy_exit_price",
    "strategy_exit_minute",
    "strategy_exit_reason",
    "strategy_mfe_r",
    "strategy_mae_r",
    "ema_touch_minute",
    "ema_touch_price",
    "protected_2021_2022_opened",
]

SPEC = {
    "schema": 1,
    "id": ID,
    "status": "PREREGISTERED_BEFORE_DEV_PASS",
    "asset": "XAUUSD Dukascopy BID M1 aggregated causally to M5/M15",
    "warmup_start": "2016-11-01T00:00:00+00:00",
    "dev_start": DEV_START.isoformat(),
    "dev_end_exclusive": DEV_END_EXCLUSIVE.isoformat(),
    "market_passes": 1,
    "cost_unit": "R; analyzer subtracts C directly from gross strategy R",
    "costs_r": [0.00, 0.05, 0.10, 0.15, 0.20],
    "same_m1_barrier_ambiguity": "conservative: score Stop Loss before Take Profit",
    "protected_2021_2022_opened": False,
    "protected_2023_plus_opened": False,
    "protected_2026_opened": False,
    "strategies": {
        "S1_VFWA": {
            "name": "VOLATILITY_FRONT_WICK_ABSORPTION",
            "horizon_min": 30,
            "rv_ratio": "sum(last 6 completed M5 return^2) / sum(previous 6 completed M5 return^2)",
            "signal": "ratio > 1.50 AND signal M5 range >=1.20 ATR14_M5 AND exactly one wick >=0.45 range",
            "control": "same anatomy, ratio <=1.00; ratio (1.00,1.50] ignored",
            "direction": "opposite qualifying wick",
            "entry": "completed signal M5 close",
            "risk": "1.5*ATR14_M5 = 1R",
            "exit": "+1R TP / -1R SL / 30m time-stop",
            "dev_kill_gate": "gross MeanR >= +0.15 AND gross daily delta vs control >= +0.08R/day",
        },
        "S2_FWSD": {
            "name": "FIXING_WINDOW_STRETCH_DECAY",
            "horizon_min": 15,
            "ema": "causal EMA20 M5 including signal bar",
            "stretch": "abs(Close-EMA20)/ATR14_M5 >=2.0",
            "signal_times_utc": ["13:35", "14:35", "16:05"],
            "control": "same stretch outside inclusive M5-close windows 13:30..13:45, 14:30..14:45, 16:00..16:15 UTC",
            "direction": "toward EMA20",
            "entry": "completed signal M5 close",
            "risk": "1.5*ATR14_M5 = 1R normalization",
            "exit": "first causal M1 touch of latest available EMA20 M5, else 15m time-stop",
            "dev_pass_gate": "MeanR at 0.10R cost >0 AND daily delta at 0.10R >=+0.06R/day AND p<0.01",
        },
        "S3_SCFB": {
            "name": "SCALE_CONFLICT_FROZEN_BREAKOUT",
            "horizon_min": 60,
            "slow_return": "completed M15 close - close 12 M15 bars earlier (3h)",
            "fast_return": "completed M5 close - close 3 M5 bars earlier (15m)",
            "signal_state": "slow*fast <0 at completed M15 close",
            "control_state": "slow*fast >0 at completed M15 close",
            "frozen_center": "(High_M15+Low_M15+Close_M15)/3 at state freeze",
            "state_validity": "from freeze until and including next M15 close; max 3 subsequent completed M5 bars; then overwritten/expired",
            "trigger": "first subsequent M5 close > center+0.3 ATR for slow>0, or < center-0.3 ATR for slow<0",
            "direction": "slow M15 direction",
            "entry": "trigger M5 close",
            "risk": "1.5*ATR14_M5 = 1R",
            "exit": "+2R TP / -1R SL / 60m time-stop",
            "dev_pass_gate": "gross MeanR >=+0.15 AND MeanR at 0.10R cost >0 AND daily delta at 0.10R >=+0.05R/day AND p<0.01",
        },
    },
}


@dataclass
class StrategyPending:
    row: dict
    strategy: str
    side: int
    entry: float
    risk: float
    age: int = 0
    mfe: float = 0.0
    mae: float = 0.0


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


def sign(x):
    return 1 if x > 0 else -1 if x < 0 else 0


class SimpleThree:
    def __init__(self, na, writer):
        self.na = na
        self.base = na.Atlas(writer)
        self.writer = writer
        self.pending: list[StrategyPending] = []
        self.ema20 = None
        self.ema_alpha = 2.0 / 21.0
        self.m15_hist = deque(maxlen=10000)
        self.scfb_state = None
        self.signals = 0
        self.written = 0
        self.family_counts = {}

    @staticmethod
    def contiguous(bars, seconds):
        if len(bars) < 2:
            return True
        for a, b in zip(bars, bars[1:]):
            if a.available_at is None or b.available_at is None:
                return False
            if (b.available_at - a.available_at).total_seconds() != seconds:
                return False
        return True

    def snapshot(self, t, entry, side, family, variant, strength, aux):
        row = self.base.snapshot(t, entry, side, family, variant, strength, aux, None, None)
        row["job_id"] = ID
        row["entry"] = entry
        row["risk"] = 1.5 * float(self.base.atr5.value)
        row["risk_definition"] = "1.5*WilderATR14_M5"
        row["protected_2021_2022_opened"] = False
        row["protected_2023_plus_opened"] = False
        row["protected_2026_opened"] = False
        for k in CUSTOM_FIELDS:
            row.setdefault(k, None)
        return row

    def add(self, t, entry, side, family, variant, strength=None, aux=None):
        if not (DEV_START <= t < DEV_END_EXCLUSIVE):
            return
        row = self.snapshot(t, entry, side, family, variant, strength, aux)
        p = StrategyPending(
            row=row,
            strategy=family,
            side=side,
            entry=entry,
            risk=float(row["risk"]),
        )
        self.pending.append(p)
        self.signals += 1
        k = f"{family}|{variant}"
        self.family_counts[k] = self.family_counts.get(k, 0) + 1

    def write_exit(self, p, exit_price, exit_r, reason):
        p.row["strategy_exit_r"] = float(exit_r)
        p.row["strategy_exit_price"] = float(exit_price)
        p.row["strategy_exit_minute"] = int(p.age)
        p.row["strategy_exit_reason"] = reason
        p.row["strategy_mfe_r"] = p.mfe / p.risk
        p.row["strategy_mae_r"] = p.mae / p.risk
        self.writer.writerow(p.row)
        self.written += 1

    def update_excursions(self, p, bar):
        fav = max(
            (bar.high - p.entry) if p.side > 0 else (p.entry - bar.low),
            0.0,
        )
        adv = max(
            (p.entry - bar.low) if p.side > 0 else (bar.high - p.entry),
            0.0,
        )
        p.mfe = max(p.mfe, fav)
        p.mae = max(p.mae, adv)
        return fav, adv

    def process_pending(self, bar):
        keep = []
        for p in self.pending:
            p.age += 1
            fav, adv = self.update_excursions(p, bar)

            if p.strategy == "S1_VFWA":
                tp = fav >= p.risk
                sl = adv >= p.risk
                if sl:
                    self.write_exit(p, p.entry - p.side * p.risk, -1.0, "SL_1R" if not tp else "AMBIGUOUS_SL_FIRST")
                    continue
                if tp:
                    self.write_exit(p, p.entry + p.side * p.risk, 1.0, "TP_1R")
                    continue
                if p.age >= 30:
                    px = bar.close
                    r = p.side * (px - p.entry) / p.risk
                    self.write_exit(p, px, r, "TIME_30")
                    continue

            elif p.strategy == "S2_FWSD":
                ema = self.ema20
                if ema is not None and bar.low <= ema <= bar.high:
                    r = p.side * (ema - p.entry) / p.risk
                    p.row["ema_touch_minute"] = int(p.age)
                    p.row["ema_touch_price"] = float(ema)
                    self.write_exit(p, ema, r, "EMA_TOUCH")
                    continue
                if p.age >= 15:
                    px = bar.close
                    r = p.side * (px - p.entry) / p.risk
                    self.write_exit(p, px, r, "TIME_15")
                    continue

            elif p.strategy == "S3_SCFB":
                tp = fav >= 2.0 * p.risk
                sl = adv >= p.risk
                if sl:
                    self.write_exit(p, p.entry - p.side * p.risk, -1.0, "SL_1R" if not tp else "AMBIGUOUS_SL_FIRST")
                    continue
                if tp:
                    self.write_exit(p, p.entry + p.side * 2.0 * p.risk, 2.0, "TP_2R")
                    continue
                if p.age >= 60:
                    px = bar.close
                    r = p.side * (px - p.entry) / p.risk
                    self.write_exit(p, px, r, "TIME_60")
                    continue

            else:
                raise RuntimeError(f"unknown strategy pending: {p.strategy}")

            keep.append(p)

        self.pending = keep

    def rv_ratio(self, hist):
        bars = hist[-13:]
        if len(bars) != 13 or not self.contiguous(bars, 300):
            return None
        closes = [b.close for b in bars]
        rs = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        prior = sum(r * r for r in rs[:6])
        recent = sum(r * r for r in rs[6:])
        if prior <= 0:
            return None
        return recent / prior

    def detect_vfwa(self, t, hist, atr):
        sb = hist[-1]
        ratio = self.rv_ratio(hist)
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
        entry = sb.close

        if ratio > 1.50:
            self.add(t, entry, side, "S1_VFWA", "SIGNAL", ratio, wick_frac)
        elif ratio <= 1.00:
            self.add(t, entry, side, "S1_VFWA", "CONTROL_LOW_FRONT", ratio, wick_frac)

    @staticmethod
    def fixing_exclusion_window(t):
        m = t.hour * 60 + t.minute
        windows = (
            (13 * 60 + 30, 13 * 60 + 45),
            (14 * 60 + 30, 14 * 60 + 45),
            (16 * 60, 16 * 60 + 15),
        )
        return any(a <= m <= b for a, b in windows)

    def detect_fwsd(self, t, hist, atr):
        if self.ema20 is None:
            return
        sb = hist[-1]
        stretch_signed = (sb.close - self.ema20) / atr
        stretch = abs(stretch_signed)
        if stretch < 2.0 or stretch_signed == 0:
            return

        side = -sign(stretch_signed)
        signal_times = {(13, 35), (14, 35), (16, 5)}
        hm = (t.hour, t.minute)
        entry = sb.close

        if hm in signal_times:
            self.add(t, entry, side, "S2_FWSD", "SIGNAL", stretch, stretch_signed)
        elif not self.fixing_exclusion_window(t):
            self.add(t, entry, side, "S2_FWSD", "CONTROL_OFF_WINDOW", stretch, stretch_signed)

    def evaluate_scfb_state(self, t, hist, atr):
        st = self.scfb_state
        if not st or st["triggered"]:
            return
        if not (st["freeze_time"] < t <= st["expires_at"]):
            return

        sb = hist[-1]
        threshold = 0.30 * atr
        d = st["slow_dir"]
        crossed = (
            sb.close > st["center"] + threshold
            if d > 0
            else sb.close < st["center"] - threshold
        )
        if crossed:
            self.add(
                t,
                sb.close,
                d,
                "S3_SCFB",
                st["variant"],
                abs(st["slow_ret"]) / atr,
                abs(st["fast_ret"]) / atr,
            )
            st["triggered"] = True

    def freeze_scfb_state(self, t, hist):
        if t.minute % 15 != 0:
            return
        if len(self.m15_hist) < 13 or len(hist) < 4:
            self.scfb_state = None
            return

        m15 = list(self.m15_hist)[-13:]
        m5 = hist[-4:]
        if not self.contiguous(m15, 900) or not self.contiguous(m5, 300):
            self.scfb_state = None
            return

        slow_ret = m15[-1].close - m15[-13].close
        fast_ret = m5[-1].close - m5[-4].close
        prod = slow_ret * fast_ret
        if prod == 0:
            self.scfb_state = None
            return

        variant = "SIGNAL" if prod < 0 else "CONTROL_ALIGNED"
        bar15 = m15[-1]
        center = (bar15.high + bar15.low + bar15.close) / 3.0

        self.scfb_state = {
            "variant": variant,
            "freeze_time": t,
            "expires_at": t + self.na.timedelta(minutes=15),
            "center": center,
            "slow_ret": slow_ret,
            "fast_ret": fast_ret,
            "slow_dir": sign(slow_ret),
            "triggered": False,
        }

    def detect(self, t):
        hist = list(self.base.m5_hist)
        if len(hist) < 193:
            return
        if self.base.atr5.value is None or self.base.atr5.value <= 0:
            return

        sb = hist[-1]
        atr = float(self.base.atr5.value)
        if sb.available_at is not None and sb.available_at > t:
            raise self.na.AdmissionError("lookahead: completed M5 unavailable")
        if self.base.atr5.available_at is not None and self.base.atr5.available_at > t:
            raise self.na.AdmissionError("lookahead: ATR unavailable")

        # Existing SCFB state is evaluated first so the third M5 bar at the next
        # M15 close belongs to the old frozen state before the new state replaces it.
        self.evaluate_scfb_state(t, hist, atr)
        self.detect_vfwa(t, hist, atr)
        self.detect_fwsd(t, hist, atr)
        self.freeze_scfb_state(t, hist)

    def after_m5(self, b):
        self.base.after_m5(b)
        if self.ema20 is None:
            self.ema20 = b.close
        else:
            self.ema20 = self.ema_alpha * b.close + (1.0 - self.ema_alpha) * self.ema20

    def after_m15(self, b):
        self.m15_hist.append(b)
        self.base.rsi15.update(b.close, b.available_at)
        self.base.atr15.update(b)

    def on_m1(self, bar):
        self.base.roll_day(bar)

        if self.base.prev_m1_timestamp is not None:
            gap_minutes = int((bar.timestamp - self.base.prev_m1_timestamp).total_seconds() // 60)
            if gap_minutes > 1:
                for p in self.pending:
                    p.row["outcome_crossed_calendar_gap"] = True
                    p.row["outcome_max_gap_minutes"] = max(
                        int(p.row.get("outcome_max_gap_minutes") or 0),
                        gap_minutes - 1,
                    )

        if bar.timestamp.minute % 5 == 0:
            self.detect(bar.timestamp)

        self.process_pending(bar)

        self.base.rsi1.update(bar.close, bar.available_at)
        self.base.atr1.update(bar)

        b5 = self.base.agg5.push(bar)
        if b5:
            self.after_m5(b5)

        b15 = self.base.agg15.push(bar)
        if b15:
            self.after_m15(b15)

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
        "strategies": list(SPEC["strategies"]),
        "market_passes_planned": 1,
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
    spec["engine_sha256"] = sha256_file(Path(__file__).resolve())
    spec["dependency_sha256"] = hashes
    (args.output_dir / "preregistered_spec.json").write_text(
        json.dumps(spec, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    fieldnames = list(na.FIELDNAMES) + [x for x in CUSTOM_FIELDS if x not in na.FIELDNAMES]
    csvp = args.output_dir / "signals.csv"
    rows = list(admitted_dev_rows(na, args.manifest))

    with csvp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        lab = SimpleThree(na, writer)

        for i, row in enumerate(rows, 1):
            for bar in na.decode_dukascopy_m1_day(row):
                if not (na.WARMUP_START <= bar.timestamp < DEV_END_EXCLUSIVE):
                    raise na.AdmissionError("bar outside DEV hard wall")
                lab.on_m1(bar)

            if i % 100 == 0 or i == len(rows):
                print(json.dumps({
                    "status": "PROGRESS",
                    "days_done": i,
                    "days_total": len(rows),
                    "signals": lab.signals,
                    "pending": len(lab.pending),
                    "date": row["date"],
                    "protected_2021_2022_opened": False,
                    "protected_2023_plus_opened": False,
                    "protected_2026_opened": False,
                }, allow_nan=False), flush=True)

        discarded = len(lab.pending)
        lab.pending = []

    summary = {
        "schema": 1,
        "status": "COMPLETE",
        "id": ID,
        "signals_created": lab.signals,
        "signals_written": lab.written,
        "discarded_unfinished_at_dev_end": discarded,
        "family_counts": lab.family_counts,
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
