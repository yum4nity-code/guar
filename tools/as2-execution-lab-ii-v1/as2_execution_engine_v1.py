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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
ID = "AS2-EXECUTION-LAB-II-V1"

WARMUP_START = datetime(2016, 11, 1, tzinfo=UTC)
DEV_START = datetime(2017, 1, 1, tzinfo=UTC)
DEV_END_EXCLUSIVE = datetime(2026, 1, 1, tzinfo=UTC)
MAX_HOLD_MIN = 60

EXPECTED = {
    "data_loader_v1.py": "9129e1d48ce5b05ef997e9fa12b6e55a674c2be052e69ed94bc3fb4c235b2f6b",
    "preflight_v1.py": "d0e799d4a999683c4c1a46495b56a6720d40ab61058ffe2d36a1456ab69cd354",
    "night_atlas_v1.py": "edf95e275cdd49846fed81c06d180b5190e63c25de347137136b94e0f3674860",
}

CANDIDATES = [
    {"id": "TIME_H30", "kind": "TIME", "time": 30},
    {"id": "TIME_H60", "kind": "TIME", "time": 60},

    {"id": "BR_TP2_SL2_H30", "kind": "BRACKET", "tp": 2.0, "sl": 2.0, "time": 30},
    {"id": "BR_TP2_SL2_H60", "kind": "BRACKET", "tp": 2.0, "sl": 2.0, "time": 60},
    {"id": "BR_TP2_SL2p5_H60", "kind": "BRACKET", "tp": 2.0, "sl": 2.5, "time": 60},
    {"id": "BR_TP2p5_SL2_H60", "kind": "BRACKET", "tp": 2.5, "sl": 2.0, "time": 60},
    {"id": "BR_TP2p5_SL2p5_H60", "kind": "BRACKET", "tp": 2.5, "sl": 2.5, "time": 60},

    {"id": "BE1_TP2_SL2_H60", "kind": "BE", "be": 1.0, "tp": 2.0, "sl": 2.0, "time": 60},
    {"id": "BE1p5_TP2_SL2_H60", "kind": "BE", "be": 1.5, "tp": 2.0, "sl": 2.0, "time": 60},
    {"id": "BE1_TP2p5_SL2_H60", "kind": "BE", "be": 1.0, "tp": 2.5, "sl": 2.0, "time": 60},

    {
        "id": "P50_1R_BE_TP2_SL2_H60",
        "kind": "PARTIAL_BE",
        "partial": 1.0, "fraction": 0.5, "tp": 2.0, "sl": 2.0, "time": 60,
    },
    {
        "id": "P50_1p5R_BE_TP2p5_SL2_H60",
        "kind": "PARTIAL_BE",
        "partial": 1.5, "fraction": 0.5, "tp": 2.5, "sl": 2.0, "time": 60,
    },
]

FIELDNAMES = [
    "job_id", "candidate", "variant", "entry_time", "year", "side",
    "entry", "risk", "skew", "vov_cv",
    "exit_minute", "exit_reason", "gross_r", "r_cost_010", "r_cost_020",
    "partial_taken", "be_activated", "crossed_calendar_gap",
]


@dataclass
class Position:
    candidate: dict
    variant: str
    side: int
    entry_time: datetime
    entry: float
    risk: float
    skew: float
    vov_cv: float
    age: int = 0
    be_active: bool = False
    partial_taken: bool = False
    realized_r: float = 0.0
    remaining_fraction: float = 1.0
    crossed_gap: bool = False


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
            raise RuntimeError(
                f"hash mismatch {name}: expected {expected}, got {actual}"
            )
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

    if (
        duka["asset"] != "XAUUSD"
        or duka["timezone"] != "UTC"
        or duka["granularity"] != "M1"
    ):
        raise dl.AdmissionError("Execution Lab II requires XAUUSD UTC M1")

    rows = dl.load_dukascopy_index(
        Path(duka["payload_index"]),
        duka["payload_index_sha256"],
    )
    for row in rows:
        day = datetime.fromisoformat(row["date"]).replace(tzinfo=UTC)
        if WARMUP_START <= day < DEV_END_EXCLUSIVE:
            yield row


