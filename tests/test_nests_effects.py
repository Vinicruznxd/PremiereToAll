import math
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
import pytest
from premieretoall.premiere import Project, Writer, TICKS, txt, param_value
from premieretoall.audio import expand_tracks, gain_at, gain_curve
from premieretoall.xml_writer import FilmCraftXMLWriter
from premieretoall.filmcraft import FilmCraftWriter, validate_document, parameter, db
from premieretoall.effects import blend_index, rgba

T=TICKS

def volume(a,b,va,vb):
    e=ET.Element('Component');ET.SubElement(e,'MatchName').text='Internal Volume Stereo'
    bypass=ET.SubElement(e,'Param');ET.SubElement(bypass,'StartKeyframe').text='0,false,0'
    p=ET.SubElement(e,'Param');ET.SubElement(p,'StartKeyframe').text=f'0,{va},0'
    ET.SubElement(p,'Keyframes').text=f'{a},{va},0;{b},{vb},0;'
    return e

def clip(cid,start,end,pin,pout,nested=None,gain=1,effects=()):
    return dict(id=cid,name=cid,sequence='',start=start*T,end=end*T,**{'in':pin*T,'out':pout*T},
                nested=nested,media=None if nested else 'm',kind='audio',speed=1,reverse=False,
                gain=gain,effects=effects,head=None,tail=None,enabled=True,channels=[1,2])

def seq(uid,clips):
    return dict(id=uid,name=uid,audio=[dict(name='A1',index=0,clips=clips,transitions=[],enabled=True,locked=False)])

def test_composes_two_nested_volume_envelopes_with_source_trim_and_reverse():
    leaf=clip('leaf',2,10,30,38,gain=.8,effects=[volume(30*T,38*T,.5,1)])
    middle=clip('middle',3,7,4,8,'leafseq',.5,[volume(4*T,8*T,1,.5)])
    outer=clip('outer',10,12,4,6,'middleseq',.25)
    p=SimpleNamespace(sequences={'leafseq':seq('leafseq',[leaf]),'middleseq':seq('middleseq',[middle])},params=lambda e:e.findall('Param'),warn=lambda *a:None)
    expanded=[c for tr in expand_tracks(p,seq('root',[outer])) for c in tr['clips']]
    assert len(expanded)==1
    c=expanded[0]
    assert (c['start'],c['end'],c['in'],c['out'])==(10*T,12*T,33*T,35*T)
    assert gain_at(p,c,1,34*T)==pytest.approx(.8*.75*.5*.75*.25)
    curve=dict(gain_curve(p,c,1,T//25))
    assert curve[34*T]==pytest.approx(.05625)
    outer['reverse']=True;outer['speed']=2;outer['end']=11*T
    c=next(c for tr in expand_tracks(p,seq('root',[outer])) for c in tr['clips'])
    assert (c['start'],c['end'],c['in'],c['out'],c['speed'],c['reverse'])==(10*T,11*T,33*T,35*T,2,True)
    assert gain_at(p,c,1,34*T)==pytest.approx(.05625)


def test_audio_bypass_does_not_mute_clip():
    e=volume(0,T,.1,.1);e.find('Param/StartKeyframe').text='0,true,0'
    p=SimpleNamespace(params=lambda e:e.findall('Param'))
    c=clip('test',0,1,0,1,effects=[e])
    assert gain_at(p,c,1,0)==1


def test_native_db_conversion_preserves_linear_amplitude_ramp():
    p=volume(0,T,.5,1).findall('Param')[1]
    keys=parameter(p,1,db)['keyframes']
    assert len(keys)>2
    for t in (T//4,T//2,3*T//4):
        for a,b in zip(keys,keys[1:]):
            if a['time']<=t<=b['time']:
                fraction=(t-a['time'])/(b['time']-a['time'])
                got=10**((a['value']['Float']+(b['value']['Float']-a['value']['Float'])*fraction)/20)
                assert got==pytest.approx(.5+.5*t/T,rel=.002)
                break


@pytest.fixture(scope='module')
def star():
    path=Path(__file__).resolve().parent/'fixtures/nested_effects_regression.prproj'
    if not path.exists():pytest.skip('Projeto local de regressão não distribuído')
    return Project(path)


def test_nested_project_exports_both_xml_profiles_and_native(star,tmp_path):
    assert len(star.sequences)==69
    for writer in [Writer,FilmCraftXMLWriter]:
        root=writer(star,tmp_path).document()
        assert len([s for s in root.iter('sequence') if s.find('media') is not None])==69
    fw=FilmCraftWriter(star,tmp_path/'resources');doc=fw.document();validate_document(doc)
    assert len([c for i in doc['project']['items'].values() if 'Sequence' in i['kind'] for k in ('video_tracks','audio_tracks') for tr in i['kind']['Sequence'][k] for c in tr['items']])==5712
    assert fw.effect_mapper.converted['AE.ADBE Lumetri']==271
    assert fw.effect_mapper.converted['AE.ADBE Ultra Key']==46
    assert list((tmp_path/'resources').glob('*.cube'))
    for uid,seq0 in star.sequences.items():
        got=doc['project']['items'][str(fw.seq_ids[uid])]['kind']['Sequence']
        for kind in ('video','audio'):
            native=[c for tr in seq0[kind] for c in tr['clips'] if c['nested']]
            actual=[c for tr in got[kind+'_tracks'] for c in tr['items'] if c['item'] in fw.seq_ids.values()]
            assert len(native)==len(actual)


def test_color_integer_keeps_all_bits_and_opacity_enum(star):
    for c in star.clips.values():
        for e in c['effects']:
            if txt(e,'MatchName')=='AE.ADBE Opacity':
                pm={int(txt(v,'ParameterID')):v for v in star.params(e)}
                assert blend_index(pm)=={18:0,11:7,1:4,22:8}[param_value(pm[2])]
            if txt(e,'MatchName')=='AE.ADBE Tint':
                p=star.params(e)[0];raw=int(txt(p,'StartKeyframe').split(',')[1])
                assert param_value(p)==raw
                assert rgba(raw)[2]==pytest.approx((raw&65535)/65280)


def test_numbered_pngs_remain_animation_at_premiere_frame_rate(star,tmp_path,monkeypatch):
    from premieretoall import service
    import json,threading
    original=next(m for m in star.media.values() if m.get('image_sequence') and m['tpf']==T//24)
    frame=tmp_path/'frame0001.png';frame.write_bytes(b'fixture')
    m=dict(original,path=str(frame))
    p=SimpleNamespace(media={'m':m},warn=lambda *args:None)
    monkeypatch.setattr(service,'find_ffmpeg',lambda *args:'ffprobe')
    monkeypatch.setattr(service.subprocess,'run',lambda *args,**kwargs:SimpleNamespace(returncode=0,stdout=json.dumps({'streams':[{'codec_type':'video','width':3840,'height':2160,'r_frame_rate':'25/1'}],'format':{}}).encode()))
    service.probe_project(p,threading.Event(),lambda *args:None)
    assert m['tpf']==T//24
    fw=FilmCraftWriter(star,tmp_path/'luts');doc=fw.document()
    seq_media=[i['kind']['Media']['info'] for i in doc['project']['items'].values() if 'Media' in i['kind'] and i['kind']['Media']['info']['kind']=='ImageSequence']
    assert len(seq_media)==10
    assert sum(x['video']['frame_rate']=={'num':24,'den':1} for x in seq_media)==9
    assert sum(x['video']['frame_rate']=={'num':25,'den':1} for x in seq_media)==1
