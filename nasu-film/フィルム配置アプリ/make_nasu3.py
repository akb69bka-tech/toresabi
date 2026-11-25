import sys, numpy as np
from PIL import Image, ImageFilter, ImageEnhance
from scipy import ndimage as ndi
D=sys.argv[1]; OUT=sys.argv[2]
PX=3   # px/mm
def cut(fn):
    im=Image.open(f'{D}/{fn}').convert('RGB'); a=np.asarray(im).astype(int)
    fg=a.min(axis=2)<228
    fg=ndi.binary_opening(fg,iterations=1); lab,n=ndi.label(fg); sizes=ndi.sum(fg,lab,range(1,n+1)); fg=lab==(1+int(np.argmax(sizes)))
    fg=ndi.binary_fill_holes(fg); fg=ndi.binary_erosion(fg,iterations=1)
    al=Image.fromarray((fg*255).astype('uint8')).filter(ImageFilter.GaussianBlur(0.8))
    im=ImageEnhance.Color(ImageEnhance.Contrast(im).enhance(1.15)).enhance(1.12)
    rgba=im.copy(); rgba.putalpha(al); return rgba.crop(rgba.getbbox())
picks=[('r0_62.jpg',-3,False),('r0_26.jpg',2,True),('r0_296.jpg',4,False)]
canvas=Image.new('RGBA',(200*PX,300*PX),(0,0,0,0))
for (fn,rot,flip),cx in zip(picks,(56,100,144)):
    e=cut(fn)
    if flip: e=e.transpose(Image.FLIP_LEFT_RIGHT)
    k=248*PX/e.height; e=e.resize((round(e.width*k),round(e.height*k)),Image.LANCZOS).rotate(rot,resample=Image.BICUBIC,expand=True)
    # やわらかい影
    sh=Image.new('RGBA',e.size,(0,0,0,0)); sh.putalpha(e.getchannel('A').point(lambda v:int(v*0.28)).filter(ImageFilter.GaussianBlur(6)))
    x=cx*PX-e.width//2; y=round(150*PX-e.height/2)+4*PX
    canvas.alpha_composite(sh,(x+5,y+7)); canvas.alpha_composite(e,(x,y))
canvas.save(OUT,optimize=True); print(canvas.size)
