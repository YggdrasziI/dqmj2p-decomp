"""Search declaration orders, splits and local types of near-match game functions.

Usage: python tools/decl_search.py [budget]. Run after ninja. Writes the best
files to a temporary folder (printed); the score is an estimate of objdiff, so
check each applied file with tools/report_diff.py and revert any regression.
"""
import tempfile
import subprocess,struct,re,os,sys
D=tempfile.mkdtemp(prefix='decl_search')
INCLUDES=[d for d in ('include','libs/c/include','libs/cpp/include','libs/nitro/include','libs/runtime/include') if os.path.isdir(d)]
FLAGS='-O4,p -enum int -char signed -proc arm946e -gccext,on -fp soft -inline noauto -RTTI off -interworking -w off -sym on -gccinc -nolink -msgstyle gcc -enc SJIS -str reuse -Cpp_exceptions off'.split()+[x for d in INCLUDES for x in ('-i',d)]+'-lang=c++ -d REGION_JPN -c'.split()
# Game compiler: MWCC_DEFAULT_VERSION of tools/configure.py
CC='tools/mwccarm/%s/mwccarm.exe'%re.search(r'^MWCC_DEFAULT_VERSION = "([^"]+)"',open('tools/configure.py',encoding='utf-8').read(),re.M)[1]
import glob
def unit_path(u):
    """'src/<module>/unit_<addr>' from a unit id (0205D4D8) or that path itself."""
    if u.startswith('src/'): return u
    m=glob.glob(f'src/*/unit_{u}.cpp')
    assert len(m)==1,(u,m)
    return m[0][:-4].replace(os.sep,'/')
def compile(src,name='v'):
    p=os.path.join(D,name+'.cpp'); o=os.path.join(D,name+'.o')
    open(p,'w',encoding='latin-1',newline='\n').write(src)
    if os.path.exists(o): os.remove(o)
    r=subprocess.run([CC]+FLAGS+[p,'-o',o],capture_output=True,text=True)
    if not os.path.exists(o): return None,r.stdout+r.stderr
    return open(o,'rb').read(),''
def func(b,prefix):
    shoff=struct.unpack_from('<I',b,32)[0]; n=struct.unpack_from('<H',b,48)[0]
    secs=[struct.unpack_from('<10I',b,shoff+i*40) for i in range(n)]
    for s in secs:
        if s[1]==2:
            st=secs[s[6]]
            for i in range(0,s[5],16):
                nm,val,sz,info,oth,shn=struct.unpack_from('<IIIBBH',b,s[4]+i)
                name=b[st[4]+nm:b.index(b'\0',st[4]+nm)].decode()
                if name.startswith(prefix) and sz:
                    t=secs[shn]
                    return b[t[4]+val:t[4]+val+sz]
def sites(code):
    out=''
    for i in range(0,len(code),4):
        w=struct.unpack_from('<I',code,i)[0]
        if w==0xE1A00800: out+='0'
        elif w==0xE1A01800: out+='1'
    return out
def orig(unit,sym):
    b=open(f'build/jpn/delinks/{unit_path(unit.replace("unit_",""))}.o','rb').read()
    return func(b,sym)
def wdiff(a,b):
    if a is None or b is None: return -1
    if len(a)!=len(b): return 9999
    n=0
    for i in range(0,len(a),4):
        x=struct.unpack_from('<I',a,i)[0]; y=struct.unpack_from('<I',b,i)[0]
        if x==y: continue
        if (x>>24)&0xF==0xB and (y>>24)&0xF==0xB: continue
        n+=1
    return n
def funcs(b):
    shoff=struct.unpack_from('<I',b,32)[0]; n=struct.unpack_from('<H',b,48)[0]
    secs=[struct.unpack_from('<10I',b,shoff+i*40) for i in range(n)]
    out={}
    for s in secs:
        if s[1]==2:
            st=secs[s[6]]
            for i in range(0,s[5],16):
                nm,val,sz,info,oth,shn=struct.unpack_from('<IIIBBH',b,s[4]+i)
                if info&15!=2 or not sz or shn>=n: continue
                name=b[st[4]+nm:b.index(b'\0',st[4]+nm)].decode()
                t=secs[shn]
                out[name]=b[t[4]+val:t[4]+val+sz]
    return out
import difflib
def norm(a):
    return [ (0xEB000000 if ((w:=struct.unpack_from('<I',a,i)[0])>>24)&0xF==0xB else (w&0xFFFFF000 if (w>>16)&0xF==0xF and (w>>24)&0xE==4 else w)) for i in range(0,len(a),4)]
