"""XML export profiles for the destination editor's import semantics."""
from fractions import Fraction
from .premiere import Writer, add, effect, constant_parameter, rounded


class FilmCraftXMLWriter(Writer):
    audio_db_interpolation = True
    def audio_tracks(self, seq):
        # FilmCraft understands sequence references on audio tracks, too.
        # Keep the hierarchy and its own automation at every nesting level.
        return seq['audio']

    def clip(self, parent, c, seq, ch, track_index, clip_index):
        # FilmCraft 0.19 drops clips with both start and end = -1. It also
        # expands handle-based trims into overlaps. Supply actual edit bounds
        # and precise media ticks instead. Sequence still writes transitions.
        clean = dict(c, head=None, tail=None)
        x = super().clip(parent, clean, seq, ch, track_index, clip_index)
        x.find('start').text = str(rounded(Fraction(c['start'], seq['tpf'])))
        x.find('end').text = str(rounded(Fraction(c['end'], seq['tpf'])))
        for tag, v in [('pproTicksIn', c['in']), ('pproTicksOut', c['out'])]:
            existing = x.find(tag)
            if existing is None:
                add(x, tag, v)
            else:
                existing.text = str(v)
        # Explicit speed prevents the importer's duration-rounding heuristic
        # from applying a tiny accidental retime to sample-accurate audio.
        if not any(e.findtext('effectid')=='timeremap' for e in x.findall('filter/effect')):
            e = effect(x, 'Time Remap', 'timeremap', c['kind'])
            constant_parameter(e, 'speed', abs(c['speed'])*100)
            constant_parameter(e, 'reverse', c['reverse'])
        return x

    def filters(self, x, c, seq, ch=None, clock_tpf=None):
        return super().filters(x, c, seq, ch, seq['tpf'])


def writer_class(profile):
    if profile == 'filmcraft':
        return FilmCraftXMLWriter
    if profile == 'resolve':
        return Writer
    raise ValueError('Perfil XML desconhecido: '+profile)
