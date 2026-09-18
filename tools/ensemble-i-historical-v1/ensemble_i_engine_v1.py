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
ID = "GUARDIAN-ENSEMBLE-I-HISTORICAL-HOLDOUT-V1"

WARMUP_START = datetime(2004, 1, 1, tzinfo=UTC)
HOLDOUT_START = datetime(2004, 4, 1, tzinfo=UTC)
HOLDOUT_END_EXCLUSIVE = datetime(2017, 1, 1, tzinfo=UTC)

EXPECTED = {
    "data_loader_v1.py": "9129e1d48ce5b05ef997e9fa12b6e55a674c2be052e69ed94bc3fb4c235b2f6b",
    "preflight_v1.py": "d0e799d4a999683c4c1a46495b56a6720d40ab61058ffe2d36a1456ab69cd354",
    "night_atlas_v1.py": "edf95e275cdd49846fed81c06d180b5190e63c25de347137136b94e0f3674860",
}

ENSEMBLES = {
    "E1_CONSENSUS_2PLUS_4": {
        "description": (
            "Use SKEW_REV, TAIL_EXH, STRETCH_MR, SCALE_CONFLICT. "
            "At least two active votes and all active votes must agree."
        ),
        "horizon": 60,
    },
    "E2_DIVERSE_SCORE_5": {
        "description": (
            "Use all five votes including SEASONALITY. Require at least "
            "three active votes and absolute vote sum >=2."
        ),
        "horizon": 60,
    },
    "E3_AS2_CONFIRMED": {
        "description": (
            "AS2 core: SKEW_REV active and VOV>=0.75. Require at least one "
            "of TAIL_EXH, STRETCH_MR, SCALE_CONFLICT, SEASONALITY to agree "
            "with the skew-reversal direction."
        ),
        "horizon": 60,
    },
}

