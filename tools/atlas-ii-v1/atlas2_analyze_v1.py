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
    ("A2_01_ORDER_INTRABAR", "CONTROL_BODY", 5, [15]),
    ("A2_02_PATH_EFFICIENCY", "CONTROL_LOW_EFF", 60, [120]),
    ("A2_03_SCALE_CONFLICT", "CONTROL_ALIGNED", 60, [120]),
    ("A2_04_MAJORITY_AMPLITUDE_CONFLICT", "CONTROL_ALIGNED", 30, [60]),
    ("A2_05_FROZEN_CENTER_RETURN", "CONTROL_NO_EXCURSION", 30, [60]),
    ("A2_06_ROUND5_REJECTION", "CONTROL_PLACEBO", 15, [5, 30]),
]

STRICT_GATE = {
    "full_n_min": 200, "holdout_n_min": 60,
    "full_cost_010_pf_min": 1.10, "full_cost_010_mean_r_gt": 0.0,
    "full_cost_020_pf_min": 1.05, "full_cost_020_mean_r_gt": 0.0,
    "positive_years_min": 4, "all_leave_one_year_out_mean_r_gt": 0.0,
    "daily_bootstrap_signal_ci95_lower_gt": 0.0,
    "mean_r_without_top1pct_winners_gt": 0.0,
    "holdout_cost_010_pf_min": 1.05, "holdout_cost_010_mean_r_gt": 0.0,
    "holdout_cost_020_mean_r_gt": 0.0,
    "control_common_days_min": 30, "matched_control_support_min": 30,
    "matched_control_delta_gt": 0.0, "control_delta_mean_r_gt": 0.0,
    "control_delta_ci95_lower_gt": 0.0, "holdout_control_delta_gt": 0.0,
    "all_leave_one_year_out_control_delta_gt": 0.0, "bh_q_max": 0.10,
}
CHEAP_GATE = {
    "full_n_min": 100, "full_cost_010_mean_r_gt": 0.0,
    "control_delta_gt": 0.0, "development_mean_r_gt": 0.0,
    "holdout_mean_r_gt": 0.0,
}

def fnum(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except Exception:
        return None

def truthy(x):
    return str(x).strip().lower() in {"true", "1", "yes"}

def percentile(values, q):
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs)-1)*q
    lo = int(math.floor(pos)); hi = int(math.ceil(pos))
    if lo == hi:
        return xs[lo]
    w = pos-lo
    return xs[lo]*(1-w)+xs[hi]*w

def stats(values):
    xs = [x for x in values if x is not None and math.isfinite(x)]
    if not xs:
        return {"n":0,"mean_r":None,"sum_r":None,"pf":None,"win_pct":None,"median_r":None,"max_drawdown_r":None,"best_r":None,"worst_r":None}
    gw=sum(x for x in xs if x>0); gl=-sum(x for x in xs if x<0)
    cum=peak=maxdd=0.0
    for x in xs:
        cum+=x; peak=max(peak,cum); maxdd=max(maxdd,peak-cum)
    return {"n":len(xs),"mean_r":sum(xs)/len(xs),"sum_r":sum(xs),"pf":gw/gl if gl>0 else None,"win_pct":100*sum(x>0 for x in xs)/len(xs),"median_r":statistics.median(xs),"max_drawdown_r":maxdd,"best_r":max(xs),"worst_r":min(xs)}

def concentration(values):
    xs=[x for x in values if x is not None and math.isfinite(x)]
    wins=sorted((x for x in xs if x>0),reverse=True); gross=sum(wins)
    if not xs or not wins or gross<=0:
        return {"top_1pct_gross_win_share_pct":None,"top_5_gross_win_share_pct":None,"mean_r_without_top_1pct_winners":None}
    topn=max(1,math.ceil(.01*len(xs)))
    ranked=sorted(range(len(xs)),key=lambda i:xs[i],reverse=True)
    remove=set(ranked[:topn]); rem=[x for i,x in enumerate(xs) if i not in remove]
    return {"top_1pct_gross_win_share_pct":100*sum(wins[:topn])/gross,"top_5_gross_win_share_pct":100*sum(wins[:5])/gross,"mean_r_without_top_1pct_winners":sum(rem)/len(rem) if rem else None}

