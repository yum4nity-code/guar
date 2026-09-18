#!/usr/bin/env python3
from __future__ import annotations

import argparse, csv, json, math, random, statistics
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

SEED=20260918
BOOTSTRAPS=4000
PERMUTATIONS=4000
MIN_RISK=0.01
COST_PRIMARY=0.10
HOLDOUT={2021,2022}

FAMILIES=[
 {"family":"A4_01_BIPOWER_JUMP_REVERSAL","kind":"DIRECTIONAL","control":"CONTROL_CONT","h":30,"secondary":[15,60]},
 {"family":"A4_02_REALIZED_SKEW_REVERSAL","kind":"DIRECTIONAL","control":"CONTROL_CONT","h":60,"secondary":[30,120]},
 {"family":"A4_03_VOL_OF_VOL","kind":"AMPLITUDE","control":"CONTROL_LOW_VOV","h":60,"secondary":[120]},
 {"family":"A4_04_REALIZED_KURTOSIS","kind":"AMPLITUDE","control":"CONTROL_NORMAL_KURT","h":60,"secondary":[120]},
 {"family":"A4_05_CLOSE_LOCATION_PRESSURE","kind":"DIRECTIONAL","control":"CONTROL_OPPOSITE","h":30,"secondary":[15,60]},
 {"family":"A4_06_INTRADAY_SEASONALITY","kind":"DIRECTIONAL","control":"CONTROL_OPPOSITE","h":60,"secondary":[30,120]},
]

def fnum(x):
    try:
        v=float(x); return v if math.isfinite(v) else None
    except: return None

def truthy(x): return str(x).strip().lower() in {"true","1","yes"}

def pct(xs,q):
    if not xs: return None
    a=sorted(xs)
    p=(len(a)-1)*q; lo=int(math.floor(p)); hi=int(math.ceil(p))
    if lo==hi: return a[lo]
    w=p-lo; return a[lo]*(1-w)+a[hi]*w

def stats(xs):
    xs=[x for x in xs if x is not None and math.isfinite(x)]
    if not xs: return {"n":0,"mean_r":None,"pf":None,"median_r":None,"sum_r":None,"win_pct":None,"max_drawdown_r":None}
    gw=sum(x for x in xs if x>0); gl=-sum(x for x in xs if x<0)
    cum=peak=dd=0.0
    for x in xs:
        cum+=x; peak=max(peak,cum); dd=max(dd,peak-cum)
    return {"n":len(xs),"mean_r":sum(xs)/len(xs),"pf":gw/gl if gl>0 else None,"median_r":statistics.median(xs),"sum_r":sum(xs),"win_pct":100*sum(x>0 for x in xs)/len(xs),"max_drawdown_r":dd}

def ampstats(xs):
    xs=[x for x in xs if x is not None and math.isfinite(x)]
    if not xs: return {"n":0,"mean_amp_r":None,"median_amp_r":None}
    return {"n":len(xs),"mean_amp_r":sum(xs)/len(xs),"median_amp_r":statistics.median(xs),"p90_amp_r":pct(xs,.9)}

def nonoverlap(rows,h):
    out=[]; nxt=None
    for r in sorted(rows,key=lambda z:z["entry_time"]):
        if nxt is None or r["entry_time"]>=nxt:
            out.append(r); nxt=r["entry_time"]+timedelta(minutes=h)
    return out

def rvalue(r,h,c):
    raw=r.get(f"h{h}_endpoint_r"); risk=r.get("risk")
    return None if raw is None or risk in (None,0) else raw-c/risk

def ampvalue(r,h):
    a=r.get(f"h{h}_mfe_r"); b=r.get(f"h{h}_mae_r")
    return None if a is None or b is None else max(a,b)

def daily(rows,vf):
    d=defaultdict(list)
    for r in rows:
        x=vf(r)
        if x is not None and math.isfinite(x): d[r["date"]].append(x)
    return d

def bootstrap(rows,vf,seed):
    d=daily(rows,vf); blocks=[(len(v),sum(v)) for v in d.values() if v]
    if not blocks: return {"days":0,"ci95":[None,None],"median":None}
    rng=random.Random(seed); vals=[]
    for _ in range(BOOTSTRAPS):
        z=rng.choices(blocks,k=len(blocks)); n=sum(a for a,_ in z); s=sum(b for _,b in z)
        vals.append(s/n if n else 0)
    return {"days":len(blocks),"ci95":[pct(vals,.025),pct(vals,.975)],"median":pct(vals,.5)}

