"""Explicit Premiere-to-FilmCraft effect mappings; no plugin substitution by guess."""
import base64
import copy
import hashlib
import json
import math
import struct
import xml.etree.ElementTree as ET
from pathlib import Path
from .premiere import txt, integer, param_value, param_keys

# Parameter identities checked against native projects and FilmCraft's preset
# reader. The catalog is captured from the installed FilmCraft 0.19 CLI.
MAPPINGS = {
    'AE.ADBE Tint': ('tint', {1:'black', 2:'white', 3:'amount'}),
    'AE.ADBE Ultra Key': ('ultra_key', {1:'output',2:'setting',3:'key_color',5:'transparency',6:'highlight',7:'shadow',8:'tolerance',9:'pedestal',12:'choke',13:'soften',14:'contrast',15:'mid_point',18:'desaturate',19:'range',20:'spill',21:'spill_luma',24:'cc_saturation',25:'cc_hue',26:'cc_luminance'}),
    'AE.ADBE Black & White': ('black_white', {}),
    'AE.ADBE Horizontal Flip': ('horizontal_flip', {}),
    'AE.ADBE Vertical Flip': ('vertical_flip', {}),
    'AE.ADBE Mirror': ('mirror', {1:'center',2:'angle'}),
    'AE.ADBE AECrop': ('crop', {1:'left',2:'top',3:'right',4:'bottom',5:'zoom'}),
    'AE.ADBE Geometry2': ('transform', {1:'anchor',2:'position',11:'uniform_scale',3:'scale_height',4:'scale_width',5:'skew',6:'skew_axis',7:'rotation',8:'opacity',9:'shutter_override',10:'shutter_angle'}),
    'AE.ADBE Fast Blur': ('gaussian_blur', {1:'blurriness',2:'dimensions',3:'repeat_edge'}),
    'AE.ADBE Gaussian Blur 2': ('gaussian_blur', {1:'blurriness',2:'dimensions',3:'repeat_edge'}),
    'AE.ADBE Motion Blur': ('directional_blur', {1:'direction',2:'length'}),
    'AE.ADBE Median': ('median', {1:'radius'}),
    'AE.ADBE Wave Warp': ('wave_warp', {2:'height',3:'width',4:'direction',5:'speed'}),
    'PR.ADBE Lens Distortion': ('lens_distortion', {1:'curvature',2:'v_decentering',3:'h_decentering'}),
    'PR.ADBE Levels': ('levels', {1:'in_black',2:'in_white',3:'out_black',4:'out_white',5:'gamma'}),
    'PR.ADBE Color Replace': ('color_replace', {1:'similarity',2:'solid',3:'target',4:'replace'}),
}
LUMETRI = {3:'basic_on',7:'temperature',8:'tint',11:'exposure',12:'contrast',13:'highlights',14:'shadows',15:'whites',16:'blacks',20:'saturation',23:'creative_on',26:'look_intensity',28:'faded_film',29:'sharpen',30:'vibrance',31:'creative_sat',37:'curves_on',46:'wheels_on',50:'vignette_on',51:'vignette_amount',52:'vignette_midpoint',53:'vignette_roundness',54:'vignette_feather',64:'hdr_specular'}


def rgba(v):
    n = int(v)
    a = ((n >> 48) & 65535)/65280
    return [min(1., ((n >> shift) & 65535)/65280) for shift in (32,16,0)] + [min(1., a) if a else 1.]


def blend_index(pm):
    # Premiere's UI enum includes separators. The API twin (id 2) has a
    # different ordering. Prefer the stored UI value (id 3).
    ui = [0,1,3,4,5,6,7,9,10,11,12,13,15,16,17,18,19,20,21,23,24,25,26,28,29,30,31]
    if 3 in pm:
        n = int(param_value(pm[3],0))
        return ui.index(n) if n in ui else None
    api = [25,4,9,2,6,19,1,20,14,18,23,7,11,5,10,16,26,3,0,12,17,24,8,13,15,21,22]
    n = int(param_value(pm.get(2),18))
    return api[n] if 0 <= n < len(api) else None


