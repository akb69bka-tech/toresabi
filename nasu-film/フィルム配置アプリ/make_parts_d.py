# デザインC「窓あき・高知県産」：中のナスが見えるよう、印刷は上帯と下帯に集める。白＋2色目。
import sys, json, io, math
SCR='/tmp/claude-0/-home-user-toresabi/fc26378e-b8ad-59be-9637-eeec6c2c5d48/scratchpad/'
sys.path.insert(0, SCR+'ai')
import fonthelp as fh
import numpy as np, cairosvg, potrace
from PIL import Image
from scipy import ndimage as ndi
fh.FD=''
FA=SCR+'ai/fonts/'; FC=SCR+'c/fonts/'
fh.FONTS.update({'dela':(FC+'DelaGothicOne-Regular.ttf','Dela Gothic One',400),'brush':(FC+'YujiSyuku-Regular.ttf','Yuji Syuku',400)})
for k in ('maru900','maru700','min700','en600','en800','script'): fh.FONTS[k]=(FA+fh.FONTS[k][0],)+fh.FONTS[k][1:]
W,H,R=200,300,16          # R = px/mm
def raster(svg_body):
    svg=f'<svg xmlns="http://www.w3.org/2000/svg" width="{W*R}" height="{H*R}" viewBox="0 0 {W} {H}"><rect width="{W}" height="{H}" fill="#fff"/>{svg_body}</svg>'
    im=Image.open(io.BytesIO(cairosvg.svg2png(bytestring=svg.encode()))).convert('L')
    return np.array(im)<128
def T(k,t,x,y,size,ls=0): return f'<path fill="#000" d="{fh.text_paths(k,t,x,y,size,"center",ls,"middle")}"/>'
def dil(m,mm):
    r=int(round(mm*R)); yy,xx=np.ogrid[-r:r+1,-r:r+1]
    return ndi.binary_dilation(m, structure=(xx*xx+yy*yy<=r*r))
def trace(m,turd=4):
    return potrace.Bitmap(~m).trace(turdsize=turd, alphamax=1.0, opticurve=True, opttolerance=0.2)
def to_d(pl,cx,cy):
    f=lambda p: f'{(p.x/R-cx):.2f} {(p.y/R-cy):.2f}'
    out=[]
    for c in pl:
        s=['M'+f(c.start_point)]
        for sg in c.segments:
            s.append(('L'+f(sg.c)+' L'+f(sg.end_point)) if sg.is_corner else ('C'+f(sg.c1)+' '+f(sg.c2)+' '+f(sg.end_point)))
        s.append('Z'); out.append(' '.join(s))
    return ' '.join(out)
parts=[]
def add(pid,name,color,white=None,hide=False):
    m=color|(white if white is not None else False)
    ys,xs=np.nonzero(m); x0,x1,y0,y1=xs.min(),xs.max()+1,ys.min(),ys.max()+1
    cx,cy,w,h=(x0+x1)/2/R,(y0+y1)/2/R,(x1-x0)/R,(y1-y0)/R
    it=dict(id=pid,name=name,cx=round(cx,2),cy=round(cy,2),w=round(w,2),h=round(h,2),gold=to_d(trace(color),cx,cy))
    if white is not None: it['white']=to_d(trace(white,8),cx,cy)
    if hide: it['hide']=True
    parts.append(it); print(pid,it['cx'],it['cy'],it['w'],it['h'],len(json.dumps(it))//1024,'KB')


# デザインD：袋はほぼ透明。いちばん下に高知県の形（安芸市にピン）と「高知県産」だけ。
import math
G=SCR+'d/geo/'
k=[f for f in json.load(open(G+'japan.geojson'))['features'] if f['properties']['id']==39][0]
rings=[po[0] for po in k['geometry']['coordinates'] if len(po[0])>=40]   # 小さな島は省く
LON0,LON1,LAT0,LAT1=132.4794,134.3125,32.7023,33.8830
cosl=math.cos(math.radians(33.3)); MW=58.0; sc=MW/((LON1-LON0)*cosl)
MX,MY=30.0,241.0
P=lambda lon,lat:(MX+(lon-LON0)*cosl*sc, MY+(LAT1-lat)*sc)
dpath=' '.join('M'+' L'.join(f'{P(x,y)[0]:.3f} {P(x,y)[1]:.3f}' for x,y in r)+'Z' for r in rings)
land=raster(f'<path fill="#000" fill-rule="nonzero" d="{dpath}"/>')
AKI=(133.9072,33.5025)                      # 安芸市の中心街（市役所付近）
px,py=P(*AKI)
pin_d=f'M{px} {py} C{px-1.6} {py-3.4} {px-4.2} {py-5.6} {px-4.2} {py-8.6} A4.2 4.2 0 1 1 {px+4.2} {py-8.6} C{px+4.2} {py-5.6} {px+1.6} {py-3.4} {px} {py}Z'
pin=raster(f'<path fill="#000" d="{pin_d}"/>')
dot=raster(f'<circle cx="{px}" cy="{py-8.6}" r="1.6"/>')
ring=pin&~ndi.binary_erosion(pin,iterations=int(0.55*R))
lab=raster(T('maru900','安芸市',px-6.6,py+4.8,4.4,0.3))
color=(land&~dil(pin,0.6))|ring|dot|lab
white=dil(land|pin|lab,0.8)
add('kochi','高知県のアイコン（安芸市にピン）',color,white)
kt=raster(T('dela','高知県産',131.5,263.5,16.5,1.6))
add('kochiSan','「高知県産」',kt,dil(kt,1.0))
for pid,big,small in (('size_l','L','3本入'),('size_m','M','5本入')):
    c=raster('<circle cx="166" cy="34" r="13"/>')
    t=raster(T('dela',big,166,29.6,13)+T('maru900',small,166,41,4.6,0.3))
    add(pid,f'サイズ「{big}・{small}」',c&~t,c,True)
json.dump({'film':{'w':W,'h':H},'gold':'#d49936','parts':parts},open(SCR+'art/parts_d.json','w',encoding='utf-8'),ensure_ascii=False,separators=(',',':'))
print('pin tip',round(px,2),round(py,2))
