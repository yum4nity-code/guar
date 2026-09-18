#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

UTC = timezone.utc
ID = "FMC-HOLDOUT-2021-2022-V1"
WARMUP_START = datetime(2020, 11, 1, tzinfo=UTC)
TEST_START = datetime(2021, 1, 1, tzinfo=UTC)
TEST_END_EXCLUSIVE = datetime(2023, 1, 1, tzinfo=UTC)
HORIZONS = (15, 30, 60)
PRIMARY_HORIZON = 30

EXPECTED = {
    "data_loader_v1.py": "9129e1d48ce5b05ef997e9fa12b6e55a674c2be052e69ed94bc3fb4c235b2f6b",
    "preflight_v1.py": "d0e799d4a999683c4c1a46495b56a6720d40ab61058ffe2d36a1456ab69cd354",
    "night_atlas_v1.py": "edf95e275cdd49846fed81c06d180b5190e63c25de347137136b94e0f3674860",
}

FIELDS = [
    "job_id","family","variant","entry_time","year","session","direction","side",
    "entry","risk","stretch","ema20","atr14_m5",
    "outcome_crossed_calendar_gap","outcome_max_gap_minutes",
    "h15_endpoint_r","h15_mfe_r","h15_mae_r",
    "h30_endpoint_r","h30_mfe_r","h30_mae_r",
    "h60_endpoint_r","h60_mfe_r","h60_mae_r",
]

SPEC = {
    "schema": 1,
    "id": ID,
    "status": "PREREGISTERED_BEFORE_HOLDOUT_PASS",
    "hypothesis": "Extreme stretch at exact fixing-extinction timestamps persists in the direction of the stretch.",
    "test_start": TEST_START.isoformat(),
    "test_end_exclusive": TEST_END_EXCLUSIVE.isoformat(),
    "warmup_start": WARMUP_START.isoformat(),
    "signal_times_utc": ["13:35","14:35","16:05"],
    "stretch": "abs(Close_M5-EMA20_M5)/ATR14_M5 >= 2.0",
    "direction": "continuation: LONG if Close>EMA20, SHORT if Close<EMA20",
    "control": "same stretch outside inclusive windows 13:30..13:45, 14:30..14:45, 16:00..16:15 UTC",
    "entry": "completed M5 close at detection timestamp",
    "risk": "1.5*ATR14_M5 = 1R normalization",
    "horizons_min": [15,30,60],
    "primary_horizon_min": 30,
    "exit": "fixed endpoint close at each horizon; no TP/SL/trailing",
    "costs_r": [0.00,0.05,0.10,0.15,0.20],
    "non_overlap": "30 wall-clock minutes, separately signal/control after gap filtering",
    "pass_gate": {
        "mean_r_cost_0p10_gt": 0.0,
        "daily_delta_cost_0p10_min": 0.06,
        "daily_delta_p_one_sided_lt": 0.01,
        "mfe_mae_ratio_of_means_h30_gt": 1.25,
    },
    "protected_2023_plus_opened": False,
    "protected_2026_opened": False,
}

@dataclass
class Pending:
    row: dict
    side: int
    entry: float
    risk: float
    age: int = 0
    mfe: float = 0.0
    mae: float = 0.0

def sha256_file(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""):
            h.update(chunk)
    return h.hexdigest()

def verify_local(campaign: Path):
    got={}
    for name,expected in EXPECTED.items():
        p=campaign/name
        if not p.is_file():
            raise RuntimeError(f"missing dependency: {p}")
        actual=sha256_file(p)
        got[name]=actual
        if actual!=expected:
            raise RuntimeError(f"hash mismatch {name}: expected {expected}, got {actual}")
    return got

def sign(x):
    return 1 if x>0 else -1 if x<0 else 0