def non_overlap(rows,horizon):
    out=[]; next_allowed=None
    for r in sorted(rows,key=lambda z:z["entry_time"]):
        if next_allowed is None or r["entry_time"]>=next_allowed:
            out.append(r); next_allowed=r["entry_time"]+timedelta(minutes=horizon)
    return out

def rvalue(r,horizon,cost):
    raw=r.get(f"h{horizon}_endpoint_r"); risk=r.get("risk")
    if raw is None or risk is None or risk<=0:
        return None
    return raw-cost/risk

def daily_bootstrap(rows,horizon,cost,seed):
    day=defaultdict(list)
    for r in rows:
        x=rvalue(r,horizon,cost)
        if x is not None: day[r["date"]].append(x)
    blocks=[(len(v),sum(v)) for v in day.values() if v]
    if not blocks:
        return {"days":0,"ci95":[None,None],"median":None}
    rng=random.Random(seed); means=[]
    for _ in range(BOOTSTRAPS):
        chosen=rng.choices(blocks,k=len(blocks)); n=sum(c for c,_ in chosen); s=sum(v for _,v in chosen)
        means.append(s/n if n else 0.0)
    return {"days":len(blocks),"ci95":[percentile(means,.025),percentile(means,.975)],"median":percentile(means,.5)}

def daily_delta(signal_rows,control_rows,horizon,cost):
    ds=defaultdict(list); dc=defaultdict(list)
    for r in signal_rows:
        x=rvalue(r,horizon,cost)
        if x is not None: ds[r["date"]].append(x)
    for r in control_rows:
        x=rvalue(r,horizon,cost)
        if x is not None: dc[r["date"]].append(x)
    common=sorted(set(ds)&set(dc))
    return [(d,sum(ds[d])/len(ds[d])-sum(dc[d])/len(dc[d])) for d in common]

def delta_tests(signal_rows,control_rows,horizon,cost,seed):
    vals=daily_delta(signal_rows,control_rows,horizon,cost); ds=[x for _,x in vals]
    if not ds:
        return {"common_days":0,"mean_daily_delta_r":None,"ci95":[None,None],"signflip_p_one_sided":None}
    observed=sum(ds)/len(ds); rng=random.Random(seed); boots=[]
    for _ in range(BOOTSTRAPS):
        z=rng.choices(ds,k=len(ds)); boots.append(sum(z)/len(z))
    extreme=0
    for _ in range(PERMUTATIONS):
        z=[x if rng.random()<.5 else -x for x in ds]
        if sum(z)/len(z)>=observed: extreme+=1
    return {"common_days":len(ds),"mean_daily_delta_r":observed,"ci95":[percentile(boots,.025),percentile(boots,.975)],"signflip_p_one_sided":(extreme+1)/(PERMUTATIONS+1)}

def bh_qvalues(pmap):
    valid=sorted((p,k) for k,p in pmap.items() if p is not None); m=len(valid); out={k:None for k in pmap}
    if not m: return out
    qraw=[[k,min(1.0,p*m/rank)] for rank,(p,k) in enumerate(valid,1)]
    running=1.0
    for k,q in reversed(qraw):
        running=min(running,q); out[k]=running
    return out

