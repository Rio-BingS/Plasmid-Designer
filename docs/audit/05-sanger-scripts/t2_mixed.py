import sys, random
sys.path.insert(0, "src/backend"); sys.path.insert(0, "tests")
from core.sanger.pipeline import _detect_mixed_detail
random.seed(2)
# 每碱基 10 采样点，主峰 1000，单峰三角；在若干位点放入次级峰，比例 r
def build(bases, mix):  # mix: {i: (base2, ratio)}
    spb=10; n=len(bases)*spb
    tr={b:[20]*n for b in "ACGT"}
    peaks=[]
    for i,b in enumerate(bases):
        c=i*spb+5; peaks.append(c)
        for d,h in ((-2,0.3),(-1,0.7),(0,1.0),(1,0.7),(2,0.3)):
            tr[b][c+d]+=int(1000*h)
        if i in mix:
            b2,r=mix[i]
            for d,h in ((-2,0.3),(-1,0.7),(0,1.0),(1,0.7),(2,0.3)):
                tr[b2][c+d]+=int(1000*h*r)
    return tr,peaks
bases="".join(random.choice("ACGT") for _ in range(200))
for r in (0.4,0.6,0.8,0.89,0.92,0.95,1.0):
    sites=[50,80,110,140,170]
    mix={i:([x for x in "ACGT" if x!=bases[i]][0], r) for i in sites}
    tr,pk=build(bases,mix)
    det=_detect_mixed_detail(bases,tr,pk)
    print(f"secondary/main={r:.2f}  检出 {len(det)}/5", [d['pos'] for d in det])
