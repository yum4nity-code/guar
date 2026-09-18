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
ID = "GUARDIAN-ASSEMBLY-I-V1"

WARMUP_START = datetime(2022, 11, 1, tzinfo=UTC)
OOS_START = datetime(2023, 1, 1, tzinfo=UTC)
OOS_END_EXCLUSIVE = datetime(2026, 1, 1, tzinfo=UTC)

EXPECTED = {
    "data_loader_v1.py": "9129e1d48ce5b05ef997e9fa12b6e55a674c2be052e69ed94bc3fb4c235b2f6b",
    "preflight_v1.py": "d0e799d4a999683c4c1a46495b56a6720d40ab61058ffe2d36a1456ab69cd354",
    "night_atlas_v1.py": "edf95e275cdd49846fed81c06d180b5190e63c25de347137136b94e0f3674860",
}

STRATEGIES = {
    "AS1_SKEW_KURT": {
        "primary_horizon": 60,
        "secondary_horizons": [30, 120],
        "rule": "hourly; abs skew>=1.0 AND excess kurtosis>=3.0 on same prior 24 completed M5 returns; trade opposite skew sign",
        "control": "same event/timestamp; trade with skew sign",
        "components": ["A4_02_REALIZED_SKEW_REVERSAL", "A4_04_REALIZED_KURTOSIS"],
    },
    "AS2_SKEW_VOV": {
        "primary_horizon": 60,
        "secondary_horizons": [30, 120],
        "rule": "hourly; abs skew>=1.0 AND volatility-of-volatility CV>=0.75 on same prior 24 completed M5 returns; trade opposite skew sign",
        "control": "same event/timestamp; trade with skew sign",
        "components": ["A4_02_REALIZED_SKEW_REVERSAL", "A4_03_VOL_OF_VOL"],
    },
    "AS3_TAIL_KURT": {
        "primary_horizon": 60,
        "secondary_horizons": [30, 120],
        "rule": "hourly; robust tail-count exhaustion event AND excess kurtosis>=3.0 on same prior 24 completed M5 returns; trade opposite dominant tail side",
        "control": "same event/timestamp; trade with dominant tail side",
        "components": ["A5_03_TAIL_COUNT_EXHAUSTION", "A4_04_REALIZED_KURTOSIS"],
    },
}