def sdiff(a,b):
    if a is None: return None
    x,y=norm(a),norm(b)
    m=sum(t.size for t in difflib.SequenceMatcher(None,x,y,autojunk=False).get_matching_blocks())
    return (len(x)-len(y), max(len(x),len(y))-m)
def tv(u,fn,variants,base=None):
    """variants: {name: [(old,new),...]}; prints diff words per variant."""
    u=unit_path(u)
    t=open(f'{u}.cpp',encoding='latin-1',newline='').read().replace('\r\n','\n')
    if not os.path.exists(f'build/jpn/delinks/{u}.o'):
        sys.exit(f'{u}: no delinked object (build/jpn/delinks/{u}.o); run ninja')
    O=funcs(open(f'build/jpn/delinks/{u}.o','rb').read())
    k=[n for n in O if fn in n][0]
    r={}
    for name,reps in variants.items():
        s=t
        for a,b in reps:
            assert a in s,(name,a)
            s=s.replace(a,b)
        o,e=compile(s,'tv_'+os.path.basename(u))
        if not o: print(name,'ERR',e[-300:]);continue
        F=funcs(o); c=F.get(k)
        r[name]=sdiff(c,O[k]) if c else None
        print(f'{name:18}',r[name],wdiff(c,O[k]) if c else '')
    return r
F12=[x for x in FLAGS if x!='-lang=c++' and x not in('-avoid_byte','strb')]+[]
def cc12(src,name='c12'):
    p=os.path.join(D,name+'.c'); o=os.path.join(D,name+'.o')
    open(p,'w',encoding='latin-1',newline='\n').write(src)
    if os.path.exists(o): os.remove(o)
    i=F12.index('-c')
    r=subprocess.run(['tools/mwccarm/1.2/sp2p3/mwccarm.exe']+F12[:i]+['-lang=c99','-c',p,'-o',o],capture_output=True,text=True)
    if not os.path.exists(o): return None,r.stdout+r.stderr
    return open(o,'rb').read(),''
def tc(path,fn,variants):
    t=open(path,encoding='latin-1',newline='').read().replace('\r\n','\n')
    O=funcs(open('build/jpn/delinks/'+path.rsplit('.',1)[0]+'.o','rb').read())[fn]
    for name,reps in variants.items():
        s=t
        for a,b in reps:
            assert a in s,(name,a)
            s=s.replace(a,b)
        o,e=cc12(s)
        if not o: print(name,'ERR',e[-300:]);continue
        c=funcs(o).get(fn); print(f'{name:14}',sdiff(c,O))
import json,re,os,sys,time,random
from concurrent.futures import ThreadPoolExecutor
BUDGET=int(sys.argv[1]) if len(sys.argv)>1 else 100
r=json.load(open('build/jpn/report.json'))
work={}
for u in r['units']:
    n=u['name']
    if not re.match(r'src/\w+/unit_',n): continue
    uid=n
    p=f'{uid}.cpp'
    if not os.path.exists(p): continue
    fs=[(f['name'],int(f['size'])) for f in u.get('functions',[]) if 98<=(f.get('fuzzy_match_percent') or 0)<100 and int(f['size'])<=3600]
    if fs: work[uid]=fs
print(D,len(work),sum(len(v) for v in work.values()),flush=True)
def locate(t,sym):
    m=re.match(r'_ZN(\d+)',sym)
    if m:
        l=int(m[1]); cls=sym[len(m[0]):len(m[0])+l]; rest=sym[len(m[0])+l:]
        if rest.startswith('C1') or rest.startswith('C2'): name=cls
        elif rest[0]=='D': return None
        else:
            k=re.match(r'(\d+)',rest); name=rest[len(k[0]):len(k[0])+int(k[0])]
        pat=re.escape(cls)+'::'+re.escape(name)+r'\('
    else: pat=r'\b'+re.escape(sym)+r'\('
    for m in re.finditer(r'^[^\s/*#].*'+pat+r'.*$',t,re.M):
        i=t.find('{',m.start())
        if i<0 or t[m.start():i].count(';'): continue
        j=t.find('\n}\n',i)
        if j>0: return i+2,j+1