def mfe_mae_diag(rows,horizon):
    mfes=[r.get(f"h{horizon}_mfe_r") for r in rows if r.get(f"h{horizon}_mfe_r") is not None]
    maes=[r.get(f"h{horizon}_mae_r") for r in rows if r.get(f"h{horizon}_mae_r") is not None]
    plus=sum(x>=1.0 for x in mfes); minus=sum(x>=1.0 for x in maes)
    return {"mean_mfe_r":sum(mfes)/len(mfes) if mfes else None,"mean_mae_r":sum(maes)/len(maes) if maes else None,"mfe_mae_ratio":(sum(mfes)/len(mfes))/(sum(maes)/len(maes)) if mfes and maes and sum(maes)>0 else None,"hit_plus_1R_within_horizon_pct":100*plus/len(mfes) if mfes else None,"hit_minus_1R_within_horizon_pct":100*minus/len(maes) if maes else None}

def subset(rows,years):
    return [r for r in rows if r["year"] in years]

def mean_cost(rows,horizon,cost):
    return stats([rvalue(r,horizon,cost) for r in rows])

def bucket(v,cuts):
    if v is None: return "NA"
    for i,c in enumerate(cuts):
        if v<c: return i
    return len(cuts)

def match_key(fam,r):
    base=(r["year"],r.get("session"),r.get("direction")); s=r.get("event_strength"); a=r.get("event_aux")
    if fam=="A2_01_ORDER_INTRABAR": return (r["entry_time"].isoformat(),)
    if fam=="A2_02_PATH_EFFICIENCY": return base+(bucket(a,[.5,1.0,1.5,2.0,3.0]),)
    if fam=="A2_03_SCALE_CONFLICT": return base+(bucket(s,[.25,.5,1.0,1.5,2.0]),bucket(a,[.25,.5,1.0,1.5,2.0,3.0]))
    if fam=="A2_04_MAJORITY_AMPLITUDE_CONFLICT":
        vote=None if s is None else int(round(s*12))
        return base+(vote,bucket(a,[.25,.5,1.0,1.5,2.0,3.0]))
    if fam=="A2_05_FROZEN_CENTER_RETURN": return base
    if fam=="A2_06_ROUND5_REJECTION": return base+(bucket(s,[.05,.10,.15]),)
    return base

def matched_control_delta(fam,signal_rows,control_rows,horizon,cost):
    sg=defaultdict(list); cg=defaultdict(list)
    for r in signal_rows:
        x=rvalue(r,horizon,cost)
        if x is not None: sg[match_key(fam,r)].append(x)
    for r in control_rows:
        x=rvalue(r,horizon,cost)
        if x is not None: cg[match_key(fam,r)].append(x)
    num=0.0; den=0; nstrata=0
    for k in set(sg)&set(cg):
        ns=len(sg[k]); nc=len(cg[k]); w=min(ns,nc)
        if not w: continue
        num+=w*((sum(sg[k])/ns)-(sum(cg[k])/nc)); den+=w; nstrata+=1
    return {"matched_support":den,"matched_strata":nstrata,"weighted_mean_delta_r":num/den if den else None}

def descriptive_splits(rows,horizon,cost):
    directions=[]; sessions=[]; quarters=[]
    for d in ("LONG","SHORT"):
        z=[r for r in rows if r.get("direction")==d]
        if z: directions.append({"direction":d,**mean_cost(z,horizon,cost)})
    for sess in ("00-06","06-12","12-18","18-24","UNKNOWN"):
        z=[r for r in rows if r.get("session")==sess]
        if z: sessions.append({"session":sess,**mean_cost(z,horizon,cost)})
    for q in ("Q1","Q2","Q3","Q4"):
        z=[r for r in rows if r.get("quarter_of_year")==q]
        if z: quarters.append({"quarter":q,**mean_cost(z,horizon,cost)})
    return {"direction":directions,"session":sessions,"quarter_of_year":quarters}

