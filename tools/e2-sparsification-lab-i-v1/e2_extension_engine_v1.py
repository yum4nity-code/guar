#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

UTC = timezone.utc
ID = "GUARDIAN-E2-SPARSIFICATION-LAB-I-V1"

WARMUP_START = datetime(2016, 8, 1, tzinfo=UTC)
DEV_START = datetime(2017, 1, 1, tzinfo=UTC)
DEV_END_EXCLUSIVE = datetime(2026, 1, 1, tzinfo=UTC)

EXPECTED = {
    "data_loader_v1.py": "9129e1d48ce5b05ef997e9fa12b6e55a674c2be052e69ed94bc3fb4c235b2f6b",
    "preflight_v1.py": "d0e799d4a999683c4c1a46495b56a6720d40ab61058ffe2d36a1456ab69cd354",
    "night_atlas_v1.py": "edf95e275cdd49846fed81c06d180b5190e63c25de347137136b94e0f3674860",
    "ensemble_i_engine_v1.py": "defe36eb894789697d71951552b0d5df26b90e6bdee7064db4fab6bac28e9ed5",
}

ENSEMBLE_ENGINE_GIT_BLOB = "1f780087128b27732368be2aec5515d1bbf7406a"


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
        raise dl.AdmissionError("E2 sparsification requires XAUUSD UTC M1")

    rows = dl.load_dukascopy_index(
        Path(duka["payload_index"]),
        duka["payload_index_sha256"],
    )

    out = []
    for row in rows:
        day = datetime.fromisoformat(row["date"]).replace(tzinfo=UTC)
        if WARMUP_START <= day < DEV_END_EXCLUSIVE:
            out.append(row)

    if not out:
        raise dl.AdmissionError("no rows admitted for 2016-08 through 2025")

    return out


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
    e1 = importlib.import_module("ensemble_i_engine_v1")

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
        "warmup_start": WARMUP_START.isoformat(),
        "development_start": DEV_START.isoformat(),
        "development_end_exclusive": DEV_END_EXCLUSIVE.isoformat(),
        "days_including_warmup": len(rows),
        "source_vote_engine_blob": ENSEMBLE_ENGINE_GIT_BLOB,
        "family_emitted": "E2_DIVERSE_SCORE_5 only",
        "market_passes_planned": 1,
        "historical_2005_2016_reread": False,
        "development_2017_2025_will_open_on_execute": True,
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
        "status": "PREREGISTERED_BEFORE_2017_2025_EXTENSION_PASS",
        "source_vote_engine_blob": ENSEMBLE_ENGINE_GIT_BLOB,
        "warmup_start": WARMUP_START.isoformat(),
        "development_start": DEV_START.isoformat(),
        "development_end_exclusive": DEV_END_EXCLUSIVE.isoformat(),
        "family_emitted": "E2_DIVERSE_SCORE_5 only",
        "event_strength_semantics": "absolute equal-weight vote sum",
        "event_aux_semantics": "number of active component votes",
        "exit": "TIME_H60 inherited from Night Atlas outcome recording",
        "historical_2005_2016_reread": False,
        "development_2017_2025_opened": True,
        "protected_2026_opened": False,
        "engine_sha256": sha256_file(Path(__file__).resolve()),
        "dependency_sha256": hashes,
    }
    (args.output_dir / "preregistered_spec.json").write_text(
        json.dumps(spec, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    class E2OnlyLab(e1.EnsembleLab):
        def add(self, t, entry, side, family, strength, aux):
            if family != "E2_DIVERSE_SCORE_5":
                return

            if not (
                DEV_START
                <= t
                < DEV_END_EXCLUSIVE - na.timedelta(minutes=na.MAX_HORIZON_MIN)
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
            row["protected_2023_plus_opened"] = True
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
            ctl["protected_2023_plus_opened"] = True
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

    csvp = args.output_dir / "signals_2017_2025.csv"

    with csvp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=na.FIELDNAMES)
        writer.writeheader()
        lab = E2OnlyLab(na, writer)

        for i, row in enumerate(rows, 1):
            for bar in dl.decode_dukascopy_m1_day(row):
                if not (
                    WARMUP_START <= bar.timestamp < DEV_END_EXCLUSIVE
                ):
                    raise dl.AdmissionError(
                        "bar outside E2 sparsification extension hard wall"
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
                    "historical_2005_2016_reread": False,
                    "development_2017_2025_opened": True,
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
        "historical_2005_2016_reread": False,
        "development_start": DEV_START.isoformat(),
        "development_end_exclusive": DEV_END_EXCLUSIVE.isoformat(),
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