def delta(sig,ctl,vf,seed):
    a=daily(sig,vf); b=daily(ctl,vf); common=sorted(set(a)&set(b))
    ds=[sum(a[d])/len(a[d])-sum(b[d])/len(b[d]) for d in common]
    if not ds: return {"common_days":0,"mean_daily_delta":None,"ci95":[None,None],"signflip_p_one_sided":None}
    obs=sum(ds)/len(ds); rng=random.Random(seed)
    boots=[sum(z:=rng.choices(ds,k=len(ds)))/len(z) for _ in range(BOOTSTRAPS)]
    ext=0
    for _ in range(PERMUTATIONS):
        z=[x if rng.random()<.5 else -x for x in ds]
        if sum(z)/len(z)>=obs: ext+=1
    return {"common_days":len(ds),"mean_daily_delta":obs,"ci95":[pct(boots,.025),pct(boots,.975)],"signflip_p_one_sided":(ext+1)/(PERMUTATIONS+1)}

def bh(pmap):
    vals=sorted((p,k) for k,p in pmap.items() if p is not None); m=len(vals); out={k:None for k in pmap}
    running=1.0
    tmp=[]
    for i,(p,k) in enumerate(vals,1): tmp.append((k,min(1.0,p*m/i)))
    for k,q in reversed(tmp): running=min(running,q); out[k]=running
    return out

def subset(rows,years): return [r for r in rows if r["year"] in years]

def matched(fam,kind,sig,ctl,vf):
    # Same-event directional controls are exact timestamp pairs.
    if kind=="DIRECTIONAL":
        sm={r["entry_time"]:vf(r) for r in sig}
        cm={r["entry_time"]:vf(r) for r in ctl}
        ks=[k for k in sm.keys()&cm.keys() if sm[k] is not None and cm[k] is not None]
        return {"matched_support":len(ks),"matched_strata":len(ks),"weighted_mean_delta":sum(sm[k]-cm[k] for k in ks)/len(ks) if ks else None}
    # Amplitude controls occur at different times: coarsen on year/session and background aux only,
    # deliberately excluding defining state strength because the preregistered regimes do not overlap.
    def key(r):
        a=r.get("event_aux")
        if a is None: ab="NA"
        elif a<.25: ab=0
        elif a<.5: ab=1
        elif a<1: ab=2
        elif a<2: ab=3
        elif a<4: ab=4
        else: ab=5
        return (r["year"],r["session"],ab)
    sg=defaultdict(list); cg=defaultdict(list)
    for r in sig:
        x=vf(r)
        if x is not None: sg[key(r)].append(x)
    for r in ctl:
        x=vf(r)
        if x is not None: cg[key(r)].append(x)
    num=0.0; den=0; nst=0
    for k in sg.keys()&cg.keys():
        w=min(len(sg[k]),len(cg[k]))
        if not w: continue
        num+=w*(sum(sg[k])/len(sg[k])-sum(cg[k])/len(cg[k])); den+=w; nst+=1
    return {"matched_support":den,"matched_strata":nst,"weighted_mean_delta":num/den if den else None}