CACHE={}
DECL=re.compile(r'^(\s+)((?:const |unsigned |volatile )*[A-Za-z_]\w*(?:<[^>]*>)?\s*\*?\s*\*?)(\w+)( = .*)?;$')
def isdecl(l):
    m=DECL.match(l)
    return m if m and m[2].strip() not in('return','delete','goto','else','case') else None
def neighbors(body):
    L=body.split('\n'); out=[]
    idx=[k for k,l in enumerate(L) if isdecl(l)]
    for k in idx:
        m=isdecl(L[k]); ind=m[1]
        # swap with next line of the same indent decl
        if k+1<len(L) and isdecl(L[k+1]) and isdecl(L[k+1])[1]==ind:
            n=list(L); n[k],n[k+1]=n[k+1],n[k]; out.append(n)
        # split initialiser
        if m[4]:
            n=list(L); n[k:k+1]=[f'{ind}{m[2]}{m[3]};',f'{ind}{m[3]}{m[4]};']; out.append(n)
        # merge decl with the next assignment
        if not m[4]:
            for q in range(k+1,min(k+8,len(L))):
                a=re.match(r'^\s+'+re.escape(m[3])+r' = (.*);$',L[q])
                if a:
                    n=list(L); n[q]=f'{L[q][:len(L[q])-len(L[q].lstrip())]}{m[2]}{m[3]} = {a[1]};'; del n[k]; out.append(n); break
        # move to the top of the function
        if not m[4] and len(ind)>4 or (k>0 and not m[4]):
            n=list(L); x=n.pop(k); n.insert(0,'    '+x.strip()); out.append(n)
        # types
        ty=m[2].strip()
        rep={'int':'u32','u32':'int','short':'int','u16':'int','bool':'int'}.get(ty)
        if rep:
            n=list(L); n[k]=f'{ind}{rep} {m[3]}{m[4] or ""};'; out.append(n)
    for k,l in enumerate(L):
        a=re.match(r'^(\s+)for \((int|u32) (\w+) = (.*)$',l)
        if a:
            n=list(L); n[k]=f'{a[1]}for ({a[3]} = {a[4]}'; n.insert(0,f'    {a[2]} {a[3]};'); out.append(n)
    return ['\n'.join(n) for n in out]
def score(src,name,O,keys):
    b,e=compile(src,name)
    if not b: return None
    F=funcs(b); s=0
    for k in keys:
        if k not in F: return None
        ck=(k,F[k])
        if ck not in CACHE: CACHE[ck]=sdiff(F[k],O[k])
        x=CACHE[ck]; s+=abs(x[0])*3+x[1]
    return s
def run(uid):
    name='pm'+uid.replace('/','_'); p=f'{uid}.cpp'
    t=open(p,encoding='latin-1',newline='').read().replace('\r\n','\n')
    op=f'build/jpn/delinks/{uid}.o'
    if not os.path.exists(op): return uid,[]
    O=funcs(open(op,'rb').read())
    res=[]
    allk=[k for k in O if k in {f for f in O}]
    b,e=compile(t,name)
    if not b: return uid,'base error'
    F0=funcs(b); keep=[k for k in O if k in F0]
    S0={k:sdiff(F0[k],O[k]) for k in keep}
    for sym,size in work[uid]:
        if sym not in O: continue
        loc=locate(t,sym)
        if not loc: res.append((sym,'noloc'));continue
        a,e_=loc; body=t[a:e_]
        s=lambda v:score(t[:a]+v+t[e_:],name,O,keep)
        best=s(body); start=best; n=0; seen={body}; bestbody=body
        if best is None: continue
        improved=True
        while improved and n<BUDGET and best>sum(abs(x[0])*3+x[1] for k,x in S0.items() if k!=sym and 0):
            improved=False
            nb=neighbors(body); random.shuffle(nb)
            for v in nb:
                if n>=BUDGET: break
                n+=1; c=s(v)
                if c is not None and c<best:
                    best=c; body=v; bestbody=v; improved=True; break
                if c is not None and c==best and random.random()<0.25 and v not in seen:
                    seen.add(v); body=v; improved=True; break
        t=t[:a]+bestbody+t[e_:]
        res.append((sym,start,best,n))
        print(uid,sym[-28:],start,'->',best,n,flush=True)
        open(os.path.join(D,'p2_'+uid.replace('/','_')+'.cpp'),'w',encoding='latin-1',newline='\n').write(t)
    return uid,res
with ThreadPoolExecutor(10) as ex:
    for u,rs in ex.map(run,list(work)): print('DONE',u,flush=True)