GATE = {
    "n_min": 300,
    "cost_010_pf_min": 1.10,
    "cost_010_mean_gt": 0.0,
    "cost_020_pf_min": 1.05,
    "cost_020_mean_gt": 0.0,
    "positive_years_min": 9,
    "early_2004_2010_mean_gt": 0.0,
    "late_2011_2016_mean_gt": 0.0,
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


def sign(x):
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
        raise dl.AdmissionError("Ensemble I requires XAUUSD UTC M1")

    rows = dl.load_dukascopy_index(
        Path(duka["payload_index"]),
        duka["payload_index_sha256"],
    )

    selected = []
    for row in rows:
        day = datetime.fromisoformat(row["date"]).replace(tzinfo=UTC)
        if WARMUP_START <= day < HOLDOUT_END_EXCLUSIVE:
            selected.append(row)

    if not selected:
        raise dl.AdmissionError("no historical rows admitted")

    first_day = datetime.fromisoformat(selected[0]["date"]).replace(tzinfo=UTC)
    if first_day >= datetime(2004, 2, 1, tzinfo=UTC):
        raise dl.AdmissionError(
            f"historical payload lacks January 2004 warmup: {first_day.isoformat()}"
        )

    return selected


class EnsembleLab:
    def __init__(self, na, writer):
        self.na = na
        self.base = na.Atlas(writer)
        self.ema20 = None
        self.ema_alpha = 2.0 / 21.0
        self.same_hour = {h: deque(maxlen=60) for h in range(24)}
        self.just_gap = False
        self.events = 0

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
    def realized_skew(rs):
        mu = sum(rs) / len(rs)
        var = sum((r - mu) ** 2 for r in rs) / len(rs)
        if var <= 0:
            return None
        sd = math.sqrt(var)
        return sum(((r - mu) / sd) ** 3 for r in rs) / len(rs)

    @staticmethod
    def vov_cv(rs):
        rv = [
            sum(r * r for r in rs[i:i + 6])
            for i in (0, 6, 12, 18)
        ]
        mean_rv = sum(rv) / 4.0
        if mean_rv <= 0:
            return None
        return statistics.pstdev(rv) / mean_rv

    @staticmethod
    def tail_vote(rs):
        med = statistics.median(rs)
        mad = statistics.median(abs(r - med) for r in rs)
        scale = 1.4826 * mad
        if scale <= 0:
            return None

        up = sum(r > med + 1.5 * scale for r in rs)
        dn = sum(r < med - 1.5 * scale for r in rs)

        if up + dn < 4 or abs(up - dn) < 3:
            return None

        dominant = 1 if up > dn else -1
        return -dominant

    def scale_conflict_vote(self, closes):
        if len(closes) < 25:
            return None
        bslow = closes[-4] - closes[-25]
        fast = closes[-1] - closes[-4]
        d = sign(bslow)
        if d and fast != 0 and bslow * fast < 0:
            return d
        return None

    def stretch_vote(self, hist, atr):
        if self.ema20 is None or atr <= 0:
            return None
        stretch = (hist[-1].close - self.ema20) / atr
        if abs(stretch) >= 2.0:
            return -sign(stretch)
        return None

    def seasonality_vote(self, t):
        bucket = self.same_hour[t.hour]
        if len(bucket) < 40:
            return None
        m = sum(bucket) / len(bucket)
        return sign(m) if m != 0 else None

    def update_seasonality_state(self, t, hist, atr):
        bars = hist[-12:]
        if (
            len(bars) != 12
            or not self.contiguous(bars)
            or atr <= 0
        ):
            return

        completed_hour = (t.hour - 1) % 24
        normalized_return = (bars[-1].close - bars[0].open) / atr
        self.same_hour[completed_hour].append(normalized_return)

    def ensemble_sides(self, votes, vov):
        out = {}

        core4_names = (
            "SKEW_REV",
            "TAIL_EXH",
            "STRETCH_MR",
            "SCALE_CONFLICT",
        )
        core4 = [votes[k] for k in core4_names if votes[k] is not None]

        if len(core4) >= 2 and abs(sum(core4)) == len(core4):
            out["E1_CONSENSUS_2PLUS_4"] = sign(sum(core4))

        all5 = [
            votes[k]
            for k in (
                "SKEW_REV",
                "TAIL_EXH",
                "STRETCH_MR",
                "SCALE_CONFLICT",
                "SEASONALITY",
            )
            if votes[k] is not None
        ]

        if len(all5) >= 3 and abs(sum(all5)) >= 2:
            out["E2_DIVERSE_SCORE_5"] = sign(sum(all5))

        skew_side = votes["SKEW_REV"]
        if skew_side is not None and vov is not None and vov >= 0.75:
            confirms = [
                votes[k]
                for k in (
                    "TAIL_EXH",
                    "STRETCH_MR",
                    "SCALE_CONFLICT",
                    "SEASONALITY",
                )
                if votes[k] is not None
            ]
            if any(v == skew_side for v in confirms):
                out["E3_AS2_CONFIRMED"] = skew_side

        return out

    def add(self, t, entry, side, family, strength, aux):
        na = self.na

        if not (
            HOLDOUT_START
            <= t
            < HOLDOUT_END_EXCLUSIVE - na.timedelta(minutes=na.MAX_HORIZON_MIN)
        ):
            return

        row = self.base.snapshot(
            t,
            entry,
            side,
            family,
            "SIGNAL",
            strength,
            aux,
            None,
            None,
        )
        row["job_id"] = ID
        row["protected_2023_plus_opened"] = False
        row["protected_2026_opened"] = False

        self.base.pending.append(
            na.Pending(row, side, entry, float(row["risk"]))
        )
        self.base.signals += 1

        ctl = self.base.snapshot(
            t,
            entry,
            -side,
            family,
            "CONTROL_OPPOSITE",
            strength,
            aux,
            None,
            None,
        )
        ctl["job_id"] = ID
        ctl["protected_2023_plus_opened"] = False
        ctl["protected_2026_opened"] = False

        self.base.pending.append(
            na.Pending(ctl, -side, entry, float(ctl["risk"]))
        )
        self.base.signals += 1

        for variant in ("SIGNAL", "CONTROL_OPPOSITE"):
            key = f"{family}|{variant}"
            self.base.family_counts[key] = (
                self.base.family_counts.get(key, 0) + 1
            )

        self.events += 1

    def detect(self, t, entry):
        if t.minute != 0:
            return

        hist = list(self.base.m5_hist)

        if len(hist) < 193 or self.just_gap:
            return

        if any(
            x is None
            for x in (
                self.base.atr5.value,
                self.base.rsi1.value,
                self.base.rsi5.value,
                self.base.rsi15.value,
            )
        ):
            return

        atr = float(self.base.atr5.value)
        if atr <= 0:
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

        bars = hist[-25:]
        if len(bars) != 25 or not self.contiguous(bars):
            return

        rs = self.returns_from_bars(bars)
        if len(rs) != 24:
            return

        skew = self.realized_skew(rs)
        vov = self.vov_cv(rs)

        skew_vote = None
        if skew is not None and abs(skew) >= 1.0:
            skew_vote = -sign(skew)

        votes = {
            "SKEW_REV": skew_vote,
            "TAIL_EXH": self.tail_vote(rs),
            "STRETCH_MR": self.stretch_vote(hist, atr),
            "SCALE_CONFLICT": self.scale_conflict_vote(
                [b.close for b in hist]
            ),
            "SEASONALITY": self.seasonality_vote(t),
        }

        sides = self.ensemble_sides(votes, vov)

        active_n = sum(v is not None for v in votes.values())
        vote_sum = sum(v for v in votes.values() if v is not None)

        for family, side in sides.items():
            if side:
                self.add(
                    t,
                    entry,
                    side,
                    family,
                    strength=abs(vote_sum),
                    aux=active_n,
                )

        self.update_seasonality_state(t, hist, atr)

    def after_m5(self, b):
        self.base.after_m5(b)

        if self.ema20 is None:
            self.ema20 = b.close
        else:
            self.ema20 = (
                self.ema_alpha * b.close
                + (1.0 - self.ema_alpha) * self.ema20
            )

    def on_m1(self, bar):
        prev = self.base.prev_m1_timestamp
        self.just_gap = False

        if prev is not None:
            gap_minutes = int(
                (bar.timestamp - prev).total_seconds() // 60
            )
            if gap_minutes > 1:
                self.just_gap = True

        self.base.roll_day(bar)

        if self.base.prev_m1_timestamp is not None:
            gap_minutes = int(
                (
                    bar.timestamp
                    - self.base.prev_m1_timestamp
                ).total_seconds()
                // 60
            )
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
    dl = importlib.import_module("data_loader_v1")
    pf = importlib.import_module("preflight_v1")

    for p in (
        args.standards / "GUARDIAN_RESEARCH_PROTOCOL_V1.md",
        args.standards / "TRADE_OBSERVATION_SCHEMA_V1.json",
        args.standards / "guardian_observation_v1.py",
    ):
        if not p.is_file():
            raise dl.AdmissionError(f"missing research standard: {p}")

    rows = admitted_rows(dl, pf, args.manifest)

    preflight = {
        "status": "PREFLIGHT_ONLY",
        "id": ID,
        "days_including_warmup": len(rows),
        "warmup_start": WARMUP_START.isoformat(),
        "holdout_start": HOLDOUT_START.isoformat(),
        "holdout_end_exclusive": HOLDOUT_END_EXCLUSIVE.isoformat(),
        "ensembles": ENSEMBLES,
        "gate": GATE,
        "market_passes_planned": 1,
        "historical_2004_2016_opened_by_this_run": True,
        "development_2017_2025_reread": False,
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
        "status": "PREREGISTERED_BEFORE_HISTORICAL_HOLDOUT_PASS",
        "warmup_start": WARMUP_START.isoformat(),
        "holdout_start": HOLDOUT_START.isoformat(),
        "holdout_end_exclusive": HOLDOUT_END_EXCLUSIVE.isoformat(),
        "votes": {
            "SKEW_REV": (
                "hourly abs standardized skew of prior 24 completed "
                "M5 returns >=1.0; vote opposite skew sign"
            ),
            "TAIL_EXH": (
                "hourly robust tail counts beyond median +/-1.5*1.4826*MAD; "
                ">=4 tails and abs count imbalance>=3; vote opposite "
                "dominant tail side"
            ),
            "STRETCH_MR": (
                "hourly abs((last completed M5 close-causal EMA20)/ATR14_M5)"
                ">=2.0; vote toward EMA"
            ),
            "SCALE_CONFLICT": (
                "hourly subset of original A2_03: 105-minute slow move and "
                "last 15-minute move oppose; vote with slow move"
            ),
            "SEASONALITY": (
                "hourly rolling mean of prior 60 same-UTC-hour normalized "
                "returns, minimum 40; vote with mean sign"
            ),
        },
        "ensembles": ENSEMBLES,
        "exit": "uncapped TIME_H60 only",
        "control": "same event/timestamp exact opposite direction",
        "risk_definition": "1.5*WilderATR14_M5",
        "cost_semantics": (
            "legacy Atlas: subtract 0.10/0.20 absolute XAUUSD price units "
            "before dividing by risk"
        ),
        "multiple_testing": (
            "BH across the three historical-holdout primary ensemble tests"
        ),
        "gate": GATE,
        "historical_2004_2016_opened_by_this_run": True,
        "development_2017_2025_reread": False,
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
        lab = EnsembleLab(na, writer)

        for i, row in enumerate(rows, 1):
            for bar in dl.decode_dukascopy_m1_day(row):
                if not (
                    WARMUP_START
                    <= bar.timestamp
                    < HOLDOUT_END_EXCLUSIVE
                ):
                    raise dl.AdmissionError(
                        "bar outside Ensemble I historical hard wall"
                    )
                lab.on_m1(bar)

            if i % 100 == 0 or i == len(rows):
                print(json.dumps({
                    "status": "PROGRESS",
                    "days_done": i,
                    "days_total": len(rows),
                    "events": lab.events,
                    "signals": lab.base.signals,
                    "pending": len(lab.base.pending),
                    "date": row["date"],
                    "historical_2004_2016_opened": True,
                    "development_2017_2025_reread": False,
                    "protected_2026_opened": False,
                }, allow_nan=False), flush=True)

        discarded = len(lab.base.pending)
        lab.base.pending = []

    summary = {
        "schema": 1,
        "status": "COMPLETE",
        "id": ID,
        "events": lab.events,
        "signals_created": lab.base.signals,
        "signals_written": lab.base.written,
        "discarded_unfinished_at_end": discarded,
        "family_counts": lab.base.family_counts,
        "signals_csv_sha256": sha256_file(csvp),
        "engine_sha256": sha256_file(Path(__file__).resolve()),
        "dependency_sha256": hashes,
        "market_passes": 1,
        "holdout_start": HOLDOUT_START.isoformat(),
        "holdout_end_exclusive": HOLDOUT_END_EXCLUSIVE.isoformat(),
        "historical_2004_2016_opened": True,
        "development_2017_2025_reread": False,
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
