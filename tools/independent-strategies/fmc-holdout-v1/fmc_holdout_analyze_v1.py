#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ID="FMC-HOLDOUT-2021-2022-V1"
PRIMARY_H=30
HORIZONS=(15,30,60)
SEED=20260918
BOOTSTRAPS=4000
PERMUTATIONS=4000
COSTS=(0.00,0.05,0.10,0.15,0.20)

def fnum(x):
    try:
        v=float(x)
        return v if math.isfinite(v) else None
    except Exception:
        return None

def truthy(x):
    return str(x).strip().lower() in {"true","1","yes"}

def pct(xs,q):
    if not xs:
        return None
    a=sorted(xs)
    if len(a)==1:
        return a[0]
    pos=(len(a)-1)*q
    lo=int(math.floor(pos)); hi=int(math.ceil(pos))
    if lo==hi:
        return a[lo]
    w=pos-lo
    return a[lo]*(1-w)+a[hi]*w

def non_overlap(rows,h=PRIMARY_H):
    out=[]
    nxt=None
    for r in sorted(rows,key=lambda z:z["entry_time"]):
        if nxt is None or r["entry_time"]>=nxt:
            out.append(r)
            nxt=r["entry_time"]+timedelta(minutes=h)
    return out

def value(r,h,cost):
    x=r.get(f"h{h}_endpoint_r")
    return None if x is None else x-cost

def stats(xs):
    xs=[x for x in xs if x is not None and math.isfinite(x)]
    if not xs:
        return {"n":0,"mean_r":None,"median_r":None,"pf":None,"win_pct":None,"sum_r":None,"max_drawdown_r":None}
    gw=sum(x for x in xs if x>0)
    gl=-sum(x for x in xs if x<0)
    cum=peak=dd=0.0
    for x in xs:
        cum+=x
        peak=max(peak,cum)
        dd=max(dd,peak-cum)
    return {
        "n":len(xs),
        "mean_r":sum(xs)/len(xs),
        "median_r":statistics.median(xs),
        "pf":gw/gl if gl>0 else None,
        "win_pct":100.0*sum(x>0 for x in xs)/len(xs),
        "sum_r":sum(xs),
        "max_drawdown_r":dd,
    }

def daily(rows,vf):
    d=defaultdict(list)
    for r in rows:
        x=vf(r)
        if x is not None and math.isfinite(x):
            d[r["date"]].append(x)
    return d

def daily_delta(sig,ctl,vf,seed):
    a=daily(sig,vf); b=daily(ctl,vf)
    common=sorted(set(a)&set(b))
    ds=[sum(a[d])/len(a[d])-sum(b[d])/len(b[d]) for d in common]
    if not ds:
        return {"common_days":0,"mean_daily_delta_r":None,"ci95":[None,None],"signflip_p_one_sided":None}
    obs=sum(ds)/len(ds)
    rng=random.Random(seed)
    boots=[]
    for _ in range(BOOTSTRAPS):
        z=rng.choices(ds,k=len(ds))
        boots.append(sum(z)/len(z))
    extreme=0
    for _ in range(PERMUTATIONS):
        z=[x if rng.random()<.5 else -x for x in ds]
        if sum(z)/len(z)>=obs:
            extreme+=1
    return {
        "common_days":len(ds),
        "mean_daily_delta_r":obs,
        "ci95":[pct(boots,.025),pct(boots,.975)],
        "signflip_p_one_sided":(extreme+1)/(PERMUTATIONS+1),
    }

def bootstrap_signal(rows,vf,seed):
    d=daily(rows,vf)
    blocks=[(len(v),sum(v)) for v in d.values() if v]
    if not blocks:
        return {"days":0,"ci95":[None,None],"median":None}
    rng=random.Random(seed)
    vals=[]
    for _ in range(BOOTSTRAPS):
        z=rng.choices(blocks,k=len(blocks))
        n=sum(a for a,_ in z)
        s=sum(b for _,b in z)
        vals.append(s/n if n else 0.0)
    return {"days":len(blocks),"ci95":[pct(vals,.025),pct(vals,.975)],"median":pct(vals,.5)}

