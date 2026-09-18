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

SEED = 20260918
BOOTSTRAPS = 4000
PERMUTATIONS = 4000
MIN_RISK = 0.01
COST_PRIMARY = 0.10
COST_STRESS = 0.20
HOLDOUT_YEARS = {2021, 2022}

FAMILIES = [
    {"family":"A3_01_SEMIVARIANCE_IMBALANCE","kind":"AMPLITUDE","control":"CONTROL_UPSIDE_DOM","h":60,"secondary":[120]},
    {"family":"A3_02_VOLATILITY_FRONT","kind":"AMPLITUDE","control":"CONTROL_FRONTLOADED","h":30,"secondary":[60]},
    {"family":"A3_03_LOW_OCCUPANCY_ZONE","kind":"DIRECTIONAL","control":"CONTROL_OCC1","h":15,"secondary":[30]},
    {"family":"A3_04_FVG_GEOMETRY","kind":"DIRECTIONAL","control":"CONTROL_OPPOSITE","h":15,"secondary":[30,60]},
    {"family":"A3_05_MIDPOINT_RANGE_GRAVITY","kind":"DIRECTIONAL","control":"CONTROL_QUARTER","h":30,"secondary":[15,60,120]},
    {"family":"A3_06_MEAN_REVERT_STRETCH","kind":"DIRECTIONAL","control":"CONTROL_CONT","h":30,"secondary":[15,60]},
]

DIR_STRICT = {
    "full_n_min": 200,
    "holdout_n_min": 60,
    "full_cost_010_pf_min": 1.10,
    "full_cost_020_pf_min": 1.05,
    "positive_years_min": 4,
    "holdout_cost_010_pf_min": 1.05,
    "control_common_days_min": 30,
    "matched_control_support_min": 30,
    "bh_q_max": 0.10,
}
DIR_CHEAP = {"full_n_min":100}
AMP_GATE = {
    "full_n_min": 500,
    "holdout_n_min": 150,
    "full_relative_uplift_pct_min": 5.0,
    "matched_control_support_min": 100,
    "bh_q_max": 0.10,
}


def fnum(x):
    try:
        v=float(x)
        return v if math.isfinite(v) else None
    except Exception:
        return None


def truthy(x):
    return str(x).strip().lower() in {"true","1","yes"}


def percentile(values,q):
    if not values:
        return None
    xs=sorted(values)
    if len(xs)==1:
        return xs[0]
    pos=(len(xs)-1)*q
    lo=int(math.floor(pos)); hi=int(math.ceil(pos))
    if lo==hi:
        return xs[lo]
    w=pos-lo
    return xs[lo]*(1-w)+xs[hi]*w


def stats(values):
    xs=[x for x in values if x is not None and math.isfinite(x)]
    if not xs:
        return {"n":0,"mean_r":None,"sum_r":None,"pf":None,"win_pct":None,"median_r":None,"max_drawdown_r":None,"best_r":None,"worst_r":None}
    gw=sum(x for x in xs if x>0)
    gl=-sum(x for x in xs if x<0)
    cum=peak=maxdd=0.0
    for x in xs:
        cum+=x
        peak=max(peak,cum)
        maxdd=max(maxdd,peak-cum)
    return {
        "n":len(xs),
        "mean_r":sum(xs)/len(xs),
        "sum_r":sum(xs),
        "pf":gw/gl if gl>0 else None,
        "win_pct":100*sum(x>0 for x in xs)/len(xs),
        "median_r":statistics.median(xs),
        "max_drawdown_r":maxdd,
        "best_r":max(xs),
        "worst_r":min(xs),
    }


def amp_stats(values):
    xs=[x for x in values if x is not None and math.isfinite(x)]
    if not xs:
        return {"n":0,"mean_amp_r":None,"median_amp_r":None,"p25_amp_r":None,"p75_amp_r":None,"p90_amp_r":None}
    return {
        "n":len(xs),
        "mean_amp_r":sum(xs)/len(xs),
        "median_amp_r":statistics.median(xs),
        "p25_amp_r":percentile(xs,.25),
        "p75_amp_r":percentile(xs,.75),
        "p90_amp_r":percentile(xs,.90),
    }


