#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,json,math
from collections import defaultdict
from pathlib import Path
def F(x):
    try:return float(x)
    except:return None
class A:
    __slots__=("n","s","w","l","wc")
    def __init__(self):self.n=0;self.s=0.;self.w=0.;self.l=0.;self.wc=0
    def add(self,x):
        self.n+=1;self.s+=x
        if x>0:self.w+=x;self.wc+=1
        elif x<0:self.l-=x
    def out(self,k):
        return {"group":k,"n":self.n,"mean_r":self.s/self.n,"pf":self.w/self.l if self.l else (math.inf if self.w else None),"win_pct":100*self.wc/self.n}
def main():
    p=argparse.ArgumentParser();p.add_argument("--input",required=True,type=Path);p.add_argument("--output",required=True,type=Path);x=p.parse_args();b=defaultdict(A)
    def add(r,k):
        raw=F(r.get("h60_endpoint_r"));risk=F(r.get("risk"))
        if raw is not None and risk and risk>0:b[k].add(raw-0.10/risk)
    rows=0
    with x.input.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows+=1
            for name in ("rsi_m1","rsi_m5","rsi_m15"):
                z=F(r[name])
                if z is not None:add(r,f"{name.upper()}_{int(z//10)*10:02d}-{min(100,int(z//10)*10+10):02d}")
            add(r,"SESSION_"+r["session"]);add(r,"WEEKDAY_"+r["weekday"]);add(r,"DIRECTION_"+r["direction"])
            loc=F(r["range96_loc"])
            if loc is not None:add(r,f"R96_LOC_{max(-2,min(12,int(loc*10)))/10:.1f}")
            imp=F(r["ret3_atr"])
            if imp is not None:add(r,f"RET3_ATR_BIN_{sum(imp>=e for e in [-3,-2,-1,-.5,0,.5,1,2,3])}")
            vr=F(r["vol_ratio_12_48"])
            if vr is not None:add(r,"VOLRATIO_"+("LT0.5" if vr<.5 else "0.5-0.8" if vr<.8 else "0.8-1.0" if vr<1 else "1.0-1.2" if vr<1.2 else "1.2-1.5" if vr<1.5 else "GE1.5"))
            add(r,"OUTCOME_GAP_"+str(r.get("outcome_crossed_calendar_gap")))
    out=[a.out(k) for k,a in b.items() if a.n>=100];out.sort(key=lambda z:z["mean_r"],reverse=True)
    x.output.write_text(json.dumps({"status":"COMPLETE","signals":rows,"horizon":60,"cost":0.10,"groups":out},indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":"COMPLETE","signals":rows,"groups":len(out),"top":out[:20]}))
if __name__=="__main__":main()