class Lab:
    def __init__(self, na, writer):
        self.na = na
        self.base = na.Atlas(None)
        self.writer = writer
        self.positions: list[Position] = []
        self.just_gap = False
        self.signal_events = 0
        self.rows_written = 0
        self.candidate_counts = {}

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
        return [
            bars[i].close - bars[i - 1].close
            for i in range(1, len(bars))
        ]

    @staticmethod
    def skew_value(rs):
        mu = sum(rs) / len(rs)
        var = sum((r - mu) ** 2 for r in rs) / len(rs)
        if var <= 0:
            return None
        sd = math.sqrt(var)
        return sum(((r - mu) / sd) ** 3 for r in rs) / len(rs)

    @staticmethod
    def vov_value(rs):
        rv = [
            sum(r * r for r in rs[i:i + 6])
            for i in (0, 6, 12, 18)
        ]
        mean_rv = sum(rv) / 4.0
        if mean_rv <= 0:
            return None
        return statistics.pstdev(rv) / mean_rv

    def add_event(self, t, entry, risk, skew, vov):
        if not (
            DEV_START <= t
            < DEV_END_EXCLUSIVE - timedelta(minutes=MAX_HOLD_MIN)
        ):
            return

        d = sgn(skew)
        if d == 0:
            return

        self.signal_events += 1

        for candidate in CANDIDATES:
            for variant, side in (
                ("SIGNAL", -d),
                ("CONTROL_OPPOSITE", d),
            ):
                self.positions.append(
                    Position(
                        candidate=candidate,
                        variant=variant,
                        side=side,
                        entry_time=t,
                        entry=entry,
                        risk=risk,
                        skew=skew,
                        vov_cv=vov,
                    )
                )
                key = f"{candidate['id']}|{variant}"
                self.candidate_counts[key] = (
                    self.candidate_counts.get(key, 0) + 1
                )

    def detect(self, t, entry):
        if t.minute != 0 or self.just_gap:
            return

        hist = list(self.base.m5_hist)
        if len(hist) < 193 or self.base.atr5.value is None:
            return

        atr = float(self.base.atr5.value)
        if atr <= 0:
            return

        bars = hist[-25:]
        if len(bars) != 25 or not self.contiguous(bars):
            return

        for ts in (bars[-1].available_at, self.base.atr5.available_at):
            if ts is not None and ts > t:
                raise self.na.AdmissionError("lookahead")

        rs = self.returns_from_bars(bars)
        if len(rs) != 24:
            return

        skew = self.skew_value(rs)
        vov = self.vov_value(rs)

        if (
            skew is not None
            and vov is not None
            and abs(skew) >= 1.0
            and vov >= 0.75
        ):
            self.add_event(
                t=t,
                entry=entry,
                risk=1.5 * atr,
                skew=skew,
                vov=vov,
            )

    @staticmethod
    def excursions(bar, p):
        fav = (
            bar.high - p.entry
            if p.side > 0
            else p.entry - bar.low
        )
        adv = (
            p.entry - bar.low
            if p.side > 0
            else bar.high - p.entry
        )
        return max(fav, 0.0), max(adv, 0.0)

    @staticmethod
    def endpoint_r(bar, p):
        return p.side * (bar.close - p.entry) / p.risk

    def write_exit(self, p, gross_r, reason):
        self.writer.writerow({
            "job_id": ID,
            "candidate": p.candidate["id"],
            "variant": p.variant,
            "entry_time": p.entry_time.isoformat(),
            "year": p.entry_time.year,
            "side": p.side,
            "entry": p.entry,
            "risk": p.risk,
            "skew": p.skew,
            "vov_cv": p.vov_cv,
            "exit_minute": p.age,
            "exit_reason": reason,
            "gross_r": gross_r,
            "r_cost_010": gross_r - 0.10 / p.risk,
            "r_cost_020": gross_r - 0.20 / p.risk,
            "partial_taken": p.partial_taken,
            "be_activated": p.be_active,
            "crossed_calendar_gap": p.crossed_gap,
        })
        self.rows_written += 1

    def step_time(self, bar, p):
        if p.age >= p.candidate["time"]:
            self.write_exit(
                p,
                self.endpoint_r(bar, p),
                "TIME",
            )
            return True
        return False

    def step_bracket(self, bar, p):
        c = p.candidate
        fav, adv = self.excursions(bar, p)
        tp_hit = fav >= c["tp"] * p.risk
        sl_hit = adv >= c["sl"] * p.risk

        if tp_hit and sl_hit:
            self.write_exit(p, -c["sl"], "AMBIGUOUS_STOP_FIRST")
            return True
        if sl_hit:
            self.write_exit(p, -c["sl"], "SL")
            return True
        if tp_hit:
            self.write_exit(p, c["tp"], "TP")
            return True
        if p.age >= c["time"]:
            self.write_exit(p, self.endpoint_r(bar, p), "TIME")
            return True
        return False

    def step_be(self, bar, p):
        c = p.candidate
        fav, adv = self.excursions(bar, p)

        if p.be_active:
            tp_hit = fav >= c["tp"] * p.risk
            be_hit = (
                bar.low <= p.entry
                if p.side > 0
                else bar.high >= p.entry
            )

            if tp_hit and be_hit:
                self.write_exit(p, 0.0, "AMBIGUOUS_BE_FIRST")
                return True
            if be_hit:
                self.write_exit(p, 0.0, "BE")
                return True
            if tp_hit:
                self.write_exit(p, c["tp"], "TP")
                return True
        else:
            tp_hit = fav >= c["tp"] * p.risk
            sl_hit = adv >= c["sl"] * p.risk

            if tp_hit and sl_hit:
                self.write_exit(p, -c["sl"], "AMBIGUOUS_STOP_FIRST")
                return True
            if sl_hit:
                self.write_exit(p, -c["sl"], "SL")
                return True
            if tp_hit:
                self.write_exit(p, c["tp"], "TP")
                return True

            if fav >= c["be"] * p.risk:
                # Conservative intrabar convention:
                # BE becomes active only from the next M1 bar.
                p.be_active = True

        if p.age >= c["time"]:
            self.write_exit(p, self.endpoint_r(bar, p), "TIME")
            return True
        return False

    def step_partial_be(self, bar, p):
        c = p.candidate
        fav, adv = self.excursions(bar, p)

        if not p.partial_taken:
            full_sl_hit = adv >= c["sl"] * p.risk
            runner_tp_hit = fav >= c["tp"] * p.risk
            partial_hit = fav >= c["partial"] * p.risk

            # If favorable and adverse barriers coexist in the same M1,
            # score the full initial stop first.
            if full_sl_hit and (partial_hit or runner_tp_hit):
                self.write_exit(
                    p,
                    -c["sl"],
                    "AMBIGUOUS_FULL_STOP_FIRST",
                )
                return True
            if full_sl_hit:
                self.write_exit(p, -c["sl"], "SL_BEFORE_PARTIAL")
                return True

            if runner_tp_hit:
                # Reaching runner TP necessarily traverses the partial level.
                gross = (
                    c["fraction"] * c["partial"]
                    + (1.0 - c["fraction"]) * c["tp"]
                )
                p.partial_taken = True
                p.be_active = True
                self.write_exit(p, gross, "PARTIAL_PLUS_RUNNER_TP")
                return True

            if partial_hit:
                p.partial_taken = True
                p.realized_r = c["fraction"] * c["partial"]
                p.remaining_fraction = 1.0 - c["fraction"]
                # BE protection starts on the next M1 only.
                p.be_active = True

        else:
            runner_tp_hit = fav >= c["tp"] * p.risk
            be_hit = (
                bar.low <= p.entry
                if p.side > 0
                else bar.high >= p.entry
            )

            if runner_tp_hit and be_hit:
                self.write_exit(
                    p,
                    p.realized_r,
                    "AMBIGUOUS_RUNNER_BE_FIRST",
                )
                return True
            if be_hit:
                self.write_exit(p, p.realized_r, "RUNNER_BE")
                return True
            if runner_tp_hit:
                gross = (
                    p.realized_r
                    + p.remaining_fraction * c["tp"]
                )
                self.write_exit(p, gross, "RUNNER_TP")
                return True

        if p.age >= c["time"]:
            ep = self.endpoint_r(bar, p)
            if p.partial_taken:
                gross = p.realized_r + p.remaining_fraction * ep
                self.write_exit(p, gross, "TIME_AFTER_PARTIAL")
            else:
                self.write_exit(p, ep, "TIME")
            return True

        return False

    def process_positions(self, bar):
        keep = []
        for p in self.positions:
            p.age += 1
            kind = p.candidate["kind"]

            if kind == "TIME":
                done = self.step_time(bar, p)
            elif kind == "BRACKET":
                done = self.step_bracket(bar, p)
            elif kind == "BE":
                done = self.step_be(bar, p)
            elif kind == "PARTIAL_BE":
                done = self.step_partial_be(bar, p)
            else:
                raise RuntimeError(f"unknown candidate kind: {kind}")

            if not done:
                keep.append(p)
        self.positions = keep

    def on_m1(self, bar):
        prev = self.base.prev_m1_timestamp
        self.just_gap = False

        if prev is not None:
            gap_minutes = int(
                (bar.timestamp - prev).total_seconds() // 60
            )
            if gap_minutes > 1:
                self.just_gap = True
                # Mark positions that existed before the new bar.
                for p in self.positions:
                    p.crossed_gap = True

        self.base.roll_day(bar)

        if bar.timestamp.minute % 5 == 0:
            self.detect(bar.timestamp, bar.open)

        self.process_positions(bar)

        # Maintain the same causal M5 state convention as Atlas/Assembly I.
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
        "development_start": DEV_START.isoformat(),
        "development_end_exclusive": DEV_END_EXCLUSIVE.isoformat(),
        "entry_rule": "AS2_SKEW_VOV frozen: hourly abs(skew)>=1.0 and VOV CV>=0.75; signal trades opposite skew",
        "candidates": CANDIDATES,
        "market_passes_planned": 1,
        "development_2017_2025_contaminated": True,
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
        "status": "PREREGISTERED_BEFORE_EXECUTION_REPLAY",
        "warmup_start": WARMUP_START.isoformat(),
        "development_start": DEV_START.isoformat(),
        "development_end_exclusive": DEV_END_EXCLUSIVE.isoformat(),
        "entry_rule": preflight["entry_rule"],
        "candidates": CANDIDATES,
        "same_event_opposite_control": True,
        "intrabar_policy": {
            "tp_and_sl_same_m1": "stop first",
            "be_activation": "active next M1 only",
            "be_and_tp_same_m1_after_activation": "BE first",
            "partial_and_initial_sl_same_m1": "full initial stop first",
            "partial_and_runner_tp_same_m1_without_sl": "both fills accepted because runner TP path necessarily crosses partial level",
            "runner_be_and_tp_same_m1": "BE first",
        },
        "cost_semantics": "subtract absolute XAUUSD price cost 0.10/0.20 divided by row risk from gross R",
        "risk_definition": "1.5*WilderATR14_M5",
        "max_hold_minutes": MAX_HOLD_MIN,
        "development_2017_2025_contaminated": True,
        "protected_2026_opened": False,
        "engine_sha256": sha256_file(Path(__file__).resolve()),
        "dependency_sha256": hashes,
    }
    (args.output_dir / "preregistered_spec.json").write_text(
        json.dumps(spec, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    csvp = args.output_dir / "execution_rows.csv"
    with csvp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        lab = Lab(na, writer)

        for i, row in enumerate(rows, 1):
            for bar in dl.decode_dukascopy_m1_day(row):
                if not (
                    WARMUP_START
                    <= bar.timestamp
                    < DEV_END_EXCLUSIVE
                ):
                    raise dl.AdmissionError(
                        "bar outside Execution Lab II hard wall"
                    )
                lab.on_m1(bar)

            if i % 100 == 0 or i == len(rows):
                print(json.dumps({
                    "status": "PROGRESS",
                    "days_done": i,
                    "days_total": len(rows),
                    "signal_events": lab.signal_events,
                    "open_positions": len(lab.positions),
                    "rows_written": lab.rows_written,
                    "date": row["date"],
                    "protected_2026_opened": False,
                }, allow_nan=False), flush=True)

        discarded = len(lab.positions)
        lab.positions = []

    summary = {
        "schema": 1,
        "status": "COMPLETE",
        "id": ID,
        "signal_events": lab.signal_events,
        "rows_written": lab.rows_written,
        "discarded_unfinished_at_end": discarded,
        "candidate_counts": lab.candidate_counts,
        "execution_rows_sha256": sha256_file(csvp),
        "engine_sha256": sha256_file(Path(__file__).resolve()),
        "dependency_sha256": hashes,
        "market_passes": 1,
        "development_start": DEV_START.isoformat(),
        "development_end_exclusive": DEV_END_EXCLUSIVE.isoformat(),
        "development_2017_2025_contaminated": True,
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