def excursion(rows,h):
    mfes=[r.get(f"h{h}_mfe_r") for r in rows if r.get(f"h{h}_mfe_r") is not None]
    maes=[r.get(f"h{h}_mae_r") for r in rows if r.get(f"h{h}_mae_r") is not None]
    mmfe=sum(mfes)/len(mfes) if mfes else None
    mmae=sum(maes)/len(maes) if maes else None
    return {
        "mean_mfe_r":mmfe,
        "mean_mae_r":mmae,
        "mfe_mae_ratio_of_means":mmfe/mmae if mmfe is not None and mmae not in (None,0) else None,
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--input",required=True,type=Path)
    ap.add_argument("--output-dir",required=True,type=Path)
    args=ap.parse_args()
    args.output_dir.mkdir(parents=True,exist_ok=True)

    raw=defaultdict(list)
    input_rows=0
    rejected_gap=defaultdict(int)

    with args.input.open(newline="",encoding="utf-8") as f:
        for row in csv.DictReader(f):
            input_rows+=1
            if row.get("family")!="FMC":
                continue
            var=row.get("variant")
            if truthy(row.get("outcome_crossed_calendar_gap")):
                rejected_gap[var]+=1
                continue
            t=datetime.fromisoformat(row["entry_time"])
            if t.year not in {2021,2022}:
                raise RuntimeError(f"row outside frozen FMC holdout: {t.isoformat()}")
            z={
                "entry_time":t,
                "date":t.date().isoformat(),
                "year":t.year,
                "session":row.get("session"),
                "direction":row.get("direction"),
                "risk":fnum(row.get("risk")),
                "stretch":fnum(row.get("stretch")),
            }
            for h in HORIZONS:
                z[f"h{h}_endpoint_r"]=fnum(row.get(f"h{h}_endpoint_r"))
                z[f"h{h}_mfe_r"]=fnum(row.get(f"h{h}_mfe_r"))
                z[f"h{h}_mae_r"]=fnum(row.get(f"h{h}_mae_r"))
            raw[var].append(z)

    sig=non_overlap(raw["SIGNAL"])
    ctl=non_overlap(raw["CONTROL_OFF_WINDOW"])

    primary={}
    for c in COSTS:
        key=f"cost_{str(c).replace('.','p')}"
        primary[key]=stats([value(r,PRIMARY_H,c) for r in sig])

    control010=stats([value(r,PRIMARY_H,.10) for r in ctl])
    vf=lambda r:value(r,PRIMARY_H,.10)
    d=daily_delta(sig,ctl,vf,SEED)
    boot=bootstrap_signal(sig,vf,SEED+1)
    ex=excursion(sig,PRIMARY_H)

    secondary=[]
    for h in (15,60):
        ss=non_overlap(raw["SIGNAL"],h)
        cc=non_overlap(raw["CONTROL_OFF_WINDOW"],h)
        vf2=lambda r,hh=h:value(r,hh,.10)
        secondary.append({
            "horizon":h,
            "signal_cost_010":stats([value(r,h,.10) for r in ss]),
            "control_cost_010":stats([value(r,h,.10) for r in cc]),
            "daily_delta_cost_010":daily_delta(ss,cc,vf2,SEED+h),
            "signal_excursion":excursion(ss,h),
        })

    yearly=[]
    for y in (2021,2022):
        sy=[r for r in sig if r["year"]==y]
        cy=[r for r in ctl if r["year"]==y]
        yearly.append({
            "year":y,
            "signal_cost_010":stats([value(r,PRIMARY_H,.10) for r in sy]),
            "control_cost_010":stats([value(r,PRIMARY_H,.10) for r in cy]),
            "daily_delta_cost_010":daily_delta(sy,cy,vf,SEED+y),
            "signal_excursion":excursion(sy,PRIMARY_H),
        })

    mean010=primary["cost_0p1"]["mean_r"]
    ratio=ex["mfe_mae_ratio_of_means"]
    checks={
        "mean_r_cost_0p10_gt_0":mean010 is not None and mean010>0,
        "daily_delta_ge_0p06":d["mean_daily_delta_r"] is not None and d["mean_daily_delta_r"]>=.06,
        "p_lt_0p01":d["signflip_p_one_sided"] is not None and d["signflip_p_one_sided"]<.01,
        "mfe_mae_ratio_gt_1p25":ratio is not None and ratio>1.25,
    }
    passed=all(checks.values())

    report={
        "schema":1,
        "status":"COMPLETE",
        "id":ID,
        "scope":"FROZEN_2021_2022_ONE_SHOT",
        "input_rows":input_rows,
        "signal_n_after_nonoverlap":len(sig),
        "control_n_after_nonoverlap":len(ctl),
        "primary_horizon_min":PRIMARY_H,
        "signal_primary":primary,
        "control_cost_010":control010,
        "signal_vs_control_cost_010":d,
        "signal_bootstrap_cost_010":boot,
        "signal_excursion_h30":ex,
        "yearly":yearly,
        "secondary_horizons_descriptive_only":secondary,
        "rejected_gap":{"signal":rejected_gap["SIGNAL"],"control":rejected_gap["CONTROL_OFF_WINDOW"]},
        "sieve":{"checks":checks,"holdout_pass":passed,"hard_kill_mean_nonpositive":mean010 is not None and mean010<=0},
        "protected_2023_plus_opened":False,
        "protected_2026_opened":False,
    }

    out=args.output_dir/"fmc_holdout_analysis.json"
    out.write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+"\n",encoding="utf-8")

    s0=primary["cost_0p0"]; s10=primary["cost_0p1"]; s20=primary["cost_0p2"]
    print("=== FMC HOLDOUT 2021-2022 V1 ===")
    print("2023+ ACCESSED: FALSE")
    print("2026 ACCESSED: FALSE")
    print(f"SIGNAL | N={s10['n']} | Gross MeanR={s0['mean_r']} PF={s0['pf']} | .10 MeanR={s10['mean_r']} PF={s10['pf']} | .20 MeanR={s20['mean_r']} PF={s20['pf']}")
    print(f"CONTROL .10 | N={control010['n']} | MeanR={control010['mean_r']} PF={control010['pf']}")
    print(f"DELTA .10/day={d['mean_daily_delta_r']} | CI95={d['ci95']} | p={d['signflip_p_one_sided']}")
    print(f"MFE={ex['mean_mfe_r']} | MAE={ex['mean_mae_r']} | MFE/MAE={ratio}")
    print(f"CHECKS={checks}")
    print(f"HOLDOUT_PASS={passed}")
    print(f"REPORT: {out}")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