class FMC:
    def __init__(self,na,writer):
        self.na=na
        self.base=na.Atlas(writer)
        self.writer=writer
        self.pending=[]
        self.ema20=None
        self.alpha=2.0/21.0
        self.signals=0
        self.written=0
        self.counts={}

    @staticmethod
    def excluded_control_window(t):
        m=t.hour*60+t.minute
        windows=((13*60+30,13*60+45),(14*60+30,14*60+45),(16*60,16*60+15))
        return any(a<=m<=b for a,b in windows)

    def add(self,t,entry,side,variant,stretch,atr):
        if not (TEST_START<=t<TEST_END_EXCLUSIVE):
            return
        risk=1.5*atr
        row={k:None for k in FIELDS}
        row.update({
            "job_id":ID,
            "family":"FMC",
            "variant":variant,
            "entry_time":t.isoformat(),
            "year":t.year,
            "session":self.base.session(t.hour),
            "direction":"LONG" if side>0 else "SHORT",
            "side":side,
            "entry":entry,
            "risk":risk,
            "stretch":stretch,
            "ema20":self.ema20,
            "atr14_m5":atr,
            "outcome_crossed_calendar_gap":False,
            "outcome_max_gap_minutes":0,
        })
        self.pending.append(Pending(row,side,entry,risk))
        self.signals+=1
        self.counts[variant]=self.counts.get(variant,0)+1

    def detect(self,t):
        hist=list(self.base.m5_hist)
        if len(hist)<193 or self.ema20 is None or self.base.atr5.value is None:
            return
        atr=float(self.base.atr5.value)
        if atr<=0:
            return
        sb=hist[-1]
        if sb.available_at is not None and sb.available_at>t:
            raise self.na.AdmissionError("lookahead: M5 unavailable")
        if self.base.atr5.available_at is not None and self.base.atr5.available_at>t:
            raise self.na.AdmissionError("lookahead: ATR unavailable")

        signed=(sb.close-self.ema20)/atr
        stretch=abs(signed)
        if stretch<2.0 or signed==0:
            return

        side=sign(signed)
        hm=(t.hour,t.minute)
        entry=sb.close
        if hm in {(13,35),(14,35),(16,5)}:
            self.add(t,entry,side,"SIGNAL",stretch,atr)
        elif not self.excluded_control_window(t):
            self.add(t,entry,side,"CONTROL_OFF_WINDOW",stretch,atr)

    def process_pending(self,bar):
        keep=[]
        for p in self.pending:
            p.age+=1
            fav=max((bar.high-p.entry) if p.side>0 else (p.entry-bar.low),0.0)
            adv=max((p.entry-bar.low) if p.side>0 else (bar.high-p.entry),0.0)
            p.mfe=max(p.mfe,fav)
            p.mae=max(p.mae,adv)

            if p.age in HORIZONS:
                h=p.age
                pnl=p.side*(bar.close-p.entry)
                p.row[f"h{h}_endpoint_r"]=pnl/p.risk
                p.row[f"h{h}_mfe_r"]=p.mfe/p.risk
                p.row[f"h{h}_mae_r"]=p.mae/p.risk

            if p.age>=max(HORIZONS):
                self.writer.writerow(p.row)
                self.written+=1
            else:
                keep.append(p)
        self.pending=keep

    def after_m5(self,b):
        self.base.after_m5(b)
        if self.ema20 is None:
            self.ema20=b.close
        else:
            self.ema20=self.alpha*b.close+(1.0-self.alpha)*self.ema20

    def on_m1(self,bar):
        self.base.roll_day(bar)

        prev=self.base.prev_m1_timestamp
        if prev is not None:
            gap_minutes=int((bar.timestamp-prev).total_seconds()//60)
            if gap_minutes>1:
                for p in self.pending:
                    p.row["outcome_crossed_calendar_gap"]=True
                    p.row["outcome_max_gap_minutes"]=max(
                        int(p.row.get("outcome_max_gap_minutes") or 0),
                        gap_minutes-1
                    )

        if bar.timestamp.minute%5==0:
            self.detect(bar.timestamp)

        self.process_pending(bar)
        self.base.rsi1.update(bar.close,bar.available_at)
        self.base.atr1.update(bar)

        b5=self.base.agg5.push(bar)
        if b5:
            self.after_m5(b5)

        b15=self.base.agg15.push(bar)
        if b15:
            self.base.rsi15.update(b15.close,b15.available_at)
            self.base.atr15.update(b15)

        self.base.update_day(bar)
        self.base.prev_m1_timestamp=bar.timestamp

def admitted_rows(na,manifest):
    for row in na.admitted_rows(manifest):
        d=datetime.fromisoformat(row["date"]).replace(tzinfo=UTC)
        if WARMUP_START<=d<TEST_END_EXCLUSIVE:
            yield row

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--manifest",required=True,type=Path)
    ap.add_argument("--standards",required=True,type=Path)
    ap.add_argument("--output-dir",required=True,type=Path)
    ap.add_argument("--execute",action="store_true")
    args=ap.parse_args()

    campaign=Path(__file__).resolve().parent
    hashes=verify_local(campaign)
    sys.path.insert(0,str(campaign))
    na=importlib.import_module("night_atlas_v1")

    rows=list(admitted_rows(na,args.manifest))
    pf={
        "status":"PREFLIGHT_ONLY",
        "id":ID,
        "days_including_warmup":len(rows),
        "test_start":TEST_START.isoformat(),
        "test_end_exclusive":TEST_END_EXCLUSIVE.isoformat(),
        "dependency_sha256":hashes,
        "protected_2023_plus_opened":False,
        "protected_2026_opened":False,
    }
    if not args.execute:
        print(json.dumps(pf,indent=2,sort_keys=True))
        return 0

    if args.output_dir.exists():
        raise na.AdmissionError(f"refusing overwrite: {args.output_dir}")
    args.output_dir.mkdir(parents=True)

    spec=dict(SPEC)
    spec["engine_sha256"]=sha256_file(Path(__file__).resolve())
    spec["dependency_sha256"]=hashes
    (args.output_dir/"preregistered_spec.json").write_text(
        json.dumps(spec,indent=2,sort_keys=True)+"\n",encoding="utf-8"
    )

    csvp=args.output_dir/"signals.csv"
    with csvp.open("w",newline="",encoding="utf-8") as f:
        writer=csv.DictWriter(f,fieldnames=FIELDS)
        writer.writeheader()
        lab=FMC(na,writer)

        for i,row in enumerate(rows,1):
            for bar in na.decode_dukascopy_m1_day(row):
                if not (WARMUP_START<=bar.timestamp<TEST_END_EXCLUSIVE):
                    raise na.AdmissionError("bar outside FMC hard wall")
                lab.on_m1(bar)
            if i%100==0 or i==len(rows):
                print(json.dumps({
                    "status":"PROGRESS","days_done":i,"days_total":len(rows),
                    "signals":lab.signals,"pending":len(lab.pending),"date":row["date"],
                    "protected_2023_plus_opened":False,"protected_2026_opened":False
                }),flush=True)

        discarded=len(lab.pending)
        lab.pending=[]

    summary={
        "schema":1,"status":"COMPLETE","id":ID,
        "signals_created":lab.signals,"signals_written":lab.written,
        "discarded_unfinished_at_end":discarded,"variant_counts":lab.counts,
        "signals_csv_sha256":sha256_file(csvp),
        "engine_sha256":sha256_file(Path(__file__).resolve()),
        "dependency_sha256":hashes,
        "test_start":TEST_START.isoformat(),
        "test_end_exclusive":TEST_END_EXCLUSIVE.isoformat(),
        "protected_2023_plus_opened":False,
        "protected_2026_opened":False,
    }
    (args.output_dir/"engine_summary.json").write_text(
        json.dumps(summary,indent=2,sort_keys=True)+"\n",encoding="utf-8"
    )
    print(json.dumps(summary))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
