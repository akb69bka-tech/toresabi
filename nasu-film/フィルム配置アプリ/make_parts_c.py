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

# 1 上帯「高知県産」（帯は2色目、文字は抜いて白）
band=raster('<rect x="14" y="14" width="172" height="40" rx="4"/>')
txt=raster(T('dela','高知県産',100,30.5,27,4)+T('en600','KOCHI  JAPAN',100,47.6,4.2,1.6))
add('band','上帯「高知県産」',band&~txt,band)
# 2 「安芸ナス」筆文字
nm=raster(T('brush','安芸ナス',102.25,75,25,1.5)); add('name','「安芸ナス」',nm,dil(nm,1.1))
# 3 文句
cp=raster(T('min700','食卓に、高知の恵を。',102.4,95.5,6.2,0.9)); add('copy','文句「食卓に、高知の恵を。」',cp,dil(cp,0.8))
# 4 窓の目印（四隅のかぎ）
x0,y0,x1,y1,a=32,106,168,236,12
br=f'<path fill="none" stroke="#000" stroke-width="1.2" stroke-linecap="square" d="M{x0} {y0+a}V{y0}H{x0+a} M{x1-a} {y0}H{x1}V{y0+a} M{x0} {y1-a}V{y1}H{x0+a} M{x1-a} {y1}H{x1}V{y1-a}"/>'
wm=raster(br); add('window','窓の目印（四隅）',wm,dil(wm,0.7))
# 5 日の出と波のマーク（高知・太平洋）
sx,sy=64,253.5
rays=''.join(f'<line x1="{sx+9*math.cos(math.radians(a)):.2f}" y1="{sy-9*math.sin(math.radians(a)):.2f}" x2="{sx+12*math.cos(math.radians(a)):.2f}" y2="{sy-12*math.sin(math.radians(a)):.2f}"/>' for a in range(15,180,30))
wave=lambda y: f'<path d="M{sx-15} {y} q3.75 -2.2 7.5 0 t7.5 0 t7.5 0 t7.5 0"/>'
mk=raster(f'<path fill="#000" d="M{sx-6.5} {sy} A6.5 6.5 0 0 1 {sx+6.5} {sy} Z"/><g stroke="#000" stroke-width="1.1" stroke-linecap="round" fill="none">{rays}{wave(sy+2.6)}{wave(sy+5.6)}</g>')
add('mark','日の出と波のマーク',mk,dil(mk,0.8))
# 6 産地
pl=raster(T('min700','高知県安芸市',64.56,269,7.2,1.2)); add('place','「高知県安芸市」',pl,dil(pl,0.8))
# 7,8 サイズ表示（L は表示、M は隠しておく）
for pid,big,small,hide in (('size_l','L','3本入',False),('size_m','M','5本入',True)):
    c=raster('<circle cx="146" cy="262" r="15"/>')
    t=raster(T('dela',big,146,257.2,15)+T('maru900',small,146,270.6,5.4,0.3))
    add(pid,f'サイズ「{big}・{small}」',c&~t,c,hide)
json.dump({'film':{'w':W,'h':H},'gold':'#d49936','parts':parts},open(SCR+'art/parts_c.json','w',encoding='utf-8'),ensure_ascii=False,separators=(',',':'))
