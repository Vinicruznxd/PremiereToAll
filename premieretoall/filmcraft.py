"""Direct FilmCraft project writer. Times stay in Premiere/FilmCraft ticks.

The schema was checked against FilmCraft 0.19.0, schema 17. This writer
does not pass through an XML importer and does not require FilmCraft.
"""
from __future__ import annotations
import copy
import hashlib
import json
import math
from fractions import Fraction
from pathlib import Path
from .premiere import Project, TICKS, txt, integer, truth, param_value, param_keys
from . import __version__


def rate(tpf):
    f = Fraction(TICKS, tpf)
    return {'num': f.numerator, 'den': f.denominator}


def db(gain):
    return max(-287.5, 20 * math.log10(max(float(gain), 1e-15)))


def value(v, kind='Float'):
    if kind == 'Vec2':
        return {kind: {'x': float(v[0]), 'y': float(v[1])}}
    if kind == 'Bool':
        return {kind: bool(v)}
    if kind == 'Choice':
        return {kind: int(v)}
    if kind in ('Color','Curve','Text'):
        return {kind: v}
    return {kind: float(v)}


def parameter(native, default, transform=lambda v: v, kind='Float'):
    result = {'value': value(transform(param_value(native, default)), kind)}
    keys = param_keys(native)
    if keys:
        raw = {int(r.split(',')[0]):r.split(',') for r in txt(native,'Keyframes').split(';') if r.strip()}
        modes = {0:'Linear',1:'EaseIn',2:'EaseOut',3:'Bezier',4:'Hold',5:'Bezier'}
        result['keyframes'] = [
            {'time': t, 'value': value(transform(v), kind), 'interp': modes.get(interp,'Linear'),
             'in_influence':max(0.,min(1.,float(raw[t][5]))) if len(raw[t])>5 else 1/3,
             'out_influence':max(0.,min(1.,float(raw[t][7]))) if len(raw[t])>7 else 1/3}
            for t, v, interp in sorted(keys)
        ]
        if transform is db:
            # Premiere stores linear amplitude, FilmCraft animates dB. Simply
            # converting endpoints changes every fade between them.
            def subdivide(a,av,b,bv,depth=0):
                mid=(a+b)//2;mv=(av+bv)/2
                if depth>=16 or mid in (a,b) or abs(db(mv)-(db(av)+db(bv))/2)<.01:
                    return []
                return [*subdivide(a,av,mid,mv,depth+1),
                        {'time':mid,'value':value(db(mv)),'interp':'Linear'},
                        *subdivide(mid,mv,b,bv,depth+1)]
            extra=[]
            for (a,av,ai),(b,bv,bi) in zip(sorted(keys),sorted(keys)[1:]):
                if ai==0:
                    extra.extend(subdivide(a,float(av),b,float(bv)))
            result['keyframes']=sorted([*result['keyframes'],*extra],key=lambda k:k['time'])
    return result


def effect(name, **params):
    return {'effect': name, 'enabled': True, 'params': params}