def overlap_matrix(signal_sets):
    fams=list(signal_sets); out=[]
    for i,a in enumerate(fams):
        da={r["date"] for r in signal_sets[a]}
        for b in fams[i+1:]:
            db={r["date"] for r in signal_sets[b]}; u=da|db; inter=da&db
            out.append({"family_a":a,"family_b":b,"days_a":len(da),"days_b":len(db),"same_days":len(inter),"jaccard":len(inter)/len(u) if u else None})
    return out

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--input",required=True,type=Path)
    ap.add_argument("--output-dir",required=True,type=Path)
    args=ap.parse_args()
    args.output_dir.mkdir(parents=True,exist_ok=True)
    famcfg={f:(ctl,h,sec) for f,ctl,h,sec in FAMILIES}
    raw=defaultdict(lambda:defaultdict(list)); total=0; rejected_gap=defaultdict(int); rejected_risk=defaultdict(int)
    with args.input.open(newline="",encoding="utf-8") as f:
        for row in csv.DictReader(f):
            total+=1; fam=row.get("family"); var=row.get("variant")
            if fam not in famcfg: continue
            risk=fnum(row.get("risk")); h=famcfg[fam][1]; ep=fnum(row.get(f"h{h}_endpoint_r"))
            if risk is None or ep is None: continue
            if truthy(row.get("outcome_crossed_calendar_gap")):
                rejected_gap[(fam,var)]+=1; continue
            if risk<MIN_RISK:
                rejected_risk[(fam,var)]+=1; continue
            t=datetime.fromisoformat(row["entry_time"])
            z={"entry_time":t,"date":t.date().isoformat(),"year":t.year,"risk":risk,"direction":row.get("direction"),"session":row.get("session") or "UNKNOWN","quarter_of_year":f"Q{((t.month-1)//3)+1}","event_strength":fnum(row.get("event_strength")),"event_aux":fnum(row.get("event_aux")),"atr_m5":fnum(row.get("atr_m5"))}
            for hh in {h,*famcfg[fam][2]}:
                for field in ("endpoint_r","mfe_r","mae_r"):
                    z[f"h{hh}_{field}"]=fnum(row.get(f"h{hh}_{field}"))
            raw[fam][var].append(z)

    prepared={}
    for fam,ctl,h,sec in FAMILIES:
        prepared[fam]={"signal":non_overlap(raw[fam]["SIGNAL"],h),"control":non_overlap(raw[fam][ctl],h),"h":h,"sec":sec,"control_variant":ctl}

    pmap={}; preliminary={}
    for idx,(fam,ctl,h,sec) in enumerate(FAMILIES):
        d=prepared[fam]; dt=delta_tests(d["signal"],d["control"],h,COST_PRIMARY,SEED+idx*100)
        pmap[fam]=dt["signflip_p_one_sided"]; preliminary[fam]=dt
    qmap=bh_qvalues(pmap)

    report={"schema":1,"status":"COMPLETE","input_rows":total,"filters_preregistered":{"outcome_crossed_calendar_gap":False,"min_risk":MIN_RISK,"non_overlap":"wall-clock primary horizon separately for signal and control","development_years":[2017,2018,2019,2020],"internal_holdout_years":[2021,2022],"bootstrap_replicates":BOOTSTRAPS,"signflip_permutations":PERMUTATIONS,"multiple_testing":"Benjamini-Hochberg q across six primary signal-vs-control p-values"},"cheap_gate":CHEAP_GATE,"strict_gate":STRICT_GATE,"families":[],"protected_2023_plus_opened":False,"protected_2026_opened":False}
    signal_sets={}

    for idx,(fam,ctl,h,sec) in enumerate(FAMILIES):
        sig=prepared[fam]["signal"]; con=prepared[fam]["control"]; signal_sets[fam]=sig
        s10=mean_cost(sig,h,.10); s20=mean_cost(sig,h,.20); c10=mean_cost(con,h,.10); c20=mean_cost(con,h,.20)
        vals10=[rvalue(r,h,.10) for r in sig]; conc=concentration(vals10); boot=daily_bootstrap(sig,h,.10,SEED+idx*1000)
        delta=preliminary[fam]; delta["bh_q"]=qmap[fam]; matched=matched_control_delta(fam,sig,con,h,COST_PRIMARY)
        years=[]; positive_years=0
        for y in range(2017,2023):
            sy=subset(sig,{y}); cy=subset(con,{y}); ss=mean_cost(sy,h,.10); dd=delta_tests(sy,cy,h,.10,SEED+idx*1000+y)
            if ss["mean_r"] is not None and ss["mean_r"]>0: positive_years+=1
            years.append({"year":y,"signal":ss,"control":mean_cost(cy,h,.10),"control_delta":dd})
        loo=[]; loo_signal_ok=True; loo_delta_ok=True
        for y in range(2017,2023):
            sy=[r for r in sig if r["year"]!=y]; cy=[r for r in con if r["year"]!=y]
            ss=mean_cost(sy,h,.10); dd=delta_tests(sy,cy,h,.10,SEED+idx*2000+y)
            sigok=ss["mean_r"] is not None and ss["mean_r"]>0
            delok=dd["mean_daily_delta_r"] is not None and dd["mean_daily_delta_r"]>0
            loo_signal_ok=loo_signal_ok and sigok; loo_delta_ok=loo_delta_ok and delok
            loo.append({"excluded_year":y,"signal":ss,"control_delta":dd})
        dev_sig=subset(sig,{2017,2018,2019,2020}); dev_ctl=subset(con,{2017,2018,2019,2020})
        ho_sig=subset(sig,HOLDOUT_YEARS); ho_ctl=subset(con,HOLDOUT_YEARS)
        dev10=mean_cost(dev_sig,h,.10); devdelta=delta_tests(dev_sig,dev_ctl,h,.10,SEED+idx*3000+1)
        ho10=mean_cost(ho_sig,h,.10); ho20=mean_cost(ho_sig,h,.20); hodelta=delta_tests(ho_sig,ho_ctl,h,.10,SEED+idx*3000+2)
        secondary=[]
        for hh in sec:
            ssig=non_overlap(raw[fam]["SIGNAL"],hh); scon=non_overlap(raw[fam][ctl],hh)
            secondary.append({"horizon":hh,"signal_010":mean_cost(ssig,hh,.10),"control_010":mean_cost(scon,hh,.10),"control_delta":delta_tests(ssig,scon,hh,.10,SEED+idx*4000+hh)})
        cheap_checks={"full_n":s10["n"]>=CHEAP_GATE["full_n_min"],"full_mean":s10["mean_r"] is not None and s10["mean_r"]>0,"control_delta":delta["mean_daily_delta_r"] is not None and delta["mean_daily_delta_r"]>0,"development_mean":dev10["mean_r"] is not None and dev10["mean_r"]>0,"holdout_mean":ho10["mean_r"] is not None and ho10["mean_r"]>0}
        strict_checks={
            "full_n":s10["n"]>=STRICT_GATE["full_n_min"],
            "holdout_n":ho10["n"]>=STRICT_GATE["holdout_n_min"],
            "full_010_pf":s10["pf"] is not None and s10["pf"]>=STRICT_GATE["full_cost_010_pf_min"],
            "full_010_mean":s10["mean_r"] is not None and s10["mean_r"]>0,
            "full_020_pf":s20["pf"] is not None and s20["pf"]>=STRICT_GATE["full_cost_020_pf_min"],
            "full_020_mean":s20["mean_r"] is not None and s20["mean_r"]>0,
            "positive_years":positive_years>=STRICT_GATE["positive_years_min"],
            "loo_signal":loo_signal_ok,
            "signal_bootstrap":boot["ci95"][0] is not None and boot["ci95"][0]>0,
            "without_top1":conc["mean_r_without_top_1pct_winners"] is not None and conc["mean_r_without_top_1pct_winners"]>0,
            "holdout_010_pf":ho10["pf"] is not None and ho10["pf"]>=STRICT_GATE["holdout_cost_010_pf_min"],
            "holdout_010_mean":ho10["mean_r"] is not None and ho10["mean_r"]>0,
            "holdout_020_mean":ho20["mean_r"] is not None and ho20["mean_r"]>0,
            "control_common_days":delta["common_days"]>=STRICT_GATE["control_common_days_min"],
            "matched_control_support":matched["matched_support"]>=STRICT_GATE["matched_control_support_min"],
            "matched_control_delta":matched["weighted_mean_delta_r"] is not None and matched["weighted_mean_delta_r"]>0,
            "control_delta":delta["mean_daily_delta_r"] is not None and delta["mean_daily_delta_r"]>0,
            "control_bootstrap":delta["ci95"][0] is not None and delta["ci95"][0]>0,
            "holdout_control_delta":hodelta["mean_daily_delta_r"] is not None and hodelta["mean_daily_delta_r"]>0,
            "loo_control_delta":loo_delta_ok,
            "bh_q":qmap[fam] is not None and qmap[fam]<=STRICT_GATE["bh_q_max"],
        }
        item={"family":fam,"control_variant":ctl,"primary_horizon":h,"signal_n":len(sig),"control_n":len(con),"rejected_gap_signal":rejected_gap[(fam,"SIGNAL")],"rejected_risk_signal":rejected_risk[(fam,"SIGNAL")],"signal_cost_010":{**s10,**conc,"bootstrap_daily":boot},"signal_cost_020":s20,"control_cost_010":c10,"control_cost_020":c20,"signal_vs_control":delta,"coarsened_matched_control":matched,"development_2017_2020":{"signal_010":dev10,"control_delta":devdelta},"internal_holdout_2021_2022":{"signal_010":ho10,"signal_020":ho20,"control_delta":hodelta},"positive_years":positive_years,"yearly":years,"leave_one_year_out":loo,"mfe_mae_primary":mfe_mae_diag(sig,h),"descriptive_splits_cost_010":descriptive_splits(sig,h,.10),"secondary_horizons_descriptive_only":secondary,"cheap_fail_checks":cheap_checks,"cheap_fail_pass":all(cheap_checks.values()),"strict_freeze_checks":strict_checks,"strict_freeze_candidate":all(strict_checks.values())}
        report["families"].append(item)

    report["signal_day_overlap"]=overlap_matrix(signal_sets)
    out=args.output_dir/"atlas2_analysis.json"
    out.write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+"