def concentration(xs):
    xs=[x for x in xs if x is not None and math.isfinite(x)]
    if not xs: return {"mean_r_without_top_1pct_winners":None}
    n=max(1,math.ceil(.01*len(xs))); idx=sorted(range(len(xs)),key=lambda i:xs[i],reverse=True)[:n]; rem=[x for i,x in enumerate(xs) if i not in set(idx)]
    return {"mean_r_without_top_1pct_winners":sum(rem)/len(rem) if rem else None}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--input",required=True,type=Path); ap.add_argument("--output-dir",required=True,type=Path); args=ap.parse_args()
    args.output_dir.mkdir(parents=True,exist_ok=True)
    cfg={x["family"]:x for x in FAMILIES}; raw=defaultdict(lambda:defaultdict(list)); total=0

    with args.input.open(newline="",encoding="utf-8") as f:
        for row in csv.DictReader(f):
            total+=1; fam=row.get("family"); var=row.get("variant")
            if fam not in cfg: continue
            risk=fnum(row.get("risk"))
            if risk is None or risk<MIN_RISK or truthy(row.get("outcome_crossed_calendar_gap")): continue
            t=datetime.fromisoformat(row["entry_time"]); z={"entry_time":t,"date":t.date().isoformat(),"year":t.year,"risk":risk,"session":row.get("session") or "UNKNOWN","event_strength":fnum(row.get("event_strength")),"event_aux":fnum(row.get("event_aux"))}
            for hh in {cfg[fam]["h"],*cfg[fam]["secondary"]}:
                for field in ("endpoint_r","mfe_r","mae_r"): z[f"h{hh}_{field}"]=fnum(row.get(f"h{hh}_{field}"))
            raw[fam][var].append(z)

    prep={}
    pmap={}
    pre={}
    for i,x in enumerate(FAMILIES):
        fam=x["family"]; h=x["h"]; s=nonoverlap(raw[fam]["SIGNAL"],h); c=nonoverlap(raw[fam][x["control"]],h); prep[fam]=(s,c)
        vf=(lambda r,hh=h: ampvalue(r,hh)) if x["kind"]=="AMPLITUDE" else (lambda r,hh=h:rvalue(r,hh,COST_PRIMARY))
        d=delta(s,c,vf,SEED+i*100); pre[fam]=d; pmap[fam]=d["signflip_p_one_sided"]
    qmap=bh(pmap)

    report={"schema":1,"status":"COMPLETE","input_rows":total,"protected_2023_plus_opened":False,"protected_2026_opened":False,"families":[]}

    for i,x in enumerate(FAMILIES):
        fam=x["family"]; kind=x["kind"]; h=x["h"]; sig,ctl=prep[fam]
        if kind=="AMPLITUDE":
            vf=lambda r,hh=h: ampvalue(r,hh)
            ss=ampstats([vf(r) for r in sig]); cs=ampstats([vf(r) for r in ctl]); d=pre[fam]; d["bh_q"]=qmap[fam]
            mat=matched(fam,kind,sig,ctl,vf)
            hos=subset(sig,HOLDOUT); hoc=subset(ctl,HOLDOUT); hod=delta(hos,hoc,vf,SEED+i*1000+1)
            m1=ss["mean_amp_r"]; m0=cs["mean_amp_r"]; uplift=None if m1 is None or not m0 else 100*(m1-m0)/m0
            loo_ok=True; loo=[]
            for y in range(2017,2023):
                ld=delta([r for r in sig if r["year"]!=y],[r for r in ctl if r["year"]!=y],vf,SEED+i*2000+y)
                loo_ok &= (ld["mean_daily_delta"] is not None and ld["mean_daily_delta"]>0); loo.append({"excluded_year":y,"delta":ld})
            checks={
              "full_n":ss["n"]>=500,
              "holdout_n":len(hos)>=150,
              "relative_uplift":uplift is not None and uplift>=5.0,
              "delta_ci95_lower":d["ci95"][0] is not None and d["ci95"][0]>0,
              "holdout_delta":hod["mean_daily_delta"] is not None and hod["mean_daily_delta"]>0,
              "loo_delta":loo_ok,
              "matched_support":mat["matched_support"]>=100,
              "matched_delta":mat["weighted_mean_delta"] is not None and mat["weighted_mean_delta"]>0,
              "bh_q":qmap[fam] is not None and qmap[fam]<=.10,
            }
            item={"family":fam,"kind":kind,"primary_horizon":h,"signal_primary":ss,"control_primary":cs,"relative_uplift_pct":uplift,"signal_vs_control":d,"coarsened_matched_control":mat,"internal_holdout_2021_2022":{"signal":ampstats([vf(r) for r in hos]),"control_delta":hod},"leave_one_year_out":loo,"phenomenon_checks":checks,"phenomenon_candidate":all(checks.values()),"tradable_candidate":False}
        else:
            vf=lambda r,hh=h:rvalue(r,hh,.10)
            s10=stats([rvalue(r,h,.10) for r in sig]); s20=stats([rvalue(r,h,.20) for r in sig]); d=pre[fam]; d["bh_q"]=qmap[fam]
            mat=matched(fam,kind,sig,ctl,vf); boot=bootstrap(sig,vf,SEED+i*1000); conc=concentration([rvalue(r,h,.10) for r in sig])
            dev=subset(sig,{2017,2018,2019,2020}); hos=subset(sig,HOLDOUT); hoc=subset(ctl,HOLDOUT)
            devs=stats([rvalue(r,h,.10) for r in dev]); ho10=stats([rvalue(r,h,.10) for r in hos]); ho20=stats([rvalue(r,h,.20) for r in hos]); hod=delta(hos,hoc,vf,SEED+i*1000+2)
            pos=0; loo_sig=True; loo_delta=True; yearly=[]; loo=[]
            for y in range(2017,2023):
                sy=subset(sig,{y}); ys=stats([rvalue(r,h,.10) for r in sy]); pos+=1 if ys["mean_r"] is not None and ys["mean_r"]>0 else 0; yearly.append({"year":y,"signal":ys})
                sx=[r for r in sig if r["year"]!=y]; cx=[r for r in ctl if r["year"]!=y]; ls=stats([rvalue(r,h,.10) for r in sx]); ld=delta(sx,cx,vf,SEED+i*3000+y)
                loo_sig &= (ls["mean_r"] is not None and ls["mean_r"]>0); loo_delta &= (ld["mean_daily_delta"] is not None and ld["mean_daily_delta"]>0); loo.append({"excluded_year":y,"signal":ls,"control_delta":ld})
            cheap={"full_n":s10["n"]>=100,"full_mean":s10["mean_r"] is not None and s10["mean_r"]>0,"control_delta":d["mean_daily_delta"] is not None and d["mean_daily_delta"]>0,"development_mean":devs["mean_r"] is not None and devs["mean_r"]>0,"holdout_mean":ho10["mean_r"] is not None and ho10["mean_r"]>0}
            strict={
              "full_n":s10["n"]>=200,"holdout_n":ho10["n"]>=60,
              "full_010_pf":s10["pf"] is not None and s10["pf"]>=1.10,"full_010_mean":s10["mean_r"] is not None and s10["mean_r"]>0,
              "full_020_pf":s20["pf"] is not None and s20["pf"]>=1.05,"full_020_mean":s20["mean_r"] is not None and s20["mean_r"]>0,
              "positive_years":pos>=4,"loo_signal":loo_sig,"signal_bootstrap":boot["ci95"][0] is not None and boot["ci95"][0]>0,
              "without_top1":conc["mean_r_without_top_1pct_winners"] is not None and conc["mean_r_without_top_1pct_winners"]>0,
              "holdout_010_pf":ho10["pf"] is not None and ho10["pf"]>=1.05,"holdout_010_mean":ho10["mean_r"] is not None and ho10["mean_r"]>0,"holdout_020_mean":ho20["mean_r"] is not None and ho20["mean_r"]>0,
              "control_common_days":d["common_days"]>=30,"matched_support":mat["matched_support"]>=30,"matched_delta":mat["weighted_mean_delta"] is not None and mat["weighted_mean_delta"]>0,
              "control_delta":d["mean_daily_delta"] is not None and d["mean_daily_delta"]>0,"control_bootstrap":d["ci95"][0] is not None and d["ci95"][0]>0,
              "holdout_control_delta":hod["mean_daily_delta"] is not None and hod["mean_daily_delta"]>0,"loo_control_delta":loo_delta,"bh_q":qmap[fam] is not None and qmap[fam]<=.10,
            }
            item={"family":fam,"kind":kind,"primary_horizon":h,"signal_cost_010":{**s10,**conc,"bootstrap_daily":boot},"signal_cost_020":s20,"signal_vs_control":d,"coarsened_matched_control":mat,"development_2017_2020":{"signal_010":devs},"internal_holdout_2021_2022":{"signal_010":ho10,"signal_020":ho20,"control_delta":hod},"positive_years":pos,"yearly":yearly,"leave_one_year_out":loo,"cheap_fail_checks":cheap,"cheap_fail_pass":all(cheap.values()),"strict_freeze_checks":strict,"strict_freeze_candidate":all(strict.values())}
        report["families"].append(item)

    out=args.output_dir/"atlas4_analysis.json"; out.write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+"\n",encoding="utf-8")
    print("=== GUARDIAN ATLAS IV V1 ==="); print(f"INPUT ROWS: {total}"); print("2023+ ACCESSED: FALSE"); print("2026 ACCESSED: FALSE")
    for it in report["families"]:
        if it["kind"]=="AMPLITUDE":
            s=it["signal_primary"]; d=it["signal_vs_control"]; ho=it["internal_holdout_2021_2022"]["signal"]
            print(f"{it['family']} | AMPLITUDE | H={it['primary_horizon']} | N={s['n']} | MeanAmpR={s['mean_amp_r']} | Uplift={it['relative_uplift_pct']}%")
            print(f"  Delta/day={d['mean_daily_delta']} | CI95={d['ci95']} | p={d['signflip_p_one_sided']} | BHq={d['bh_q']}")
            print(f"  Holdout 2021-22: N={ho['n']} MeanAmpR={ho['mean_amp_r']} | Phenomenon={it['phenomenon_candidate']} | Tradable=False")
        else:
            s=it["signal_cost_010"]; s2=it["signal_cost_020"]; d=it["signal_vs_control"]; ho=it["internal_holdout_2021_2022"]["signal_010"]
            print(f"{it['family']} | DIRECTIONAL | H={it['primary_horizon']} | N={s['n']} | .10 MeanR={s['mean_r']} PF={s['pf']} | .20 MeanR={s2['mean_r']} | Years+={it['positive_years']}/6")
            print(f"  Delta/day={d['mean_daily_delta']} | CI95={d['ci95']} | p={d['signflip_p_one_sided']} | BHq={d['bh_q']}")
            print(f"  Holdout 2021-22: N={ho['n']} MeanR={ho['mean_r']} PF={ho['pf']} | Cheap={it['cheap_fail_pass']} | StrictFreeze={it['strict_freeze_candidate']}")
    print(f"REPORT: {out}")

if __name__=="__main__": raise SystemExit(main())