class FilmCraftWriter:
    def __init__(self, project: Project, assets_dir=None):
        self.p = project
        self.counter = 1
        self.items = {}
        self.media_ids = {}
        self.seq_ids = {}
        self.clip_ids = {}
        self.adjust_ids = {}
        self.links = {}
        self.warnings = []
        self.added_to_bin = set()
        self.luts = []
        self.lut_content = {}
        self.lut_paths = {}
        self.defaults = json.loads(Path(__file__).with_name('defaults.json').read_text(encoding='utf-8'))
        from .effects import EffectMapper
        self.effect_mapper = EffectMapper(self,assets_dir)

    def alloc(self):
        n = self.counter
        self.counter += 1
        return n

    def import_lut(self, path, clip, name=None):
        """FilmCraft renders library references, not literal file paths."""
        key = str(path).casefold()
        if key in self.lut_paths:
            return self.lut_paths[key]
        try:
            fmt = Path(path).suffix.lower().lstrip('.')
            if fmt not in ('cube', '3dl'):
                raise ValueError('Formato de LUT não suportado: '+fmt)
            with Path(path).open('rb') as stream:
                raw = stream.read((8 << 20) + 1)
            if len(raw) > 8 << 20:
                raise ValueError('A LUT excede o limite de 8 MiB do FilmCraft.')
            text = raw.decode('utf-8-sig')
            if not text.strip():
                raise ValueError('Arquivo de LUT vazio.')
            digest = hashlib.sha256((fmt+'\0'+text).encode('utf-8')).hexdigest()
            if digest not in self.lut_content:
                id_ = 'lut'+str(self.alloc())
                self.luts.append({'id': id_, 'name': name or Path(path).stem,
                                  'source_path': str(path), 'format': fmt, 'text': text})
                self.lut_content[digest] = 'lib:'+id_
            reference = self.lut_content[digest]
            self.lut_paths[key] = reference
            return reference
        except (OSError, UnicodeError, ValueError) as exc:
            self.warn(clip, 'lut_unavailable', f'LUT não incorporada: {path}. {exc}')
            return ''

    def warn(self, c, code, detail):
        row = {'sequence': c.get('sequence', ''), 'clip_id': c.get('id', ''),
               'clip': c.get('name', ''), 'code': code, 'detail': detail}
        if row not in self.warnings:
            self.warnings.append(row)

    def item(self, id_, name, kind, label='Iris', metadata=None):
        self.items[str(id_)] = {'id': id_, 'name': name, 'label': label, 'kind': kind,
                                'metadata': metadata or {}, 'created': id_}

    def media(self, uid):
        if uid in self.media_ids:
            return self.media_ids[uid]
        m = self.p.media[uid]
        if Path(m['path']).suffix.lower() in ('.aegraphic','.mogrt','.aep','.prgraphic'):
            self.warn({'name':m['name']},'graphics_engine','Gráfico/composição Adobe exige renderização ou reconstrução: '+m['path'])
        id_ = self.alloc()
        self.media_ids[uid] = id_
        video = None
        if m['video'] is not None:
            video = {'width': m['size'][0], 'height': m['size'][1], 'frame_rate': rate(m['tpf']),
                     'par': [1, 1], 'codec': '', 'pixel_format': '',
                     'color': {'matrix': 'Bt709', 'transfer': 'Bt709', 'primaries': 'Bt709', 'range': 'Limited'},
                     'has_alpha': m['still'] or Path(m['path']).suffix.lower() in ('.mov', '.png', '.tga', '.tif'),
                     'bitrate': None}
        audio = ([{'sample_rate': m['samplerate'], 'channels': max(1, m['channels']),
                   'codec': '', 'bits_per_sample': None}] if m['audio'] is not None else [])
        if m['synthetic']:
            generator = 'BlackVideo' if m['path'] == '1112293707' else 'TransparentVideo'
            ref = {'Generator': generator}
            if generator == 'TransparentVideo':
                self.warn({'name': m['name']}, 'synthetic_media',
                          'Gerador sem cor identificada: tela transparente provisória; recriar o conteúdo original.')
        else:
            ref = {'File': {'path': m['path']}}
        info = {'name': m['name'], 'kind': 'Synthetic' if m['synthetic'] else ('ImageSequence' if m.get('image_sequence') else ('Still' if m['still'] else ('Movie' if video else 'AudioOnly'))),
                'duration': max(m['duration'], 5 * TICKS if m['still'] else 0), 'video': video,
                'audio_streams': audio, 'container': '', 'start_timecode': round(m['timecode'] / m['tpf']), 'file_size': None}
        # Optional probe observations are concrete file metadata, not timeline
        # timebase substitutions. Timelines remain exact native ticks.
        if m.get('fc_info'):
            info = copy.deepcopy(m['fc_info'])
        self.item(id_, m['name'], {'Media': {
            'media': ref, 'info': info,
            'interpret': {'frame_rate': None, 'par': None, 'ignore_alpha': False, 'invert_alpha': False},
            'mark_in': None, 'mark_out': None, 'markers': [], 'offline': False, 'proxy': None,
        }}, 'Caribbean' if not video else 'Iris')
        return id_

    def effects(self, c, seq):
        output = []
        volume = channel = None
        fill = None
        channels = [max(0, ch - 1) for ch in c['channels']]
        sw, sh = self.p.media[c['media']]['size'] if c['media'] else self.p.sequences[c['nested']]['size']
        fw, fh = seq['size']
        for comp in c['effects']:
            match = txt(comp, 'MatchName') or txt(comp, 'FilterMatchName')
            params = self.p.params(comp)
            pm = {integer(v, 'ParameterID', i + 1): v for i, v in enumerate(params)}
            if any(any(k[2] not in (0,4) for k in param_keys(v)) for v in params):
                self.warn(c, 'keyframe_interpolation', 'Tempos, valores, tipos e influências temporais dos keyframes preservados; tangentes espaciais e velocidades Bézier exigem conferência.')
            if match == 'AE.ADBE Motion':
                output.append(effect('motion',
                    position=parameter(pm.get(1), (.5, .5), lambda v: (v[0]*fw, v[1]*fh), 'Vec2'),
                    scale=parameter(pm.get(2), 100), scale_width=parameter(pm.get(3), 100),
                    uniform_scale=parameter(pm.get(4), True, kind='Bool'), rotation=parameter(pm.get(5), 0),
                    anchor=parameter(pm.get(6), (.5, .5), lambda v: (v[0]*sw, v[1]*sh), 'Vec2'),
                    anti_flicker=parameter(pm.get(7), 0)))
                for pid in (8, 9, 10, 11):
                    if param_value(pm.get(pid), 0) or param_keys(pm.get(pid)):
                        output.append(effect('crop',**{('left','top','right','bottom')[pid-8]:parameter(pm[pid],0)}))
            elif match == 'AE.ADBE Opacity':
                from .effects import blend_index
                blend = blend_index(pm)
                output.append(effect('opacity', opacity=parameter(pm.get(1), 100), blend={'value': {'Choice': blend or 0}}))
                if blend is None:
                    self.warn(c, 'blend_mode', f"Modo de mesclagem original {param_value(pm.get(2), 0)}: revisar no FilmCraft; usado Normal.")
            elif match.startswith('Internal Volume'):
                volume = effect('volume', level=parameter(params[1] if len(params)>1 else None, 1, db),
                                bypass=parameter(params[0] if params else None, False, kind='Bool'))
            elif match.startswith('Internal Channel Volume'):
                channel = effect('channel_volume',
                    left=parameter(params[1] if len(params)>1 else None, 1, db),
                    right=parameter(params[2] if len(params)>2 else None, 1, db),
                    bypass=parameter(params[0] if params else None, False, kind='Bool'))
            elif match in ('Internal Audio Fill Right', 'Internal Audio Fill Left'):
                fill = 1 if 'Right' in match else 0
            else:
                mapped = self.effect_mapper.convert(comp,c,seq)
                if mapped is not None:
                    output.append(mapped)
                else:
                    name = txt(comp, 'Component/DisplayName') or txt(comp, 'AudioComponent/Component/DisplayName') or match
                    self.warn(c, 'unsupported_effect', f'Efeito sem conversão visual/sonora: {name} ({match}). Dados originais preservados em EFEITOS_PREMIERE.json.')
        if c['kind'] == 'audio':
            if volume:
                output.append(volume)
            if channel:
                output.append(channel)
            if fill is not None:
                channels = [fill, fill]
        if c['speed'] != 1 or c['reverse']:
            output.append(effect('time_remap', speed={'value': {'Float': abs(c['speed']) * 100}}))
        return output, channels

    def clip(self, c, seq):
        id_ = self.clip_ids[c['id']]
        if c['adjustment']:
            if c['id'] not in self.adjust_ids:
                aid = self.alloc()
                self.adjust_ids[c['id']] = aid
                self.item(aid, c['name'], {'AdjustmentLayer': {'width': seq['size'][0], 'height': seq['size'][1],
                          'rate': rate(seq['tpf']), 'duration': max(TICKS, c['out'])}}, 'Lavender')
            item = self.adjust_ids[c['id']]
        else:
            item = self.seq_ids[c['nested']] if c['nested'] else self.media(c['media'])
        effects, channels = self.effects(c, seq)
        return {'id': id_, 'item': item, 'name': c['name'], 'label': 'Caribbean' if c['kind']=='audio' else 'Iris',
                'start': c['start'], 'duration': c['end']-c['start'], 'source_in': c['in'],
                'speed': abs(c['speed']), 'reverse': c['reverse'] or c['speed']<0,
                'enabled': c['enabled'], 'link': self.links.get(c['id']), 'group': None,
                'effects': effects, 'markers': [], 'gain_db': db(c['gain']),
                'frame_hold': c['in'] if c['speed']==0 else None, 'scale_to_frame': False,
                'source_channels': channels if c['kind']=='audio' else []}

    def track(self, track, seq, kind):
        clips = [self.clip(c, seq) for c in track['clips']]
        transitions = []
        for tr in track['transitions']:
            outgoing = next((c for c in track['clips'] if c['tail'] and c['tail']['id']==tr['id']), None)
            incoming = next((c for c in track['clips'] if c['head'] and c['head']['id']==tr['id']), None)
            if not outgoing and not incoming:
                self.warn({'name': tr['name'], 'id': tr['id'], 'sequence': seq['name']}, 'transition_unlinked',
                          'Transição sem clipe associado; revisar na montagem.')
                continue
            eid = {'Constant Power': 'constant_power', 'Constant Gain': 'constant_gain',
                   'Exponential Fade': 'exponential_fade', 'AE.ADBE Cross Dissolve New': 'cross_dissolve'}.get(tr['match'])
            if eid is None:
                eid = 'constant_gain' if kind=='audio' else 'cross_dissolve'
                self.warn({'id': tr['id'], 'name': tr['name'], 'sequence': seq['name']}, 'transition_approximation',
                          'Transição substituída por fade/dissolução; intervalo e clipes mantidos.')
            align = 'StartAtCut' if tr['alignment']==0 else ('EndAtCut' if tr['alignment']==tr['end']-tr['start'] else 'CenterAtCut')
            transitions.append({'id': self.alloc(), 'effect': effect(eid), 'start': tr['start'],
                                'duration': tr['end']-tr['start'],
                                'from': self.clip_ids[outgoing['id']] if outgoing else None,
                                'to': self.clip_ids[incoming['id']] if incoming else None, 'align': align, 'reverse': False})
        return {'id': self.alloc(), 'kind': kind.title(), 'name': track['name'] or f"{'Vídeo' if kind=='video' else 'Áudio'} {track['index']+1}",
                'items': sorted(clips, key=lambda c:c['start']), 'transitions': sorted(transitions, key=lambda t:t['start']),
                'locked': track['locked'], 'sync_lock': True, 'enabled': track['enabled'], 'muted': not track['enabled'],
                'solo': False, 'channels': 'Stereo', 'volume_db': 0., 'pan': 0., 'effects': []}

    def bins(self, native, name=None):
        result = {'id': self.alloc(), 'name': name or txt(native, 'ProjectItem/Name') or 'Bin', 'children': []}
        for child in native.findall('ProjectItemContainer/Items/Item'):
            entry = self.p.ref(child)
            if entry.tag == 'BinProjectItem':
                result['children'].append({'Bin': self.bins(entry)})
            elif entry.tag == 'ClipProjectItem':
                master = self.p.ref(entry.find('MasterClip'))
                uid = master.get('ObjectUID')
                ids = []
                if uid in self.p.sequence_for_master:
                    ids.append(self.seq_ids[self.p.sequence_for_master[uid]])
                else:
                    for ref in master.findall('Clips/Clip'):
                        clip = self.p.ref(ref)
                        source = self.p.ref(clip.find('Clip/Source'))
                        if source is None:
                            continue
                        m = self.p.ref(source.find('MediaSource/Media'))
                        if m is not None:
                            ids.append(self.media(m.get('ObjectUID')))
                for id_ in dict.fromkeys(ids):
                    if id_ not in self.added_to_bin:
                        result['children'].append({'Item': id_})
                        self.added_to_bin.add(id_)
        return result

    def document(self):
        for uid in self.p.sequences:
            self.seq_ids[uid] = self.alloc()
        for cid in self.p.clips:
            self.clip_ids[cid] = self.alloc()
        for members in self.p.links:
            group = self.alloc()
            for cid in members:
                self.links[cid] = group
        for uid, seq in self.p.sequences.items():
            settings = copy.deepcopy(self.defaults['sequence_settings'])
            settings.update(width=seq['size'][0], height=seq['size'][1], frame_rate=rate(seq['tpf']),
                            sample_rate=seq['samplerate'], drop_frame=seq['drop'], preset='PremiereToAll')
            s = {'settings': settings,
                 'video_tracks': [self.track(t, seq, 'video') for t in seq['video']],
                 'audio_tracks': [self.track(t, seq, 'audio') for t in seq['audio']],
                 'markers': [], 'mark_in': None, 'mark_out': None, 'work_area': None,
                 'start_timecode': round(seq['timecode']/seq['tpf'])}
            self.item(self.seq_ids[uid], seq['name'], {'Sequence': s}, 'Forest',
                      {'Premiere sequence UID': uid})
        rootnative = self.p.root.find('RootProjectItem')
        root = self.bins(rootnative, self.p.path.stem) if rootnative is not None else {'id': self.alloc(), 'name': self.p.path.stem, 'children': []}
        # Keep unused sources too. Missing bin metadata must never discard a
        # sequence or media item from the project panel.
        for uid in self.p.media:
            self.media(uid)
        for id_ in self.items:
            if int(id_) not in self.added_to_bin:
                root['children'].append({'Item': int(id_)})
        active = next((self.seq_ids[u] for u,s in self.p.sequences.items() if s['duration']>0), next(iter(self.seq_ids.values()), None))
        return {'format': 'filmcraft.project', 'schema_version': 17, 'generator': 'PremiereToAll '+__version__,
                'project': {'name': self.p.path.stem, 'settings': self.defaults['project_settings'],
                            'root': root, 'items': self.items, 'next_id': self.counter, 'luts': self.luts,
                            'notes': 'Convertido do Premiere com PremiereToAll. Consulte o relatório de compatibilidade ao lado deste projeto.'},
                'view': {'open_sequences': [active] if active else [], 'active_sequence': active,
                         'sequences': {}, 'playheads': {}}}