class EffectMapper:
    def __init__(self, writer, assets_dir=None):
        self.w = writer
        self.p = writer.p
        self.assets = Path(assets_dir) if assets_dir else None
        self.catalog = json.loads(Path(__file__).with_name('effect_catalog.json').read_text(encoding='utf-8'))
        self.binary = {e.get('BinaryHash'):e.text for e in self.p.root.iter() if e.get('Encoding')=='base64' and e.text and e.text.strip()}
        self.luts = {}
        self.converted = {}

    def blob(self, param):
        node = param.find('StartKeyframeValue') if param is not None else None
        if node is None:
            return b''
        raw = (node.text or '').strip() or self.binary.get(node.get('BinaryHash'), '')
        try:
            return base64.b64decode(raw, validate=False)
        except ValueError:
            return b''

    def mapped_param(self, eid, name, native, seq):
        from .filmcraft import parameter
        spec = self.catalog[eid][name]
        kind, default = next(iter(spec['default'].items()))
        transform = lambda v:v
        if kind == 'Color':
            transform = rgba
            default = 18374966859414961920
        elif kind == 'Vec2':
            transform = lambda v:(v[0]*seq['size'][0],v[1]*seq['size'][1])
            default = (.5,.5)
        return parameter(native, default, transform, kind)

    def embedded_luts(self, pm, clip):
        blob = self.blob(pm.get(98))
        if not blob or not self.assets:
            return []
        fingerprint = hashlib.sha256(blob).hexdigest()[:16]
        if fingerprint in self.luts:
            return self.luts[fingerprint]
        paths = []
        offset = 0
        while offset+60 <= len(blob):
            size = struct.unpack_from('<I',blob,offset+16)[0]
            start = offset+20
            if blob[start:start+4] != b'ITX3' or start+size > len(blob):
                break
            n, channels = struct.unpack_from('<II',blob,start+4)
            if not 2 <= n <= 65 or channels != 3 or size != 40+12*n**3:
                break
            values = list(struct.iter_unpack('<fff',blob[start+40:start+size]))
            if not all(math.isfinite(v) for row in values for v in row):
                break
            domain_min = struct.unpack_from('<fff', blob, start+16)
            domain_max = struct.unpack_from('<fff', blob, start+28)
            if not all(math.isfinite(a) and math.isfinite(b) and a < b for a,b in zip(domain_min,domain_max)):
                break
            self.assets.mkdir(parents=True,exist_ok=True)
            path = self.assets/f'LUT_{fingerprint}_{len(paths)+1}.cube'
            if not path.exists():
                path.write_text(f'TITLE "LUT incorporada no Premiere"\nLUT_3D_SIZE {n}\n'+
                                'DOMAIN_MIN '+' '.join(f'{v:.9g}' for v in domain_min)+'\n'+
                                'DOMAIN_MAX '+' '.join(f'{v:.9g}' for v in domain_max)+'\n'+
                                '\n'.join(' '.join(f'{v:.9g}' for v in row) for row in values)+'\n',encoding='utf-8')
            paths.append(self.p.output_path_resolver(str(path.resolve())))
            offset = start+size
        if offset != len(blob):
            self.w.warn(clip,'embedded_lut_partial','Uma LUT incorporada usa estrutura não reconhecida; conferir o relatório e o caminho original.')
        self.luts[fingerprint] = paths
        return paths

    def lumetri(self, comp, pm, clip, seq):
        from .filmcraft import effect
        # Missing colour parameters render as white in FilmCraft, not as the
        # neutral 50% split-tone values shown by its UI. Supply every default.
        params = {name:{'value':copy.deepcopy(spec['default'])} for name,spec in self.catalog['lumetri'].items()}
        params.update({name:self.mapped_param('lumetri',name,pm[pid],seq) for pid,name in LUMETRI.items() if pid in pm})
        for pid,name in ((63,'hdr_white'),(65,'curves_hdr_range')):
            if pid in pm:params[name]=self.mapped_param('lumetri',name,pm[pid],seq)
        blob = self.blob(pm.get(1))
        try:
            root = ET.fromstring(blob) if blob else ET.Element('empty')
        except ET.ParseError:
            root = ET.Element('empty')
            self.w.warn(clip,'lumetri_blob','Dados adicionais do Lumetri não puderam ser lidos; controles numéricos preservados.')
        shaders = root.findall('keyframe/shader')
        embedded = self.embedded_luts(pm,clip)
        lut_index = 0
        # Embedded tables include populated LUT slots only: an empty Input LUT
        # must not consume the Creative Look's table.
        populated = [txt(s.find('parameters'),'LUT').strip('"') for s in shaders
                     if txt(s,'name').strip('"') == 'LUT' and txt(s.find('parameters'),'LUT').strip('"')]
        embedded_index = 0
        for shader in shaders:
            name = txt(shader,'name').strip('"')
            fields = shader.find('parameters')
            if fields is None:
                continue
            if name == 'LUT':
                path = txt(fields,'LUT').strip('"')
                visible = txt(shader,'visible','"1"').strip('"') == '1'
                target = 'input_lut' if lut_index == 0 else 'look_lut'
                if visible and path:
                    original_name = Path(path.replace('\\','/')).stem
                    path = self.p.media_path_resolver(path)
                    try:
                        exists = Path(path).is_file()
                    except OSError:
                        exists = False
                    if len(embedded) == len(populated) and embedded_index < len(embedded):
                        selected = embedded[embedded_index]
                    elif exists:
                        selected = path
                    else:
                        selected = path
                        self.w.warn(clip,'lut_missing','LUT não encontrada: '+path)
                    params[target] = {'value':{'Text':self.w.import_lut(selected, clip, original_name)}}
                if path:
                    embedded_index += 1
                lut_index += 1
            elif name == 'ToneCurves':
                for source,target in [('ToneCurvesLuma','curve_luma'),('ToneCurvesRed','curve_red'),('ToneCurvesGreen','curve_green'),('ToneCurvesBlue','curve_blue')]:
                    raw = txt(fields,source).strip('"').partition('value:')[2]
                    try:
                        points = [[float(x),float(y)] for item in raw.split(';') if item for y,x in [item.split('@')]]
                        if points:
                            params[target] = {'value':{'Curve':sorted(points)}}
                    except ValueError:
                        self.w.warn(clip,'lumetri_curves','Curva RGB do Lumetri exige revisão.')
            elif name in ('Wheels','ColorCurves','LookAdjustments','SecondaryLayer'):
                # These controls do not have identical units or curve transfer
                # functions. Report non-neutral values rather than inventing them.
                fields_to_check = list(fields)
                changed = False
                for field in fields_to_check:
                    raw = (field.text or '').strip('"')
                    if name=='Wheels' and '.' in field.tag and raw not in ('D0','N0'):
                        changed = True
                    if name=='ColorCurves' and 'value:' in raw and raw.split('value:')[1] not in ('1@0;1@1','0@0;0@1'):
                        changed = True
                    if name=='LookAdjustments' and 'Tint.' in field.tag and raw!='D1':
                        changed = True
                    if name=='SecondaryLayer' and field.tag in ('Temp','Tint','Contrast','Saturation','Midtones.X','Midtones.Y','Midtones.Z') and raw not in ('D0','D100'):
                        changed = True
                if changed:
                    self.w.warn(clip,'lumetri_partial','Lumetri: controles avançados em '+name+' exigem revisão; correção básica, criação, curvas RGB, LUT e vinheta foram mapeadas.')
        self.w.warn(clip,'color_engine','Lumetri convertido para Color Grading; comparar a cor, pois os motores Adobe e FilmCraft usam cálculos próprios.')
        return effect('lumetri',**params)

    def convert(self, comp, clip, seq):
        from .filmcraft import effect, parameter
        match = txt(comp,'MatchName') or txt(comp,'FilterMatchName')
        native = self.p.params(comp)
        pm = {(integer(v,'ParameterID',i+1) if integer(v,'ParameterID',i+1)>0 else i+1):v for i,v in enumerate(native)}
        if match == 'AE.ADBE Lumetri':
            result = self.lumetri(comp,pm,clip,seq)
        elif match in MAPPINGS:
            eid,mapping = MAPPINGS[match]
            result = effect(eid,**{name:self.mapped_param(eid,name,pm[pid],seq) for pid,name in mapping.items() if pid in pm})
            if match=='PR.ADBE Color Replace' and 1 in pm:
                result['params']['similarity']=parameter(pm[1],0,lambda v:v*100/127)
            if match in ('AE.ADBE Wave Warp','PR.ADBE Lens Distortion','PR.ADBE Levels','AE.ADBE Geometry2'):
                self.w.warn(clip,'effect_partial',match+': controles equivalentes convertidos; conferir opções adicionais e diferenças de renderização.')
        elif match == 'c6762838-e451-4d22-9c90-b0eaa734605a' and len(native)>1:
            result = effect('denoise',amount=parameter(native[1],0,lambda v:v*100))
            self.w.warn(clip,'audio_effect_equivalent','DeNoise convertido para o redutor de ruído do FilmCraft; o resultado sonoro pode diferir.')
        else:
            return None
        if any((x.text or '').lower() in ('true','1') for x in comp.iter('Bypass')):
            result['enabled'] = False
        if clip['kind']=='audio' and native and param_value(native[0],False):
            result['enabled'] = False
        self.converted[match] = self.converted.get(match,0)+1
        return result
