#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,json,math
from collections import defaultdict
from datetime import datetime,timedelta
from pathlib import Path
H=(5,15,30,60,120,240);C=(0.00,0.05,0.10,0.15,0.20)
def F(x):
    try:return float(x)
    except:return None
class A:
    __slots__=("n","s","w","l","wc")
    def __init__(self):self.n=0;self.s=0.0;self.w=0.0;self.l=0.0;self.wc=0
    def add(self,x):
        self.n+=1;self.s+=x
        if x>0:self.w+=x;self.wc+=1
        elif x<0:self.l-=x
    def out(self):
        return {"n":self.n,"mean_r":self.s/self.n if self.n else None,"sum_r":self.s if self.n else None,"pf":self.w/self.l if self.l else (math.inf if self.w else None),"win_pct":100*self.wc/self.n if self.n else None}
def main():
    p=argparse.ArgumentParser();p.add_argument("--input",required=True,type=Path);p.add_argument("--output-dir",required=True,type=Path);x=p.parse_args();x.output_dir.mkdir(parents=True,exist_ok=True)
    allacc=defaultdict(A);noacc=defaultdict(A);yracc=defaultdict(A);last={};lastyr={};rows=0
    with x.input.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows+=1;g=(r["family"],r["variant"],r["direction"]);t=datetime.fromisoformat(r["entry_time"]);y=r["year"];risk=F(r["risk"])
            if not risk or risk<=0:continue
            for h in H:
                raw=F(r.get(f"h{h}_endpoint_r"))
                if raw is None:continue
                for c in C:allacc[g+(h,c)].add(raw-c/risk)
                kn=g+(h,)
                take=kn not in last or t>=last[kn]
                if take:
                    last[kn]=t+timedelta(minutes=h)
                    for c in C:noacc[g+(h,c)].add(raw-c/risk)
                ky=g+(h,y)
                if ky not in lastyr or t>=lastyr[ky]:
                    lastyr[ky]=t+timedelta(minutes=h);yracc[ky].add(raw-0.10/risk)
    lb=[]
    keys=set(allacc)|set(noacc)
    for fam,var,d,h,c in sorted(keys):
        if (fam,var,d,h,c) in allacc:lb.append({"family":fam,"variant":var,"direction":d,"horizon_min":h,"cost":c,"non_overlap":False,**allacc[(fam,var,d,h,c)].out()})
        if (fam,var,d,h,c) in noacc:lb.append({"family":fam,"variant":var,"direction":d,"horizon_min":h,"cost":c,"non_overlap":True,**noacc[(fam,var,d,h,c)].out()})
    yr=[]
    for (fam,var,d,h,y),a in sorted(yracc.items()):yr.append({"family":fam,"variant":var,"direction":d,"horizon_min":h,"year":y,**a.out()})
    with (x.output_dir/"leaderboard.csv").open("w",newline="",encoding="utf-8") as f:
        fd=["family","variant","direction","horizon_min","cost","non_overlap","n","mean_r","sum_r","pf","win_pct"];w=csv.DictWriter(f,fieldnames=fd);w.writeheader();w.writerows(lb)
    with (x.output_dir/"yearly.csv").open("w",newline="",encoding="utf-8") as f:
        fd=["family","variant","direction","horizon_min","year","n","mean_r","sum_r","pf","win_pct"];w=csv.DictWriter(f,fieldnames=fd);w.writeheader();w.writerows(yr)
    ymap=defaultdict(list)
    for z in yr:ymap[(z["family"],z["variant"],z["direction"],z["horizon_min"])].append(z)
    index={(z["family"],z["variant"],z["direction"],z["horizon_min"],z["cost"],z["non_overlap"]):z for z in lb}
    short=[]
    for r in lb:
        if r["cost"]!=0.10 or r["non_overlap"] is not True or not r["n"] or r["n"]<200 or r["mean_r"] is None or r["pf"] is None:continue
        pos=sum((q["mean_r"] or -999)>0 for q in ymap[(r["family"],r["variant"],r["direction"],r["horizon_min"])])
        stress=index.get((r["family"],r["variant"],r["direction"],r["horizon_min"],0.20,True))
        if r["mean_r"]>0 and r["pf"]>1.05 and pos>=4:
            short.append({**r,"positive_years":pos,"stress_020_mean_r":stress["mean_r"] if stress else None,"stress_020_pf":stress["pf"] if stress else None,"label":"DISCOVERY_PROMISING_NOT_VALIDATED"})
    short.sort(key=lambda z:(z["pf"],z["mean_r"]),reverse=True)
    (x.output_dir/"shortlist.json").write_text(json.dumps(short,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":"COMPLETE","signals":rows,"shortlist_count":len(short),"top":short[:10]}))
if __name__=="__main__":main()