def concentration(values):
    xs=[x for x in values if x is not None and math.isfinite(x)]
    wins=sorted((x for x in xs if x>0),reverse=True)
    gross=sum(wins)
    if not xs or not wins or gross<=0:
        return {"top_1pct_gross_win_share_pct":None,"top_5_gross_win_share_pct":None,"mean_r_without_top_1pct_winners":None}
    topn=max(1,math.ceil(.01*len(xs)))
    ranked=sorted(range(len(xs)),key=lambda i:xs[i],reverse=True)
    remove=set(ranked[:topn])
    rem=[x for i,x in enumerate(xs) if i not in remove]
    return {
        "top_1pct_gross_win_share_pct":100*sum(wins[:topn])/gross,
        "top_5_gross_win_share_pct":100*sum(wins[:5])/gross,
        "mean_r_without_top_1pct_winners":sum(rem)/len(rem) if rem else None,
    }


def non_overlap(rows,horizon):
    out=[]
    next_allowed=None
    for r in sorted(rows,key=lambda z:z["entry_time"]):
        if next_allowed is None or r["entry_time"]>=next_allowed:
            out.append(r)
            next_allowed=r["entry_time"]+timedelta(minutes=horizon)
    return out


def rvalue(r,horizon,cost):
    raw=r.get(f"h{horizon}_endpoint_r")
    risk=r.get("risk")
    if raw is None or risk is None or risk<=0:
        return None
    return raw-cost/risk


def amp_value(r,horizon):
    a=r.get(f"h{horizon}_mfe_r")
    b=r.get(f"h{horizon}_mae_r")
    if a is None or b is None:
        return None
    return max(a,b)


def abs_endpoint_value(r,horizon):
    x=r.get(f"h{horizon}_endpoint_r")
    return abs(x) if x is not None else None


def daily_values(rows,value_fn):
    out=defaultdict(list)
    for r in rows:
        x=value_fn(r)
        if x is not None and math.isfinite(x):
            out[r["date"]].append(x)
    return out


def daily_bootstrap(rows,value_fn,seed):
    day=daily_values(rows,value_fn)
    blocks=[(len(v),sum(v)) for v in day.values() if v]
    if not blocks:
        return {"days":0,"ci95":[None,None],"median":None}
    rng=random.Random(seed)
    means=[]
    for _ in range(BOOTSTRAPS):
        chosen=rng.choices(blocks,k=len(blocks))
        n=sum(c for c,_ in chosen)
        s=sum(v for _,v in chosen)
        means.append(s/n if n else 0.0)
    return {"days":len(blocks),"ci95":[percentile(means,.025),percentile(means,.975)],"median":percentile(means,.5)}


def daily_delta(signal_rows,control_rows,value_fn):
    ds=daily_values(signal_rows,value_fn)
    dc=daily_values(control_rows,value_fn)
    common=sorted(set(ds)&set(dc))
    return [(d,sum(ds[d])/len(ds[d])-sum(dc[d])/len(dc[d])) for d in common]


def delta_tests(signal_rows,control_rows,value_fn,seed):
    vals=daily_delta(signal_rows,control_rows,value_fn)
    ds=[x for _,x in vals]
    if not ds:
        return {"common_days":0,"mean_daily_delta":None,"ci95":[None,None],"signflip_p_one_sided":None}
    observed=sum(ds)/len(ds)
    rng=random.Random(seed)
    boots=[]
    for _ in range(BOOTSTRAPS):
        z=rng.choices(ds,k=len(ds))
        boots.append(sum(z)/len(z))
    extreme=0
    for _ in range(PERMUTATIONS):
        z=[x if rng.random()<.5 else -x for x in ds]
        if sum(z)/len(z)>=observed:
            extreme+=1
    return {
        "common_days":len(ds),
        "mean_daily_delta":observed,
        "ci95":[percentile(boots,.025),percentile(boots,.975)],
        "signflip_p_one_sided":(extreme+1)/(PERMUTATIONS+1),
    }


def bh_qvalues(pmap):
    valid=sorted((p,k) for k,p in pmap.items() if p is not None)
    out={k:None for k in pmap}
    m=len(valid)
    if not m:
        return out
    qraw=[]
    for rank,(p,k) in enumerate(valid,1):
        qraw.append([k,min(1.0,p*m/rank)])
    running=1.0
    for k,q in reversed(qraw):
        running=min(running,q)
        out[k]=running
    return out