def validate_document(doc):
    """Reject missing references, duplicate IDs, bad times and nesting cycles."""
    p = doc['project']
    items = p['items']
    lut_ids = {lut['id'] for lut in p.get('luts', [])}
    if len(lut_ids) != len(p.get('luts', [])):
        raise ValueError('ID de LUT FilmCraft duplicado')
    seen = set()
    def take(id_):
        if id_ in seen:
            raise ValueError(f'ID FilmCraft duplicado: {id_}')
        seen.add(id_)
    def visit_bin(b):
        take(b['id'])
        for child in b['children']:
            if 'Bin' in child:
                visit_bin(child['Bin'])
            elif str(child['Item']) not in items:
                raise ValueError('Bin aponta para item inexistente')
    visit_bin(p['root'])
    graph = {}
    for id_, item in items.items():
        take(item['id'])
        if str(item['id'])!=id_:
            raise ValueError('Chave do item divergente')
        if 'Sequence' not in item['kind']:
            continue
        edges = set()
        seq = item['kind']['Sequence']
        for tr in seq['video_tracks']+seq['audio_tracks']:
            take(tr['id'])
            clipids = set()
            for c in tr['items']:
                take(c['id']); clipids.add(c['id'])
                if str(c['item']) not in items or c['duration']<0 or not math.isfinite(c['speed']):
                    raise ValueError(f"Clipe inválido: {c['name']}")
                if 'Sequence' in items[str(c['item'])]['kind']:
                    edges.add(str(c['item']))
                for e in c.get('effects', []):
                    if e['effect'] != 'lumetri':
                        continue
                    for name in ('input_lut', 'look_lut'):
                        ref = e.get('params', {}).get(name, {}).get('value', {}).get('Text', '')
                        if ref and not (ref.startswith('builtin:') or ref.startswith('lib:') and ref[4:] in lut_ids):
                            raise ValueError('Referência de LUT não carregável pelo FilmCraft: '+ref)
            for t in tr['transitions']:
                take(t['id'])
                if any(t[k] is not None and t[k] not in clipids for k in ('from','to')):
                    raise ValueError('Transição aponta para clipe de outra trilha')
        graph[id_] = edges
    done = set()
    def dfs(id_, stack):
        if id_ in stack:
            raise ValueError('Sequência aninhada recursiva')
        if id_ not in done:
            for child in graph.get(id_, ()):
                dfs(child, stack | {id_})
            done.add(id_)
    for id_ in graph:
        dfs(id_, set())
    if seen and p['next_id'] <= max(seen):
        raise ValueError('Contador de IDs inválido')
    json.dumps(doc, allow_nan=False)
