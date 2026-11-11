"""Local, dependency-free Premiere PRPROJ to Final Cut Pro 7 XML converter.

Reads the project object graph, never uploads files, never changes the input.
Implemented against the supplied Premiere projects; unsupported effects are
listed explicitly. XML is an interchange representation, not a Premiere clone.
"""
from __future__ import annotations
import argparse, collections, copy, csv, gzip, hashlib, json, math, pathlib, re
import struct, urllib.parse, xml.etree.ElementTree as ET, zlib
from fractions import Fraction

TICKS = 254016000000
DEFAULT_TPF = 8475667200
EMPTY = ET.Element('empty')

def txt(e, path, default=''):
    return e.findtext(path, default) if e is not None else default

def num(e, path, default=0):
    return float(txt(e, path, str(default)))

def integer(e, path, default=0):
    return int(txt(e, path, str(default)))

def truth(e, path, default=False):
    return txt(e, path, str(default)).lower() in ('true','1')

def fmt(value):
    if isinstance(value, bool): return 'TRUE' if value else 'FALSE'
    if isinstance(value, float):
        if not math.isfinite(value): raise ValueError('Non-finite numeric value')
        return str(round(value)) if abs(value-round(value))<1e-8 else format(value,'.12g')
    return str(value)

def add(parent, tag, value=None, **attrs):
    e=ET.SubElement(parent,tag,{k:fmt(v) for k,v in attrs.items()})
    if value is not None:e.text=fmt(value)
    return e

def rounded(value):
    return math.floor(value+0.5)

def dimensions(e):
    values=[int(v) for v in txt(e,'FrameRect','0,0,1920,1080').split(',')]
    return values[2]-values[0], values[3]-values[1]

def rate_values(tpf):
    fps=Fraction(TICKS,int(tpf))
    for base in (24,30,48,60,120,240):
        if abs(float(fps)-base*1000/1001)<0.0001:return base,True
    return rounded(float(fps)),False

def ticks_per_frame(fps):
    for base in (24,30,48,60,120,240):
        if abs(float(fps)-base*1000/1001)<0.0006:return TICKS*1001//(base*1000)
    return rounded(TICKS/float(fps))