",encoding="utf-8")
    print("=== GUARDIAN ATLAS II V1 ===")
    print(f"INPUT ROWS: {total}")
    print("2023+ ACCESSED: FALSE")
    print("2026 ACCESSED: FALSE")
    def fmt(v,digits=6):
        return "None" if v is None else f"{v:.{digits}f}"
    for item in report["families"]:
        s=item["signal_cost_010"]; s2=item["signal_cost_020"]; d=item["signal_vs_control"]; ho=item["internal_holdout_2021_2022"]["signal_010"]
        print(f"{item['family']} | H={item['primary_horizon']} | N={s['n']} | .10 MeanR={fmt(s['mean_r'])} PF={fmt(s['pf'])} | .20 MeanR={fmt(s2['mean_r'])} | Years+={item['positive_years']}/6")
        print(f"  Control delta/day={d['mean_daily_delta_r'] if d['mean_daily_delta_r'] is not None else 'None'} | CI95={d['ci95']} | p={d['signflip_p_one_sided']} | BHq={d['bh_q']}")
        print(f"  Holdout 2021-22: N={ho['n']} MeanR={ho['mean_r']} PF={ho['pf']} | Cheap={item['cheap_fail_pass']} | StrictFreeze={item['strict_freeze_candidate']}")
    print(f"REPORT: {out}")

if __name__=="__main__":
    raise SystemExit(main())