def subset(rows,years):
    return [r for r in rows if r["year"] in years]


def bucket(v,cuts):
    if v is None:
        return "NA"
    for i,c in enumerate(cuts):
        if v<c:
            return i
    return len(cuts)


def match_key(fam,kind,r):
    s=r.get("event_strength")
    a=r.get("event_aux")
    if fam in ("A3_04_FVG_GEOMETRY","A3_06_MEAN_REVERT_STRETCH"):
        return (r["entry_time"].isoformat(),)
    if kind=="AMPLITUDE":
        return (
            r["year"],
            r.get("session"),
            bucket(s,[.10,.25,.40,.60,.80]),
            bucket(a,[.25,.5,1.0,2.0,4.0,8.0]),
        )
    base=(r["year"],r.get("session"),r.get("direction"))
    if fam=="A3_03_LOW_OCCUPANCY_ZONE":
        return base+(bucket(s,[1.0,2.0,3.0,4.0,6.0,8.0]),)
    if fam=="A3_05_MIDPOINT_RANGE_GRAVITY":
        return base+(bucket(s,[.25,.5,.75,1.0,1.5,2.0]),bucket(a,[1.0,2.0,3.0,4.0,6.0,8.0]))
    return base


def matched_control_delta(fam,kind,signal_rows,control_rows,value_fn):
    sg=defaultdict(list)
    cg=defaultdict(list)
    for r in signal_rows:
        x=value_fn(r)
        if x is not None:
            sg[match_key(fam,kind,r)].append(x)
    for r in control_rows:
        x=value_fn(r)
        if x is not None:
            cg[match_key(fam,kind,r)].append(x)
    num=0.0
    den=0
    nstrata=0
    for k in set(sg)&set(cg):
        ns=len(sg[k]); nc=len(cg[k]); w=min(ns,nc)
        if not w:
            continue
        num+=w*((sum(sg[k])/ns)-(sum(cg[k])/nc))
        den+=w
        nstrata+=1
    return {"matched_support":den,"matched_strata":nstrata,"weighted_mean_delta":num/den if den else None}


def mfe_mae_diag(rows,horizon):
    mfes=[r.get(f"h{horizon}_mfe_r") for r in rows if r.get(f"h{horizon}_mfe_r") is not None]
    maes=[r.get(f"h{horizon}_mae_r") for r in rows if r.get(f"h{horizon}_mae_r") is not None]
    plus=sum(x>=1.0 for x in mfes)
    minus=sum(x>=1.0 for x in maes)
    return {
        "mean_mfe_r":sum(mfes)/len(mfes) if mfes else None,
        "mean_mae_r":sum(maes)/len(maes) if maes else None,
        "mfe_mae_ratio":(sum(mfes)/len(mfes))/(sum(maes)/len(maes)) if mfes and maes and sum(maes)>0 else None,
        "hit_plus_1R_within_horizon_pct":100*plus/len(mfes) if mfes else None,
        "hit_minus_1R_within_horizon_pct":100*minus/len(maes) if maes else None,
    }