GATE = {
    "oos_n_min": 60,
    "cost_010_pf_min": 1.10,
    "cost_010_mean_r_gt": 0.0,
    "cost_020_pf_min": 1.05,
    "cost_020_mean_r_gt": 0.0,
    "positive_years_min": 2,
    "daily_bootstrap_signal_ci95_lower_gt": 0.0,
    "mean_r_without_top1pct_winners_gt": 0.0,
    "control_delta_gt": 0.0,
    "control_delta_ci95_lower_gt": 0.0,
    "matched_control_delta_gt": 0.0,
    "bh_q_max": 0.10,
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


def sgn(x):
    return 1 if x > 0 else -1 if x < 0 else 0


def admitted_rows(dl, pf, manifest_path: Path):
    if not manifest_path.is_file():
        raise dl.AdmissionError("manifest absent")
    if pf.canonical_manifest_sha256(manifest_path) != pf.PINNED_MANIFEST_SHA256:
        raise dl.AdmissionError("manifest hash mismatch")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    datasets = pf.validate_manifest(manifest)
    duka = datasets["DUKASCOPY_XAUUSD_BID_M1_BI5_2004_2025"]

    if duka["asset"] != "XAUUSD" or duka["timezone"] != "UTC" or duka["granularity"] != "M1":
        raise dl.AdmissionError("Assembly I requires XAUUSD UTC M1")

    rows = dl.load_dukascopy_index(Path(duka["payload_index"]), duka["payload_index_sha256"])
    for row in rows:
        day = datetime.fromisoformat(row["date"]).replace(tzinfo=UTC)
        if WARMUP_START <= day < OOS_END_EXCLUSIVE:
            yield row


class Assembly1:
    def __init__(self, na, writer):
        self.na = na
        self.base = na.Atlas(writer)
        self.just_gap = False

    def __getattr__(self, name):
        return getattr(self.base, name)

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

    def snapshot(self, *args, **kwargs):
        row = self.base.snapshot(*args, **kwargs)
        row["job_id"] = ID
        row["protected_2023_plus_opened"] = True
        row["protected_2026_opened"] = False
        return row

    def add(self, t, entry, side, family, variant, strength=None, aux=None):
        na = self.na
        if not (OOS_START <= t < OOS_END_EXCLUSIVE - na.timedelta(minutes=na.MAX_HORIZON_MIN)):
            return
        row = self.snapshot(t, entry, side, family, variant, strength, aux, None, None)
        self.base.pending.append(na.Pending(row, side, entry, float(row["risk"])))
        self.base.signals += 1
        key = f"{family}|{variant}"
        self.base.family_counts[key] = self.base.family_counts.get(key, 0) + 1

    @staticmethod
    def moments(rs):
        mu = sum(rs) / len(rs)
        var = sum((r - mu) ** 2 for r in rs) / len(rs)
        if var <= 0:
            return None
        sd = math.sqrt(var)
        skew = sum(((r - mu) / sd) ** 3 for r in rs) / len(rs)
        excess = sum(((r - mu) / sd) ** 4 for r in rs) / len(rs) - 3.0
        return mu, sd, skew, excess

    @staticmethod
    def vov_cv(rs):
        rv = [sum(r * r for r in rs[i:i + 6]) for i in (0, 6, 12, 18)]
        mean_rv = sum(rv) / 4.0
        if mean_rv <= 0:
            return None
        return statistics.pstdev(rv) / mean_rv

    @staticmethod
    def tail_exhaustion(rs):
        med = statistics.median(rs)
        mad = statistics.median([abs(r - med) for r in rs])
        scale = 1.4826 * mad
        if scale <= 0:
            return None
        up = sum(r > med + 1.5 * scale for r in rs)
        dn = sum(r < med - 1.5 * scale for r in rs)
        if up + dn < 4 or abs(up - dn) < 3:
            return None
        dominant = 1 if up > dn else -1
        imbalance = abs(up - dn) / (up + dn)
        return dominant, imbalance, (up + dn) / 24.0

    def detect(self, t, entry):
        if t.minute != 0:
            return
        hist = list(self.base.m5_hist)
        if len(hist) < 193 or self.just_gap:
            return
        if any(x is None for x in (self.base.atr5.value, self.base.rsi1.value, self.base.rsi5.value, self.base.rsi15.value)):
            return

        atr = float(self.base.atr5.value)
        if atr <= 0:
            return

        bars = hist[-25:]
        if len(bars) != 25 or not self.contiguous(bars):
            return

        for ts in (
            bars[-1].available_at,
            self.base.rsi1.available_at,
            self.base.rsi5.available_at,
            self.base.rsi15.available_at,
            self.base.atr5.available_at,
        ):
            if ts is not None and ts > t:
                raise self.na.AdmissionError("lookahead")

        rs = self.returns_from_bars(bars)
        if len(rs) != 24:
            return

        mom = self.moments(rs)
        if mom is None:
            return
        _, sd, skew, excess = mom
        cv = self.vov_cv(rs)
        tail = self.tail_exhaustion(rs)

        if abs(skew) >= 1.0 and excess >= 3.0:
            d = sgn(skew)
            self.add(t, entry, -d, "AS1_SKEW_KURT", "SIGNAL", strength=abs(skew), aux=excess)
            self.add(t, entry, d, "AS1_SKEW_KURT", "CONTROL_OPPOSITE", strength=abs(skew), aux=excess)

        if abs(skew) >= 1.0 and cv is not None and cv >= 0.75:
            d = sgn(skew)
            self.add(t, entry, -d, "AS2_SKEW_VOV", "SIGNAL", strength=abs(skew), aux=cv)
            self.add(t, entry, d, "AS2_SKEW_VOV", "CONTROL_OPPOSITE", strength=abs(skew), aux=cv)

        if tail is not None and excess >= 3.0:
            dominant, imbalance, tail_fraction = tail
            self.add(t, entry, -dominant, "AS3_TAIL_KURT", "SIGNAL", strength=imbalance, aux=excess)
            self.add(t, entry, dominant, "AS3_TAIL_KURT", "CONTROL_OPPOSITE", strength=imbalance, aux=excess)

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
    dl = importlib.import_module("data_loader_v1")
    pf = importlib.import_module("preflight_v1")

    for p in (
        args.standards / "GUARDIAN_RESEARCH_PROTOCOL_V1.md",
        args.standards / "TRADE_OBSERVATION_SCHEMA_V1.json",
        args.standards / "guardian_observation_v1.py",
    ):
        if not p.is_file():
            raise dl.AdmissionError(f"missing research standard: {p}")

    rows = list(admitted_rows(dl, pf, args.manifest))

    preflight = {
        "status": "PREFLIGHT_ONLY",
        "id": ID,
        "days_including_warmup": len(rows),
        "warmup_start": WARMUP_START.isoformat(),
        "oos_start": OOS_START.isoformat(),
        "oos_end_exclusive": OOS_END_EXCLUSIVE.isoformat(),
        "strategies": STRATEGIES,
        "gate": GATE,
        "market_passes_planned": 1,
        "protected_2023_2025_opened_by_this_run": True,
        "protected_2026_opened": False,
        "dependency_sha256": hashes,
    }

    if not args.execute:
        print(json.dumps(preflight, indent=2, sort_keys=True, allow_nan=False))
        return 0

    if args.output_dir.exists():
        raise dl.AdmissionError(f"refusing overwrite: {args.output_dir}")
    args.output_dir.mkdir(parents=True)

    spec = {
        "schema": 1,
        "id": ID,
        "status": "PREREGISTERED_BEFORE_EXTERNAL_OOS_PASS",
        "warmup_start": WARMUP_START.isoformat(),
        "oos_start": OOS_START.isoformat(),
        "oos_end_exclusive": OOS_END_EXCLUSIVE.isoformat(),
        "strategies": STRATEGIES,
        "gate": GATE,
        "risk_definition": "1.5*WilderATR14_M5",
        "cost_semantics": "legacy Atlas stress: subtract absolute XAUUSD price cost 0.10/0.20 before dividing by risk",
        "non_overlap": "60 wall-clock minutes separately per strategy+variant after gap/risk filtering",
        "secondary_horizons": "descriptive only; cannot rescue failed H60",
        "multiple_testing": "Benjamini-Hochberg across three primary signal-vs-opposite-control tests",
        "same_event_control": True,
        "no_posthoc_tuning_on_2023_2025": True,
        "protected_2023_2025_opened_by_this_run": True,
        "protected_2026_opened": False,
        "engine_sha256": sha256_file(Path(__file__).resolve()),
        "dependency_sha256": hashes,
    }
    (args.output_dir / "preregistered_spec.json").write_text(
        json.dumps(spec, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    csvp = args.output_dir / "signals.csv"
    with csvp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=na.FIELDNAMES)
        writer.writeheader()
        lab = Assembly1(na, writer)

        for i, row in enumerate(rows, 1):
            for bar in dl.decode_dukascopy_m1_day(row):
                if not (WARMUP_START <= bar.timestamp < OOS_END_EXCLUSIVE):
                    raise dl.AdmissionError("bar outside Assembly I hard wall")
                lab.on_m1(bar)

            if i % 100 == 0 or i == len(rows):
                print(json.dumps({
                    "status": "PROGRESS",
                    "days_done": i,
                    "days_total": len(rows),
                    "signals": lab.base.signals,
                    "pending": len(lab.base.pending),
                    "date": row["date"],
                    "protected_2023_2025_opened": True,
                    "protected_2026_opened": False,
                }, allow_nan=False), flush=True)

        discarded = len(lab.base.pending)
        lab.base.pending = []

    summary = {
        "schema": 1,
        "status": "COMPLETE",
        "id": ID,
        "signals_created": lab.base.signals,
        "signals_written": lab.base.written,
        "discarded_unfinished_at_end": discarded,
        "family_counts": lab.base.family_counts,
        "signals_csv_sha256": sha256_file(csvp),
        "engine_sha256": sha256_file(Path(__file__).resolve()),
        "dependency_sha256": hashes,
        "market_passes": 1,
        "oos_start": OOS_START.isoformat(),
        "oos_end_exclusive": OOS_END_EXCLUSIVE.isoformat(),
        "protected_2023_2025_opened": True,
        "protected_2026_opened": False,
    }
    (args.output_dir / "engine_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