def timecode_frames(value,base):
    h,m,s,f=map(int,re.split('[:;]',value));frames=((h*60+m)*60+s)*base+f
    if ';' in value and base in (30,60):
        minutes=h*60+m;frames-=(2 if base==30 else 4)*(minutes-minutes//10)
    return frames

def rate(parent,tpf):
    base,ntsc=rate_values(tpf);r=add(parent,'rate')
    add(r,'timebase',base);add(r,'ntsc',ntsc)

def tc_string(frames,tpf,drop=False):
    base,ntsc=rate_values(tpf);n=rounded(frames)
    if drop and ntsc and base in (30,60):
        df=2 if base==30 else 4
        per10=base*600-df*9;permin=base*60-df
        tens,rem=divmod(n,per10)
        n+=df*9*tens+(df*((rem-df)//permin) if rem>=df else 0)
    h,rem=divmod(n,base*3600);m,rem=divmod(rem,base*60);s,f=divmod(rem,base)
    return f'{h:02d}:{m:02d}:{s:02d}{";" if drop else ":"}{f:02d}'

def timecode(parent,ticks,tpf,drop=False):
    tc=add(parent,'timecode');rate(tc,tpf);frames=rounded(Fraction(ticks,int(tpf)))
    add(tc,'string',tc_string(frames,tpf,drop));add(tc,'frame',frames)
    add(tc,'displayformat','DF' if drop else 'NDF')

def pathurl(path):
    path=path.replace('\\','/')
    if path.startswith('//'):return 'file:'+urllib.parse.quote(path,safe='/:')
    return 'file://localhost/'+urllib.parse.quote(path.lstrip('/'),safe='/:')

def safe_name(name):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',name).strip(' .')[:110] or 'Sequencia'

def constant_parameter(effect,pid,value,name=None):
    p=add(effect,'parameter');add(p,'parameterid',pid);add(p,'name',name or pid)
    value_element(p,value)
    return p

def value_element(parent,value):
    if isinstance(value,tuple):
        v=add(parent,'value');add(v,'horiz',value[0]);add(v,'vert',value[1])
    else:add(parent,'value',value)

def effect(parent,name,eid,media='video',category='motion',kind='motion'):
    f=add(parent,'filter');e=add(f,'effect')
    for k,v in [('name',name),('effectid',eid),('effectcategory',category),('effecttype',kind),('mediatype',media)]:add(e,k,v)
    return e

def parse_value(value):
    if value.lower() in ('true','false'):return value.lower()=='true'
    if ':' in value:return tuple(float(v) for v in value.split(':'))
    if re.fullmatch(r'[+-]?\d+',value):return int(value)
    return float(value)

def param_value(e,default=0):
    sk=txt(e,'StartKeyframe')
    if sk:return parse_value(sk.split(',')[1])
    cv=txt(e,'CurrentValue')
    return parse_value(cv) if cv else default

def param_keys(e):
    return [(int(parts[0]),parse_value(parts[1]),int(parts[2]))
            for row in txt(e,'Keyframes').split(';') if row.strip()
            for parts in [row.split(',')]]

class Project:
    def __init__(self,path,metadata=None):
        self.media_path_resolver = lambda path: path
        self.output_path_resolver = lambda path: path
        self.path=pathlib.Path(path);data=self.path.read_bytes()
        self.sha256=hashlib.sha256(data).hexdigest()
        if data[:2]==b'\x1f\x8b':data=gzip.decompress(data)
        self.root=ET.fromstring(data)
        if self.root.tag!='PremiereData':raise ValueError('Expected PremiereData XML')
        self.ids={e.get('ObjectID') or e.get('ObjectUID'):e for e in self.root if e.get('ObjectID') or e.get('ObjectUID')}
        self.warnings=[];self.media={};self.sequences={};self.clips={}
        for m in self.root.findall('Media'):
            uid=m.get('ObjectUID');v=self.ref(m.find('VideoStream'));a=self.ref(m.find('AudioStream'))
            p=txt(m,'FilePath') or txt(m,'ActualMediaFilePath')
            channels=json.loads(txt(a,'AudioChannelLayout','[]'))
            self.media[uid]={'id':uid,'el':m,'name':txt(m,'Title'), 'path':p,'video':v,'audio':a,
                'tpf':integer(v,'FrameRate',DEFAULT_TPF),'size':dimensions(v),
                'duration':max(integer(v,'Duration'),integer(a,'Duration')),
                'channels':len(channels),'samplerate':rounded(Fraction(TICKS,integer(a,'FrameRate',5292000))),
                'still':truth(v,'IsStill') or truth(m,'Infinite'),
                'image_sequence':truth(v,'IsNumberedStills'),
                'timecode':integer(m,'AlternateStart') if truth(m,'UseAlternateStart') else integer(m,'Start'),
                'synthetic':bool(p and re.fullmatch(r'\d+',p))}
        # Optional observations from the destination editor resolve stale native
        # timecodes and VFR averages without changing any source trim in seconds.
        for m in self.media.values():
            actual=(metadata or {}).get(m['path'].casefold())
            if not actual or m['still']:continue
            if m['video'] is None:
                # Premiere may cache an XMP start offset on an audio-only file
                # that Resolve reads as starting at zero. That offset prevents
                # conforming short SFX and music even when the WAV exists.
                if actual['start_timecode'] in ('00:00:00:00','00:00:00;00') and m['timecode']:
                    self.warn({'name':m['name']},'media_metadata_alignment','Timecode de identificação do áudio ajustado para zero, conforme a mídia lida no DaVinci. Cortes mantidos.')
                    m['timecode']=0
                continue
            tpf=ticks_per_frame(actual['fps']);base,ntsc=rate_values(tpf)
            tc=timecode_frames(actual['start_timecode'],base)
            if rate_values(m['tpf'])!=(base,ntsc) or abs(m['timecode']/m['tpf']-tc)>0.51:
                self.warn({'name':m['name']},'media_metadata_alignment',
                    f'Metadados de identificação ajustados aos lidos no DaVinci: {actual["fps"]} fps, TC {actual["start_timecode"]}. Pontos de corte em segundos mantidos.')
            m['tpf']=tpf;m['timecode']=tc*tpf;m['drop']=';' in actual['start_timecode']
        for s in self.root.findall('Sequence'):
            groups=[self.ref(x) for x in s.findall('TrackGroups/TrackGroup/Second')]
            vg=next((g for g in groups if g is not None and g.tag=='VideoTrackGroup'),EMPTY)
            ag=next((g for g in groups if g is not None and g.tag=='AudioTrackGroup'),EMPTY)
            uid=s.get('ObjectUID');seq={'id':uid,'el':s,'name':txt(s,'Name'),'tpf':integer(vg,'TrackGroup/FrameRate',DEFAULT_TPF),
                'size':dimensions(vg),'samplerate':rounded(Fraction(TICKS,integer(ag,'TrackGroup/FrameRate',5292000))),
                'video':[],'audio':[],'duration':0,'timecode':0,
                'drop':integer(s,'Node/Properties/MZ.Sequence.VideoTimeDisplayFormat') in (102,106)}
            for kind,g in [('video',vg),('audio',ag)]:
                for tr in sorted(g.findall('TrackGroup/Tracks/Track'),key=lambda e:int(e.get('Index','0'))):
                    t=self.ref(tr);base=t.find('ClipTrack/Track');props=base.find('Node/Properties')
                    track={'id':t.get('ObjectUID'),'el':t,'name':txt(props,'MZ.TrackName') or txt(base,'Name'),
                           'index':len(seq[kind]),'clips':[],'transitions':[],
                           'enabled':not (truth(props,'MZ.TrackMuted') or truth(t,'AudioTrack/MutedBySolo')),'locked':truth(props,'MZ.TrackLocked')}
                    for itemref in t.findall('ClipTrack/ClipItems/TrackItems/TrackItem'):
                        it=self.ref(itemref);c=self.parse_clip(it,seq,kind)
                        track['clips'].append(c);self.clips[c['id']]=c;seq['duration']=max(seq['duration'],c['end'])
                    for itemref in t.findall('ClipTrack/TransitionItems/TrackItems/TrackItem'):
                        track['transitions'].append(self.parse_transition(self.ref(itemref)))
                    seq[kind].append(track)
            self.sequences[uid]=seq
        self.sequence_for_master={}
        for m in self.root.findall('MasterClip'):
            for c in m.findall('Clips/Clip'):
                cl=self.ref(c)
                if cl.tag not in ('VideoClip','AudioClip'):continue
                src=self.ref(cl.find('Clip/Source'))
                if src is None:continue
                nested=self.ref(src.find('SequenceSource/Sequence'))
                if nested is not None:self.sequence_for_master[m.get('ObjectUID')]=nested.get('ObjectUID');break
        self.links=[]
        for e in self.root.findall('Link'):
            members=[n.get('ObjectRef') for n in e.iter() if n.get('ObjectRef') in self.clips]
            if members:self.links.append(members)

    def ref(self,e):
        if e is None:return None
        key=e.get('ObjectRef') or e.get('ObjectURef')
        if key not in self.ids:raise ValueError(f'Unresolved native reference: {key}')
        return self.ids[key]

    def components(self,owner):
        chain=self.ref(owner.find('ComponentOwner/Components')) if owner is not None else None
        return [self.ref(n) for n in chain.findall('ComponentChain/Components/Component')] if chain is not None else []

    def params(self,component):
        return [self.ref(n) for n in component.findall('.//Params/Param')]

    def markers(self,clip):
        container=self.ref(clip.find('Clip/MarkerOwner/Markers')) if clip is not None else None
        result=[]
        if container is not None:
            for e in container.iter():
                if e.get('ObjectRef'):
                    marker=self.ref(e)
                    if marker.tag=='Marker' and txt(marker,'DVAMarker'):
                        result.append(json.loads(txt(marker,'DVAMarker')).get('DVAMarker',{}))
        return result

    def warn(self,c,code,detail):
        row={'sequence':c.get('sequence',''),'clip_id':c.get('id',''),'clip':c.get('name',''),'code':code,'detail':detail}
        if row not in self.warnings:self.warnings.append(row)

    def parse_transition(self,it):
        b=it.find('TransitionTrackItem');ti=b.find('TrackItem')
        return {'id':it.get('ObjectID'),'start':integer(ti,'Start'),'end':integer(ti,'End'),
            'alignment':integer(b,'Alignment'),'incoming':truth(b,'HasIncomingClip'),'outgoing':truth(b,'HasOutgoingClip'),
            'name':txt(b,'DisplayName'),'match':txt(b,'MatchName'),'shape':txt(it,'FadeShapeValue')}

    def parse_clip(self,it,seq,kind):
        b=it.find('ClipTrackItem');ti=b.find('TrackItem');sc=self.ref(b.find('SubClip'))
        cl=self.ref(sc.find('Clip'));base=cl.find('Clip');src=self.ref(base.find('Source'))
        media=self.ref(src.find('MediaSource/Media'));nested=self.ref(src.find('SequenceSource/Sequence'))
        c={'id':it.get('ObjectID'),'el':it,'native':cl,'master':self.ref(sc.find('MasterClip')),
           'name':txt(sc,'Name'),'sequence':seq['name'],'kind':kind,
           'start':integer(ti,'Start'),'end':integer(ti,'End'),'in':integer(base,'InPoint'),'out':integer(base,'OutPoint'),
           'speed':num(base,'PlaybackSpeed',1),'reverse':truth(base,'PlayBackwards'),
           'media':media.get('ObjectUID') if media is not None else None,
           'nested':nested.get('ObjectUID') if nested is not None else None,
           'effects':self.components(b),'gain':num(cl,'Gain',1),
           'enabled':not truth(ti,'Node/Properties/MZ.TrackItemIsDisabled'),
           'adjustment':truth(cl,'AdjustmentLayer'),
           'head':self.parse_transition(self.ref(b.find('HeadTransition'))) if b.find('HeadTransition') is not None else None,
           'tail':self.parse_transition(self.ref(b.find('TailTransition'))) if b.find('TailTransition') is not None else None}
        channels=[]
        for sec in cl.findall('SecondaryContents/SecondaryContentItem'):
            ch=self.ref(sec);channels.append(integer(ch,'ChannelIndex')+1)
        if not channels and kind=='audio':channels=list(range(1,len(json.loads(txt(cl,'AudioChannelLayout','[{}]')))+1))
        c['channels']=channels
        if not c['media'] and not c['nested']:raise ValueError(f'Unsupported source {src.tag} for {c["name"]}')
        if c['adjustment']:self.warn(c,'adjustment_layer','Camada de ajuste mantida como marcador; seus efeitos precisam ser refeitos.')
        for comp in c['effects']:
            match=txt(comp,'MatchName') or txt(comp,'FilterMatchName')
            supported=(match in ('AE.ADBE Motion','AE.ADBE Opacity','Internal Audio Fill Right','Internal Audio Fill Left') or match.startswith('Internal Volume') or match.startswith('Internal Channel Volume'))
            if not supported:self.warn(c,'unsupported_effect',txt(comp,'Component/DisplayName') or txt(comp,'AudioComponent/Component/DisplayName') or match)
        return c

class Writer:
    def __init__(self,p,assets_dir):
        self.p=p;self.assets_dir=assets_dir;self.files_seen=set();self.seq_seen=set();self.clip_elements={};self.clip_index={}
        self.masters_seen=set();self.counter=0;self.project_mode=False

    def fid(self,uid):return 'file-'+uid
    def sid(self,uid):return 'sequence-'+uid

    def video_format(self,parent,tpf,size):
        sc=add(parent,'samplecharacteristics');rate(sc,tpf)
        add(sc,'width',size[0]);add(sc,'height',size[1]);add(sc,'anamorphic',False)
        add(sc,'pixelaspectratio','square');add(sc,'fielddominance','none')

    def file(self,parent,uid,seq_tpf):
        m=self.p.media[uid]
        if m.get('image_sequence'):
            self.p.warn({'name':m['name']},'image_sequence_xml','Sequência de imagens preservada no .fcproj; no XML, o editor precisa interpretar os quadros numerados como sequência de imagens.')
        # Audio-only source coordinates use the timeline clock. A project can
        # contain several clocks, plus bin master clips at the default rate.
        # Keep each clock's definition distinct in the combined project XML.
        key=uid+'-audio-'+str(seq_tpf) if self.project_mode and m['video'] is None else uid
        f=add(parent,'file',id=self.fid(key))
        if key in self.files_seen:return
        self.files_seen.add(key);tpf=m['tpf'] if m['video'] is not None else seq_tpf
        add(f,'name',m['name'])
        if m['synthetic']:
            if m['path']=='1112293707':
                out=self.assets_dir/'video_preto.png';out.parent.mkdir(parents=True,exist_ok=True)
                if not out.exists():write_solid_png(out,1920,1080,(0,0,0,255))
                add(f,'pathurl',pathurl(self.p.output_path_resolver(str(out.resolve()))))
            else:
                out=self.assets_dir/('RECRIAR_'+safe_name(m['name'])+'.png');out.parent.mkdir(parents=True,exist_ok=True)
                if not out.exists():write_solid_png(out,*m['size'],(0,0,0,0))
                add(f,'pathurl',pathurl(self.p.output_path_resolver(str(out.resolve()))))
                self.p.warn({'name':m['name']},'synthetic_media','Mídia sintética substituída por PNG transparente; recriar conteúdo original: '+m['path'])
        elif m['path']:add(f,'pathurl',pathurl(m['path']))
        rate(f,tpf);add(f,'duration',max(1,math.ceil(Fraction(m['duration'],tpf))))
        timecode(f,m['timecode'],tpf,m.get('drop',False));media=add(f,'media')
        if m['video'] is not None:
            v=add(media,'video');self.video_format(v,tpf,m['size'])
            if m['still']:add(v,'stillframe',True)
        if m['audio'] is not None:
            a=add(media,'audio');sc=add(a,'samplecharacteristics');add(sc,'depth',16)
            add(sc,'samplerate',m['samplerate']);add(a,'channelcount',m['channels'])

    def parameter(self,e,pid,param,default,transform=lambda x:x,tpf=DEFAULT_TPF,offset=0):
        p=add(e,'parameter');add(p,'parameterid',pid);add(p,'name',pid)
        keys=param_keys(param)
        if keys:
            for ticks,val,interp in keys:
                k=add(p,'keyframe');add(k,'when',rounded(Fraction(ticks-offset,tpf)));value_element(k,transform(val))
        else:value_element(p,transform(param_value(param,default)))
        return p

    def filters(self,x,c,seq,ch=None,clock_tpf=None):
        p=self.p;tpf=clock_tpf or seq['tpf'];scale=None;channel_volume=None;volume=None;fill=None;gain=c['gain']
        for comp in c['effects']:
            match=txt(comp,'MatchName') or txt(comp,'FilterMatchName');params=p.params(comp)
            pm={integer(v,'ParameterID',i+1):v for i,v in enumerate(params)}
            if match=='AE.ADBE Motion':
                e=effect(x,'Basic Motion','basic')
                self.parameter(e,'scale',pm.get(2),100,tpf=tpf)
                self.parameter(e,'rotation',pm.get(5),0,tpf=tpf)
                self.parameter(e,'center',pm.get(1),(.5,.5),lambda v:(v[0]-.5,v[1]-.5),tpf)
                self.parameter(e,'centerOffset',pm.get(6),(.5,.5),lambda v:(v[0]-.5,v[1]-.5),tpf)
                if not param_value(pm.get(4),True):
                    height=param_value(pm.get(2),100);width=param_value(pm.get(3),100)
                    if height and width:constant_parameter(e,'aspect',100*(height/width-1) if height>=width else -100*(width/height-1))
                    p.warn(c,'nonuniform_scale','Escalas horizontal e vertical convertidas em escala/aspecto; conferir no editor de destino.')
                for pid,field in [(8,'left'),(9,'top'),(10,'right'),(11,'bottom')]:
                    if param_value(pm.get(pid),0) or param_keys(pm.get(pid)):
                        crop=effect(x,'Crop','crop');self.parameter(crop,field,pm.get(pid),0,tpf=tpf)
                if any(param_keys(v) for v in params):p.warn(c,'keyframe_interpolation','Valores e tempos dos keyframes preservados com interpolação linear; curvas Bézier precisam de conferência.')
            elif match=='AE.ADBE Opacity':
                e=effect(x,'Opacity','opacity');self.parameter(e,'opacity',pm.get(1),100,tpf=tpf)
                blend=param_value(pm.get(2),0)
                if blend!=0:p.warn(c,'blend_mode',f'Modo de mesclagem Premiere {blend}; conferir composição.')
            elif match.startswith('Internal Volume'):
                volume=params[1] if len(params)>1 else None
                if params and param_value(params[0],False):gain=0
            elif match.startswith('Internal Channel Volume'):
                if ch is not None and len(params)>ch:channel_volume=params[ch]
            elif match in ('Internal Audio Fill Right','Internal Audio Fill Left'):
                fill=2 if 'Right' in match else 1
        if c['kind']=='audio':
            from .audio import gain_at, gain_curve
            v=gain_at(p,c,ch,c['in'])
            keys=gain_curve(p,c,ch,tpf,logarithmic=getattr(self,'audio_db_interpolation',False))
            if keys:
                e=effect(x,'Audio Levels','audiolevels','audio','audiolevels','audiolevels')
                param=add(e,'parameter');add(param,'parameterid','level');add(param,'name','Level')
                frames={rounded(Fraction(t,tpf)):v for t,v in keys}
                for t,v in sorted(frames.items()):
                    k=add(param,'keyframe');add(k,'when',t)
                    add(k,'value',v)
                p.warn(c,'audio_keyframes','Automação de volume exportada; interpolação linear no XML.')
            elif abs(v-1)>1e-7:
                e=effect(x,'Audio Levels','audiolevels','audio','audiolevels','audiolevels');constant_parameter(e,'level',v,'Level')
            if len(c['channels'])>=2:
                e=effect(x,'Audio Pan','audiopan','audio','audiopan','audiopan')
                pan=-1 if ch in (1,5) else (1 if ch in (2,6) else 0)
                constant_parameter(e,'pan',pan,'Pan')
            if fill is not None:
                x.find('sourcetrack/trackindex').text=str(fill)
        if c['speed']!=1 or c['reverse']:
            e=effect(x,'Time Remap','timeremap',c['kind'])
            for pid,val in [('variablespeed',0),('speed',abs(c['speed'])*100),('reverse',c['reverse']),('frameblending',False)]:constant_parameter(e,pid,val)
            pin=float(x.findtext('in'));pout=float(x.findtext('out'))
            if c['media'] and not c['reverse'] and abs(c['in']/tpf/abs(c['speed'])-rounded(c['in']/tpf/abs(c['speed'])))>1e-7:
                speed=abs(c['speed']);source_tpf=self.p.media[c['media']]['tpf']
                source_in=c['in']/source_tpf;slope=speed*tpf/source_tpf
                if c['head']:source_in-=(c['start']-c['head']['start'])/source_tpf*speed
                anchor=source_in-pin*slope
                full=max(math.ceil(self.p.media[c['media']]['duration']/tpf/speed),rounded(pout)+1)
                x.find('duration').text=str(full)
                graph=constant_parameter(e,'graphdict',0)
                add(graph,'valuemin',0);add(graph,'valuemax',self.p.media[c['media']]['duration']/source_tpf-1)
                for when,value,flag in [(0,anchor,'speedkfstart'),(pin,source_in,'speedkfin'),(pout,source_in+(pout-pin)*slope,'speedkfout'),(full,anchor+(full-1)*slope+1,'speedkfend')]:
                    k=add(graph,'keyframe');add(k,'when',rounded(when));add(k,'value',value);add(k,'speedvirtualkf',True);add(k,flag,True)
                interpolation=add(graph,'interpolation');add(interpolation,'name','FCPCurve')

    def transition(self,parent,tr,seq,kind):
        x=add(parent,'transitionitem');tpf=seq['tpf']
        add(x,'start',rounded(Fraction(tr['start'],tpf)));add(x,'end',rounded(Fraction(tr['end'],tpf)))
        alignment='center'
        if not tr['outgoing']:alignment='start-black'
        elif not tr['incoming']:alignment='end-black'
        elif tr['alignment']==0:alignment='start'
        elif tr['alignment']==tr['end']-tr['start']:alignment='end'
        add(x,'alignment',alignment);rate(x,tpf);e=add(x,'effect')
        if kind=='audio':
            name='Cross Fade (+3dB)' if tr['match']=='Constant Power' else 'Cross Fade (0dB)'
            eid='KGAudioTransCrossFade3dB' if tr['match']=='Constant Power' else 'KGAudioTransCrossFade'
        else:name=eid='Cross Dissolve'
        for k,v in [('name',name),('effectid',eid),('effectcategory','Dissolve'),('effecttype','transition'),('mediatype',kind),('wipecode',0),('wipeaccuracy',100),('startratio',0),('endratio',1),('reverse',False)]:add(e,k,v)
        if tr['match'] not in ('Constant Power','AE.ADBE Cross Dissolve New'):
            self.p.warn({'id':tr['id'],'sequence':seq['name'],'name':tr['name']},'transition_approximation',f'Transição substituída por {name}; duração preservada.')

    def clip(self,parent,c,seq,ch,track_index,clip_index):
        if c['kind']=='video' and 0<abs(c['speed'])<0.01 and c['media']:
            original=self.p.media[c['media']];frame=math.floor(c['in']/original['tpf'])
            hold=self.assets_dir/(self.p.path.stem+'_'+c['id']+'_quadro_'+str(frame)+'.png')
            if hold.exists():
                uid=c['media']+'-hold-'+str(frame)
                self.p.media[uid]=dict(original,id=uid,path=self.p.output_path_resolver(str(hold.resolve())),still=True,synthetic=False,
                    audio=None,channels=0,timecode=0,duration=seq['tpf'],tpf=seq['tpf'])
                self.p.warn(c,'frame_hold','Trecho com velocidade de 0,1% representado por imagem estática do quadro '+str(frame)+' da mídia original.')
                c=dict(c,media=uid,speed=1,reverse=False,in_=0)
                c['in']=0;c['out']=c['end']-c['start']
            else:self.p.warn(c,'very_slow_speed','Velocidade abaixo de 1%; conferir suporte a congelamento no editor de destino.')
        self.counter+=1;cid=f'clipitem-{self.counter}'
        x=add(parent,'clipitem',id=cid);self.clip_elements.setdefault(c.get('link_id',c['id']),[]).append(x)
        self.clip_index[cid]=(c['kind'],track_index,clip_index)
        tpf=seq['tpf'];speed=abs(c['speed']) or 1
        # Resolve maps source trim coordinates using nominal frame counts.
        # Conform the NTSC clock for mixed integer/1000:1001 media rates.
        clock_tpf=tpf
        if c['media'] and self.p.media[c['media']]['video'] is not None and not self.p.media[c['media']]['still']:
            source_ntsc=rate_values(self.p.media[c['media']]['tpf'])[1]
            seq_base,seq_ntsc=rate_values(tpf)
            if source_ntsc!=seq_ntsc:clock_tpf=TICKS*(1001 if source_ntsc else 1)//(seq_base*(1000 if source_ntsc else 1))
        start=Fraction(c['start'],tpf);end=Fraction(c['end'],tpf)
        headstart=Fraction(c['head']['start'],tpf) if c['head'] else start
        tailend=Fraction(c['tail']['end'],tpf) if c['tail'] else end
        pin=c['in']/clock_tpf/speed-float(start-headstart)*tpf/clock_tpf
        pout=c['out']/clock_tpf/speed+float(tailend-end)*tpf/clock_tpf
        if c['reverse'] and c['media']:
            duration=self.p.media[c['media']]['duration']/clock_tpf/speed
            pin,pout=duration-c['out']/clock_tpf/speed-float(start-headstart)*tpf/clock_tpf,duration-c['in']/clock_tpf/speed+float(tailend-end)*tpf/clock_tpf
        if c['speed']!=1:
            pin=rounded(pin);pout=pin+rounded(float(tailend-headstart)*tpf/clock_tpf)
        elif clock_tpf!=tpf:pin=rounded(pin);pout=rounded(pout)
        if c['media'] and self.p.media[c['media']]['still']:
            pin=0;pout=float(tailend-headstart)
        add(x,'name',c['name']);add(x,'enabled',c['enabled']);add(x,'duration',float(tailend-headstart)*speed)
        rate(x,tpf);add(x,'start',-1 if c['head'] else rounded(start));add(x,'end',-1 if c['tail'] else rounded(end))
        add(x,'in',pin);add(x,'out',pout)
        if c['kind']=='video':add(x,'anamorphic',False)
        if c['nested']:self.sequence(x,c['nested'])
        else:
            self.file(x,c['media'],tpf)
            if self.p.media[c['media']]['still']:add(x,'stillframe',True)
        if c['kind']=='audio':
            source=add(x,'sourcetrack');add(source,'mediatype','audio');add(source,'trackindex',ch or 1)
        self.filters(x,c,seq,ch,clock_tpf)
        self.write_markers(x,self.p.markers(c['native']),tpf)
        if c['speed']!=1 or c['reverse']:
            add(x,'pproTicksIn',c['in']);add(x,'pproTicksOut',c['out'])
        return x

    def write_markers(self,parent,markers,tpf):
        for marker in markers:
            start=marker.get('mStartTime',{}).get('ticks',0);duration=marker.get('mDuration',{}).get('ticks',0)
            m=add(parent,'marker');add(m,'name',marker.get('mName') or 'Marcador')
            add(m,'comment',marker.get('mComment') or '')
            add(m,'in',rounded(Fraction(start,tpf)));add(m,'out',rounded(Fraction(start+duration,tpf)) if duration else -1)

    def master_clip(self,parent,master):
        uid=master.get('ObjectUID')
        if uid in self.p.sequence_for_master:
            self.sequence(parent,self.p.sequence_for_master[uid]);return
        streams=[]
        for ref in master.findall('Clips/Clip'):
            cl=self.p.ref(ref)
            if cl.tag not in ('VideoClip','AudioClip'):continue
            src=self.p.ref(cl.find('Clip/Source'))
            if src is None:continue
            media=self.p.ref(src.find('MediaSource/Media'))
            if media is not None:streams.append((cl,media.get('ObjectUID')))
        if not streams:return
        x=add(parent,'clip',id='masterclip-'+uid)
        if uid in self.masters_seen:return
        self.masters_seen.add(uid)
        media=self.p.media[streams[0][1]];tpf=media['tpf'];dur=max(1,math.ceil(media['duration']/tpf))
        add(x,'masterclipid','masterclip-'+uid);add(x,'ismasterclip',True);add(x,'name',txt(master,'Name'))
        add(x,'duration',dur);rate(x,tpf);add(x,'in',0);add(x,'out',dur)
        outmedia=add(x,'media')
        for kind in ('video','audio'):
            selected=[(cl,mid) for cl,mid in streams if cl.tag.lower().startswith(kind)]
            if not selected:continue
            k=add(outmedia,kind)
            for cl,mid in selected:
                count=self.p.media[mid]['channels'] if kind=='audio' else 1
                for channel in range(1,count+1):
                    track=add(k,'track');self.counter+=1;c=add(track,'clipitem',id=f'clipitem-{self.counter}')
                    add(c,'masterclipid','masterclip-'+uid);add(c,'name',txt(master,'Name'));add(c,'enabled',True)
                    add(c,'duration',dur);rate(c,tpf);add(c,'start',-1);add(c,'end',-1);add(c,'in',0);add(c,'out',dur)
                    self.file(c,mid,tpf)
                    if kind=='audio':st=add(c,'sourcetrack');add(st,'mediatype','audio');add(st,'trackindex',channel)

    def project_items(self,parent,item):
        if item.tag=='ClipProjectItem':
            master=self.p.ref(item.find('MasterClip'))
            if master is not None:self.master_clip(parent,master)
            return
        dest=parent
        if item.tag=='BinProjectItem':
            b=add(parent,'bin');add(b,'name',txt(item,'ProjectItem/Name'));dest=add(b,'children')
        for ref in item.findall('ProjectItemContainer/Items/Item'):
            self.project_items(dest,self.p.ref(ref))

    def audio_tracks(self,s):
        from .audio import expand_tracks
        return expand_tracks(self.p,s)

    def sequence(self,parent,uid):
        x=add(parent,'sequence',id=self.sid(uid))
        if uid in self.seq_seen:return x
        self.seq_seen.add(uid);s=self.p.sequences[uid];tpf=s['tpf']
        add(x,'uuid',uid);add(x,'name',s['name']);add(x,'duration',rounded(Fraction(s['duration'],tpf)))
        rate(x,tpf);timecode(x,s['timecode'],tpf,s['drop']);media=add(x,'media')
        for kind in ('video','audio'):
            m=add(media,kind);f=add(m,'format')
            if kind=='video':self.video_format(f,tpf,s['size'])
            else:sc=add(f,'samplecharacteristics');add(sc,'depth',16);add(sc,'samplerate',s['samplerate'])
            trackindex=0
            for tr in (self.audio_tracks(s) if kind=='audio' else s[kind]):
                nch=max([len(c['channels']) for c in tr['clips']] or [1]) if kind=='audio' else 1
                for channel in range(nch):
                    trackindex+=1;out=add(m,'track')
                    if tr['name']:add(out,'name',tr['name']+(f' ch{channel+1}' if nch>1 else ''))
                    emitted_transitions=set();ci=0
                    for c in sorted(tr['clips'],key=lambda c:(c['start'],c['end'])):
                        if c['adjustment']:continue
                        if kind=='audio' and channel>=len(c['channels']):continue
                        # XMEML transitions are attached by document adjacency,
                        # including when a fade covers the entire short clip.
                        if c['head'] and c['head']['id'] not in emitted_transitions:
                            self.transition(out,c['head'],s,kind);emitted_transitions.add(c['head']['id'])
                        ci+=1;self.clip(out,c,s,c['channels'][channel] if kind=='audio' else None,trackindex,ci)
                        if c['tail'] and c['tail']['id'] not in emitted_transitions:
                            self.transition(out,c['tail'],s,kind);emitted_transitions.add(c['tail']['id'])
                    add(out,'enabled',tr['enabled']);add(out,'locked',tr['locked'])
        for tr in s['video']:
            for c in tr['clips']:
                if c['adjustment']:
                    marker=add(x,'marker');add(marker,'name','Camada de ajuste: '+c['name'])
                    add(marker,'comment','Efeitos de camada de ajuste precisam ser refeitos. Consulte o relatório.')
                    add(marker,'in',rounded(Fraction(c['start'],tpf)));add(marker,'out',rounded(Fraction(c['end'],tpf)))
        return x

    def link_clips(self):
        groups=list(self.p.links)
        # Preserve source audio channel grouping even if the native object graph
        # does not contain a separate Link object.
        groups.extend([[cid] for cid,items in self.clip_elements.items() if len(items)>1])
        for members in groups:
            elements=[e for cid in members for e in self.clip_elements.get(cid,[])]
            for e in elements:
                existing={n.findtext('linkclipref') for n in e.findall('link')}
                for target in elements:
                    cid=target.get('id')
                    if cid in existing:continue
                    l=add(e,'link');add(l,'linkclipref',cid);kind,track,index=self.clip_index[cid]
                    add(l,'mediatype',kind);add(l,'trackindex',track);add(l,'clipindex',index)
                    if kind=='audio':add(l,'groupindex',1)

    def document(self,sequence=None):
        root=ET.Element('xmeml',version='5')
        if sequence:self.sequence(root,sequence)
        else:
            self.project_mode=True
            proj=add(root,'project');add(proj,'name',self.p.path.stem);children=add(proj,'children')
            rootitem=self.p.root.find('RootProjectItem')
            if rootitem is not None:self.project_items(children,rootitem)
            for uid,s in sorted(self.p.sequences.items(),key=lambda v:(v[1]['name'].startswith('Sequência aninhada'),v[1]['name'])):
                if uid not in self.seq_seen:self.sequence(children,uid)
        self.link_clips();return root

def evaluate(param,t,default):
    keys=sorted(param_keys(param))
    if not keys:return param_value(param,default)
    if t<=keys[0][0]:return keys[0][1]
    if t>=keys[-1][0]:return keys[-1][1]
    for (a,av,ai),(b,bv,bi) in zip(keys,keys[1:]):
        if a<=t<=b:
            if ai==4:return av
            if isinstance(av,tuple):return tuple(x+(y-x)*(t-a)/(b-a) for x,y in zip(av,bv))
            return av+(bv-av)*float((t-a)/(b-a))

def write_solid_png(path,width,height,color):
    def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data))
    raw=(b'\0'+bytes(color)*width)*height
    path.write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>2I5B',width,height,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b''))

def save_xml(root,path):
    path.parent.mkdir(parents=True,exist_ok=True);ET.indent(root,space='  ')
    path.write_bytes(b'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n'+ET.tostring(root,encoding='utf-8')+b'\n')

def convert(path,outdir,individual=True,metadata=None):
    p=Project(path,metadata);outdir=pathlib.Path(outdir);outdir.mkdir(parents=True,exist_ok=True)
    assets=outdir/'midias_geradas';save_xml(Writer(p,assets).document(),outdir/(p.path.stem+'.xml'))
    seqstats=[]
    for i,(uid,s) in enumerate(p.sequences.items(),1):
        name=f'{i:02d}_{safe_name(s["name"])}.xml'
        if individual:save_xml(Writer(p,assets).document(uid),outdir/'sequencias'/p.path.stem/name)
        seqstats.append({'id':uid,'name':s['name'],'frames':rounded(Fraction(s['duration'],s['tpf'])),
            'fps':float(Fraction(TICKS,s['tpf'])),'width':s['size'][0],'height':s['size'][1],
            'video_clips':sum(len(t['clips']) for t in s['video']),'audio_clips':sum(len(t['clips']) for t in s['audio']),
            'individual_xml':str(pathlib.Path('sequencias')/p.path.stem/name)})
    report={'source':p.path.name,'source_sha256':p.sha256,'sequences':seqstats,'warnings':p.warnings,
            'note':'Sem sequências ou mídias no projeto.' if not p.sequences else '',
            'native_clips':len(p.clips),'adjustment_layers':sum(c['adjustment'] for c in p.clips.values()),
            'media':[{'name':m['name'],'path':m['path']} for m in p.media.values()]}
    (outdir/(p.path.stem+'.relatorio.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('projects',nargs='+',type=pathlib.Path)
    ap.add_argument('--output',type=pathlib.Path,required=True);ap.add_argument('--no-individual',action='store_true')
    ap.add_argument('--media-metadata',type=pathlib.Path,help='JSON opcional com FPS e timecode lidos nas mídias pelo editor de destino')
    args=ap.parse_args();metadata=json.loads(args.media_metadata.read_text(encoding='utf-8')) if args.media_metadata else None
    for path in args.projects:
        report=convert(path,args.output,not args.no_individual,metadata)
        print(json.dumps({'project':path.name,'sequences':len(report['sequences']),'clips':report['native_clips'],'warnings':len(report['warnings'])}))

if __name__=='__main__':main()