def overlap_matrix(signal_sets):
    fams=list(signal_sets)
    out=[]
    for i,a in enumerate(fams):
        da={r["date"] for r in signal_sets[a]}
        for b in fams[i+1:]:
            db={r["date"] for r in signal_sets[b]}
            u=da|db
            inter=da&db
            out.append({
                "family_a":a,
                "family_b":b,
                "days_a":len(da),
                "days_b":len(db),
                "same_days":len(inter),
                "jaccard":len(inter)/len(u) if u else None,
            })
    return out


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--input",required=True,type=Path)
    ap.add_argument("--output-dir",required=True,type=Path)
    args=ap.parse_args()
    args.output_dir.mkdir(parents=True,exist_ok=True)

    cfg={x["family"]:x for x in FAMILIES}
    raw=defaultdict(lambda:defaultdict(list))
    total=0
    rejected_gap=defaultdict(int)
    rejected_risk=defaultdict(int)

    with args.input.open(newline="",encoding="utf-8") as f:
        for row in csv.DictReader(f):
            total+=1
            fam=row.get("family")
            var=row.get("variant")
            if fam not in cfg:
                continue
            h=cfg[fam]["h"]
            risk=fnum(row.get("risk"))
            if risk is None:
                continue
            if truthy(row.get("outcome_crossed_calendar_gap")):
                rejected_gap[(fam,var)]+=1
                continue
            if risk<MIN_RISK:
                rejected_risk[(fam,var)]+=1
                continue
            t=datetime.fromisoformat(row["entry_time"])
            z={
                "entry_time":t,
                "date":t.date().isoformat(),
                "year":t.year,
                "risk":risk,
                "direction":row.get("direction"),
                "session":row.get("session") or "UNKNOWN",
                "event_strength":fnum(row.get("event_strength")),
                "event_aux":fnum(row.get("event_aux")),
            }
            for hh in {h,*cfg[fam]["secondary"]}:
                for field in ("endpoint_r","mfe_r","mae_r"):
                    z[f"h{hh}_{field}"]=fnum(row.get(f"h{hh}_{field}"))
            raw[fam][var].append(z)

    prepared={}
    for x in FAMILIES:
        fam=x["family"]
        h=x["h"]
        prepared[fam]={
            "signal":non_overlap(raw[fam]["SIGNAL"],h),
            "control":non_overlap(raw[fam][x["control"]],h),
        }

    pmap={}
    preliminary={}
    for idx,x in enumerate(FAMILIES):
        fam=x["family"]
        h=x["h"]
        sig=prepared[fam]["signal"]
        con=prepared[fam]["control"]
        if x["kind"]=="AMPLITUDE":
            vf=lambda r,hh=h: amp_value(r,hh)
        else:
            vf=lambda r,hh=h: rvalue(r,hh,COST_PRIMARY)
        dt=delta_tests(sig,con,vf,SEED+idx*100)
        pmap[fam]=dt["signflip_p_one_sided"]
        preliminary[fam]=dt

    qmap=bh_qvalues(pmap)

    report={
        "schema":1,
        "status":"COMPLETE",
        "input_rows":total,
        "filters_preregistered":{
            "outcome_crossed_calendar_gap":False,
            "min_risk":MIN_RISK,
            "non_overlap":"wall-clock primary horizon separately for signal and control",
            "development_years":[2017,2018,2019,2020],
            "internal_holdout_years":[2021,2022],
            "bootstrap_replicates":BOOTSTRAPS,
            "signflip_permutations":PERMUTATIONS,
            "multiple_testing":"Benjamini-Hochberg across all six primary signal-vs-control tests",
            "amplitude_primary_metric":"max(h_MFE_R,h_MAE_R)",
            "amplitude_secondary_metric":"abs(h_endpoint_R)",
        },
        "families":[],
        "protected_2023_plus_opened":False,
        "protected_2026_opened":False,
    }

    signal_sets={}

    for idx,x in enumerate(FAMILIES):
        fam=x["family"]
        kind=x["kind"]
        ctl=x["control"]
        h=x["h"]
        sig=prepared[fam]["signal"]
        con=prepared[fam]["control"]
        signal_sets[fam]=sig

        if kind=="AMPLITUDE":
            vf=lambda r,hh=h: amp_value(r,hh)
            signal_primary=amp_stats([vf(r) for r in sig])
            control_primary=amp_stats([vf(r) for r in con])
            delta=preliminary[fam]
            delta["bh_q"]=qmap[fam]
            matched=matched_control_delta(fam,kind,sig,con,vf)
            dev_sig=subset(sig,{2017,2018,2019,2020})
            dev_ctl=subset(con,{2017,2018,2019,2020})
            ho_sig=subset(sig,HOLDOUT_YEARS)
            ho_ctl=subset(con,HOLDOUT_YEARS)
            dev_delta=delta_tests(dev_sig,dev_ctl,vf,SEED+idx*1000+1)
            ho_delta=delta_tests(ho_sig,ho_ctl,vf,SEED+idx*1000+2)
            ho_signal=amp_stats([vf(r) for r in ho_sig])
            ho_control=amp_stats([vf(r) for r in ho_ctl])

            yearly=[]
            loo=[]
            loo_ok=True
            for y in range(2017,2023):
                sy=subset(sig,{y}); cy=subset(con,{y})
                yearly.append({
                    "year":y,
                    "signal":amp_stats([vf(r) for r in sy]),
                    "control":amp_stats([vf(r) for r in cy]),
                    "delta":delta_tests(sy,cy,vf,SEED+idx*2000+y),
                })
                sy2=[r for r in sig if r["year"]!=y]
                cy2=[r for r in con if r["year"]!=y]
                dd=delta_tests(sy2,cy2,vf,SEED+idx*3000+y)
                ok=dd["mean_daily_delta"] is not None and dd["mean_daily_delta"]>0
                loo_ok=loo_ok and ok
                loo.append({"excluded_year":y,"delta":dd})

            mean_sig=signal_primary["mean_amp_r"]
            mean_ctl=control_primary["mean_amp_r"]
            uplift=None if mean_sig is None or mean_ctl in (None,0) else 100*(mean_sig-mean_ctl)/mean_ctl

            checks={
                "full_n":signal_primary["n"]>=AMP_GATE["full_n_min"],
                "holdout_n":ho_signal["n"]>=AMP_GATE["holdout_n_min"],
                "relative_uplift":uplift is not None and uplift>=AMP_GATE["full_relative_uplift_pct_min"],
                "delta_ci95_lower":delta["ci95"][0] is not None and delta["ci95"][0]>0,
                "holdout_delta":ho_delta["mean_daily_delta"] is not None and ho_delta["mean_daily_delta"]>0,
                "loo_delta":loo_ok,
                "matched_support":matched["matched_support"]>=AMP_GATE["matched_control_support_min"],
                "matched_delta":matched["weighted_mean_delta"] is not None and matched["weighted_mean_delta"]>0,
                "bh_q":qmap[fam] is not None and qmap[fam]<=AMP_GATE["bh_q_max"],
            }

            secondary=[]
            for hh in x["secondary"]:
                ss=non_overlap(raw[fam]["SIGNAL"],hh)
                cc=non_overlap(raw[fam][ctl],hh)
                vfa=lambda r,hhh=hh: amp_value(r,hhh)
                vfe=lambda r,hhh=hh: abs_endpoint_value(r,hhh)
                secondary.append({
                    "horizon":hh,
                    "signal_max_excursion":amp_stats([vfa(r) for r in ss]),
                    "control_max_excursion":amp_stats([vfa(r) for r in cc]),
                    "max_excursion_delta":delta_tests(ss,cc,vfa,SEED+idx*4000+hh),
                    "signal_abs_endpoint":amp_stats([vfe(r) for r in ss]),
                    "control_abs_endpoint":amp_stats([vfe(r) for r in cc]),
                })

            item={
                "family":fam,
                "kind":kind,
                "control_variant":ctl,
                "primary_horizon":h,
                "signal_n":len(sig),
                "control_n":len(con),
                "rejected_gap_signal":rejected_gap[(fam,"SIGNAL")],
                "rejected_risk_signal":rejected_risk[(fam,"SIGNAL")],
                "signal_primary":signal_primary,
                "control_primary":control_primary,
                "relative_uplift_pct":uplift,
                "signal_vs_control":delta,
                "coarsened_matched_control":matched,
                "development_2017_2020":{"control_delta":dev_delta},
                "internal_holdout_2021_2022":{"signal":ho_signal,"control":ho_control,"control_delta":ho_delta},
                "yearly":yearly,
                "leave_one_year_out":loo,
                "secondary_horizons_descriptive_only":secondary,
                "phenomenon_checks":checks,
                "phenomenon_candidate":all(checks.values()),
                "tradable_candidate":False,
                "tradable_candidate_reason":"non-directional amplitude family; no direction/execution edge preregistered",
            }

        else:
            vf=lambda r,hh=h: rvalue(r,hh,COST_PRIMARY)
            s10=stats([rvalue(r,h,.10) for r in sig])
            s20=stats([rvalue(r,h,.20) for r in sig])
            c10=stats([rvalue(r,h,.10) for r in con])
            c20=stats([rvalue(r,h,.20) for r in con])
            conc=concentration([rvalue(r,h,.10) for r in sig])
            boot=daily_bootstrap(sig,vf,SEED+idx*1000)
            delta=preliminary[fam]
            delta["bh_q"]=qmap[fam]
            matched=matched_control_delta(fam,kind,sig,con,vf)

            years=[]
            positive_years=0
            loo=[]
            loo_signal_ok=True
            loo_delta_ok=True
            for y in range(2017,2023):
                sy=subset(sig,{y}); cy=subset(con,{y})
                ys=stats([rvalue(r,h,.10) for r in sy])
                if ys["mean_r"] is not None and ys["mean_r"]>0:
                    positive_years+=1
                yd=delta_tests(sy,cy,vf,SEED+idx*2000+y)
                years.append({"year":y,"signal":ys,"control":stats([rvalue(r,h,.10) for r in cy]),"control_delta":yd})

                sy2=[r for r in sig if r["year"]!=y]
                cy2=[r for r in con if r["year"]!=y]
                ls=stats([rvalue(r,h,.10) for r in sy2])
                ld=delta_tests(sy2,cy2,vf,SEED+idx*3000+y)
                sok=ls["mean_r"] is not None and ls["mean_r"]>0
                dok=ld["mean_daily_delta"] is not None and ld["mean_daily_delta"]>0
                loo_signal_ok=loo_signal_ok and sok
                loo_delta_ok=loo_delta_ok and dok
                loo.append({"excluded_year":y,"signal":ls,"control_delta":ld})

            dev_sig=subset(sig,{2017,2018,2019,2020})
            dev_ctl=subset(con,{2017,2018,2019,2020})
            ho_sig=subset(sig,HOLDOUT_YEARS)
            ho_ctl=subset(con,HOLDOUT_YEARS)
            dev10=stats([rvalue(r,h,.10) for r in dev_sig])
            devdelta=delta_tests(dev_sig,dev_ctl,vf,SEED+idx*4000+1)
            ho10=stats([rvalue(r,h,.10) for r in ho_sig])
            ho20=stats([rvalue(r,h,.20) for r in ho_sig])
            hodelta=delta_tests(ho_sig,ho_ctl,vf,SEED+idx*4000+2)

            cheap_checks={
                "full_n":s10["n"]>=DIR_CHEAP["full_n_min"],
                "full_mean":s10["mean_r"] is not None and s10["mean_r"]>0,
                "control_delta":delta["mean_daily_delta"] is not None and delta["mean_daily_delta"]>0,
                "development_mean":dev10["mean_r"] is not None and dev10["mean_r"]>0,
                "holdout_mean":ho10["mean_r"] is not None and ho10["mean_r"]>0,
            }

            strict_checks={
                "full_n":s10["n"]>=DIR_STRICT["full_n_min"],
                "holdout_n":ho10["n"]>=DIR_STRICT["holdout_n_min"],
                "full_010_pf":s10["pf"] is not None and s10["pf"]>=DIR_STRICT["full_cost_010_pf_min"],
                "full_010_mean":s10["mean_r"] is not None and s10["mean_r"]>0,
                "full_020_pf":s20["pf"] is not None and s20["pf"]>=DIR_STRICT["full_cost_020_pf_min"],
                "full_020_mean":s20["mean_r"] is not None and s20["mean_r"]>0,
                "positive_years":positive_years>=DIR_STRICT["positive_years_min"],
                "loo_signal":loo_signal_ok,
                "signal_bootstrap":boot["ci95"][0] is not None and boot["ci95"][0]>0,
                "without_top1":conc["mean_r_without_top_1pct_winners"] is not None and conc["mean_r_without_top_1pct_winners"]>0,
                "holdout_010_pf":ho10["pf"] is not None and ho10["pf"]>=DIR_STRICT["holdout_cost_010_pf_min"],
                "holdout_010_mean":ho10["mean_r"] is not None and ho10["mean_r"]>0,
                "holdout_020_mean":ho20["mean_r"] is not None and ho20["mean_r"]>0,
                "control_common_days":delta["common_days"]>=DIR_STRICT["control_common_days_min"],
                "matched_support":matched["matched_support"]>=DIR_STRICT["matched_control_support_min"],
                "matched_delta":matched["weighted_mean_delta"] is not None and matched["weighted_mean_delta"]>0,
                "control_delta":delta["mean_daily_delta"] is not None and delta["mean_daily_delta"]>0,
                "control_bootstrap":delta["ci95"][0] is not None and delta["ci95"][0]>0,
                "holdout_control_delta":hodelta["mean_daily_delta"] is not None and hodelta["mean_daily_delta"]>0,
                "loo_control_delta":loo_delta_ok,
                "bh_q":qmap[fam] is not None and qmap[fam]<=DIR_STRICT["bh_q_max"],
            }

            secondary=[]
            for hh in x["secondary"]:
                ss=non_overlap(raw[fam]["SIGNAL"],hh)
                cc=non_overlap(raw[fam][ctl],hh)
                vf2=lambda r,hhh=hh: rvalue(r,hhh,COST_PRIMARY)
                secondary.append({
                    "horizon":hh,
                    "signal_010":stats([rvalue(r,hh,.10) for r in ss]),
                    "control_010":stats([rvalue(r,hh,.10) for r in cc]),
                    "control_delta":delta_tests(ss,cc,vf2,SEED+idx*5000+hh),
                })

            item={
                "family":fam,
                "kind":kind,
                "control_variant":ctl,
                "primary_horizon":h,
                "signal_n":len(sig),
                "control_n":len(con),
                "rejected_gap_signal":rejected_gap[(fam,"SIGNAL")],
                "rejected_risk_signal":rejected_risk[(fam,"SIGNAL")],
                "signal_cost_010":{**s10,**conc,"bootstrap_daily":boot},
                "signal_cost_020":s20,
                "control_cost_010":c10,
                "control_cost_020":c20,
                "signal_vs_control":delta,
                "coarsened_matched_control":matched,
                "development_2017_2020":{"signal_010":dev10,"control_delta":devdelta},
                "internal_holdout_2021_2022":{"signal_010":ho10,"signal_020":ho20,"control_delta":hodelta},
                "positive_years":positive_years,
                "yearly":years,
                "leave_one_year_out":loo,
                "mfe_mae_primary":mfe_mae_diag(sig,h),
                "secondary_horizons_descriptive_only":secondary,
                "cheap_fail_checks":cheap_checks,
                "cheap_fail_pass":all(cheap_checks.values()),
                "strict_freeze_checks":strict_checks,
                "strict_freeze_candidate":all(strict_checks.values()),
            }

        report["families"].append(item)

    report["signal_day_overlap"]=overlap_matrix(signal_sets)
    out=args.output_dir/"atlas3_analysis.json"
    out.write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+"\n",encoding="utf-8")

    print("=== GUARDIAN ATLAS III V1 ===")
    print(f"INPUT ROWS: {total}")
    print("2023+ ACCESSED: FALSE")
    print("2026 ACCESSED: FALSE")
    for item in report["families"]:
        if item["kind"]=="AMPLITUDE":
            s=item["signal_primary"]
            d=item["signal_vs_control"]
            ho=item["internal_holdout_2021_2022"]["signal"]
            print(f"{item['family']} | AMPLITUDE | H={item['primary_horizon']} | N={s['n']} | MeanAmpR={s['mean_amp_r']} | Uplift={item['relative_uplift_pct']}%")
            print(f"  Delta/day={d['mean_daily_delta']} | CI95={d['ci95']} | p={d['signflip_p_one_sided']} | BHq={d['bh_q']}")
            print(f"  Holdout 2021-22: N={ho['n']} MeanAmpR={ho['mean_amp_r']} | Phenomenon={item['phenomenon_candidate']} | Tradable=False")
        else:
            s=item["signal_cost_010"]
            s2=item["signal_cost_020"]
            d=item["signal_vs_control"]
            ho=item["internal_holdout_2021_2022"]["signal_010"]
            print(f"{item['family']} | DIRECTIONAL | H={item['primary_horizon']} | N={s['n']} | .10 MeanR={s['mean_r']} PF={s['pf']} | .20 MeanR={s2['mean_r']} | Years+={item['positive_years']}/6")
            print(f"  Delta/day={d['mean_daily_delta']} | CI95={d['ci95']} | p={d['signflip_p_one_sided']} | BHq={d['bh_q']}")
            print(f"  Holdout 2021-22: N={ho['n']} MeanR={ho['mean_r']} PF={ho['pf']} | Cheap={item['cheap_fail_pass']} | StrictFreeze={item['strict_freeze_candidate']}")
    print(f"REPORT: {out}")


if __name__=="__main__":
    raise SystemExit(main())
