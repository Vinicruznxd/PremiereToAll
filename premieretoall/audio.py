"""Recursive nested audio expansion and automation in source-media time."""
from fractions import Fraction
import math
from .premiere import txt, param_value, param_keys, evaluate, rounded


def volume_params(project, clip, channel):
    params = []
    for comp in clip['effects']:
        match = txt(comp, 'MatchName') or txt(comp, 'FilterMatchName')
        values = project.params(comp)
        if match.startswith(('Internal Volume', 'Internal Channel Volume')):
            # Premiere's first audio parameter is Bypass, not Mute.
            if values and param_value(values[0], False):
                continue
            index = channel if 'Channel' in match else 1
            if len(values) > index:
                params.append(values[index])
    return params


def gain_at(project, clip, channel, media_time):
    result = clip['gain']
    for param in volume_params(project, clip, channel):
        result *= evaluate(param, media_time, 1)
    for parent, slope, offset in clip.get('audio_envelopes', []):
        result *= gain_at(project, parent, channel, slope * media_time + offset)
    return result


def automation_times(project, clip, channel):
    times = {t for p in volume_params(project, clip, channel) for t, _, _ in param_keys(p)}
    for parent, slope, offset in clip.get('audio_envelopes', []):
        times.update(rounded((t-offset)/slope) for t in automation_times(project, parent, channel))
    return times


def gain_curve(project, clip, channel, tpf, logarithmic=False):
    times = automation_times(project, clip, channel)
    if not times:
        return []
    lo, hi = sorted((clip['in'], clip['out']))
    # Compose at XML frame precision. This also captures multiplication of
    # independent gain ramps, which is not linear between their endpoints.
    times = {t for t in times if lo <= t <= hi} | {lo, hi}
    if logarithmic or clip.get('audio_envelopes') or len(volume_params(project, clip, channel)) > 1:
        times.update(range((lo//tpf)*tpf, hi+1, tpf))
    samples = [(t, gain_at(project, clip, channel, t)) for t in sorted(times)]
    # Discard collinear points to keep static stretches compact.
    out = []
    for point in samples:
        while len(out) > 1:
            a, b = out[-2:]
            fraction = (b[0]-a[0])/(point[0]-a[0]) if point[0] != a[0] else 0
            av,bv,pv=[20*math.log10(max(x[1],1e-15)) for x in (a,b,point)] if logarithmic else (a[1],b[1],point[1])
            if abs(bv-(av+(pv-av)*fraction)) > (.005 if logarithmic else 1e-7):
                break
            out.pop()
        out.append(point)
    return out


def expand_tracks(project, seq, ancestors=()):
    uid = seq['id']
    if uid in ancestors:
        raise ValueError('Referência circular no áudio aninhado: '+seq['name'])
    result = []
    for track in seq['audio']:
        result.append(dict(track, clips=[c for c in track['clips'] if not c['nested']]))
        lanes = {}
        for parent in track['clips']:
            if not parent['nested']:
                continue
            pspeed = Fraction(str(abs(parent['speed'])))
            if not pspeed:
                project.warn(parent, 'frozen_audio', 'Áudio de nest congelado não produz som; trecho mantido vazio no XML.')
                continue
            prev = parent['reverse'] or parent['speed'] < 0
            def timeline(t):
                return parent['start'] + rounded((parent['out']-t if prev else t-parent['in'])/pspeed)
            nested = project.sequences[parent['nested']]
            project.warn(parent, 'expanded_nested_audio', 'Áudio aninhado expandido recursivamente; volumes do nest e dos clipes combinados em automação por quadro.')
            for index, child_track in enumerate(expand_tracks(project, nested, (*ancestors, uid))):
                if not child_track['clips']:
                    continue
                lane = lanes.setdefault(index, dict(child_track, name=(track['name'] or 'Áudio')+' / nest '+str(index+1), clips=[], transitions=[]))
                expanded = []
                for child in child_track['clips']:
                    a, b = max(child['start'], parent['in']), min(child['end'], parent['out'])
                    if b <= a:
                        continue
                    speed = Fraction(str(abs(child['speed'])))
                    if not speed:
                        continue
                    reverse = child['reverse'] or child['speed'] < 0
                    signed = -speed if reverse else speed
                    origin = child['out'] if reverse else child['in']
                    ma = origin + rounded((a-child['start'])*signed)
                    mb = origin + rounded((b-child['start'])*signed)
                    start, end = sorted((timeline(a), timeline(b)))
                    cc = dict(child, id=parent['id']+'-'+child['id'], link_id=parent['id'], sequence=seq['name'],
                              start=start, end=end, speed=float(speed*pspeed), reverse=prev != reverse,
                              enabled=child['enabled'] and parent['enabled'] and child_track['enabled'])
                    cc['in'], cc['out'] = sorted((ma, mb))
                    cc['audio_envelopes'] = [*child.get('audio_envelopes', []),
                                            (parent, 1/signed, child['start']-origin/signed)]
                    cc['head'] = cc['tail'] = None
                    for side, boundary in [('head', child['start']), ('tail', child['end'])]:
                        tr = child[side]
                        if tr and boundary == (a if side == 'head' else b):
                            dest = ('tail' if side == 'head' else 'head') if prev else side
                            ts, te = sorted((timeline(tr['start']), timeline(tr['end'])))
                            cc[dest] = dict(tr, id=parent['id']+'-'+tr['id'], start=ts, end=te,
                                            alignment=rounded(tr['alignment']/pspeed))
                    expanded.append(cc)
                expanded.sort(key=lambda c:c['start'])
                if expanded:
                    if parent['head'] and expanded[0]['start'] == parent['start']:
                        expanded[0]['head'] = parent['head']
                    if parent['tail'] and expanded[-1]['end'] == parent['end']:
                        expanded[-1]['tail'] = parent['tail']
                    lane['clips'].extend(expanded)
        result.extend(lanes.values())
    return result
