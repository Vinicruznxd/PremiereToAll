"""Conversion jobs, local media discovery, reports and safe output handling."""
from __future__ import annotations
import csv
import hashlib
import html
import json
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from .premiere import Project, TICKS, safe_name, save_xml, ticks_per_frame, rate_values, timecode_frames
from .filmcraft import FilmCraftWriter, validate_document
from .xml_writer import writer_class
from .network_paths import NetworkPathMapper
from . import __version__


class Cancelled(Exception):
    pass


_PROBE_CACHE = {}


@dataclass
class Options:
    output: Path | None = None
    xml: bool = True
    fcproj: bool = True
    xml_profile: str = 'filmcraft'
    individual_xml: bool = True
    check_media: bool = True
    probe_media: bool = True
    remaps: list[tuple[str, str]] = field(default_factory=list)


def find_ffmpeg(tool='ffmpeg'):
    found = shutil.which(tool)
    if found:
        return found
    local = Path(os.environ.get('LOCALAPPDATA', ''))
    for root in (local/'Microsoft/WinGet/Packages', Path('C:/ffmpeg'), Path('D:/ffmpeg')):
        pattern = f'Gyan.FFmpeg*/**/{tool}.exe' if root.name=='Packages' else f'**/{tool}.exe'
        found = next(root.glob(pattern), None) if root.is_dir() else None
        if found:
            return str(found)
    return None


def unique_directory(parent, name):
    parent = Path(parent).expanduser().resolve()
    parent.mkdir(parents=True, exist_ok=True)
    base = safe_name(name)
    for i in range(10000):
        path = parent/(base if i==0 else f'{base}_{i+1:02d}')
        try:
            path.mkdir()
            return path
        except FileExistsError:
            continue
    raise OSError('Não foi possível criar uma pasta de saída exclusiva.')


def mapped_path(path, source_folder, remaps):
    normalized = path.replace('\\', '/')
    for source, target in remaps:
        source = source.replace('\\', '/').rstrip('/')
        if normalized.casefold()==source.casefold() or normalized.casefold().startswith(source.casefold()+'/'):
            normalized = target.replace('\\', '/').rstrip('/')+normalized[len(source):]
            break
    if normalized and not Path(normalized).is_absolute() and not (len(normalized)>1 and normalized[1]==':'):
        normalized = str(source_folder/normalized)
    return normalized


def verify_project(p):
    if any(s['tpf']<=0 or min(s['size'])<=0 for s in p.sequences.values()):
        raise ValueError('Taxa de quadros ou resolução inválida no projeto.')
    visiting = set()
    done = set()
    def visit(uid):
        if uid in visiting:
            raise ValueError('O projeto contém uma sequência aninhada recursiva.')
        if uid in done:
            return
        visiting.add(uid)
        for kind in ('video', 'audio'):
            for track in p.sequences[uid][kind]:
                for c in track['clips']:
                    if c['end']<c['start']:
                        raise ValueError('Clipe com duração negativa: '+c['name'])
                    if c['nested']:
                        if c['nested'] not in p.sequences:
                            raise ValueError('Sequência aninhada não encontrada: '+c['name'])
                        visit(c['nested'])
        visiting.remove(uid)
        done.add(uid)
    for uid in p.sequences:
        visit(uid)


def probe_project(p, stop, log):
    ffprobe = find_ffmpeg('ffprobe')
    if not ffprobe:
        log('FFprobe não encontrado; usando os metadados do Premiere.')
        p.warn({}, 'probe_unavailable', 'FFprobe não encontrado. Metadados lidos do projeto original.')
        return
    cache = _PROBE_CACHE
    physical = [m for m in p.media.values() if m['path'] and not m['synthetic'] and not m['still']]
    for index, m in enumerate(physical, 1):
        if stop.is_set():
            raise Cancelled()
        log(f'Conferindo metadados {index}/{len(physical)}: {m["name"]}')
        try:
            stat = Path(m['path']).stat()
            key = (m['path'].casefold(), stat.st_size, stat.st_mtime_ns)
            if key not in cache:
                process = subprocess.run([ffprobe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', m['path']],
                    capture_output=True, timeout=20, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                if process.returncode:
                    raise ValueError(process.stderr.decode('utf-8', 'replace')[:220])
                cache[key] = json.loads(process.stdout)
            data = cache[key]
            video = next((s for s in data['streams'] if s['codec_type']=='video'), None)
            tags = data.get('format', {}).get('tags', {})
            if video:
                from fractions import Fraction
                fr = video.get('r_frame_rate') or video.get('avg_frame_rate')
                fps = float(Fraction(fr))
                if fps>0 and not m.get('image_sequence'):
                    m['tpf'] = ticks_per_frame(fps)
                tags = {**tags, **video.get('tags', {})}
                width, height = int(video['width']), int(video['height'])
                rotation = float(tags.get('rotate', 0))
                for side in video.get('side_data_list', []):
                    if 'rotation' in side:
                        rotation = float(side['rotation'])
                if round(rotation)%180 in (90, -90):
                    width, height = height, width
                if m['size'] != (width, height):
                    p.warn({'name':m['name']}, 'media_orientation',
                           f'Resolução de exibição ajustada para {width} × {height}; rotação registrada na mídia: {rotation:g}°.')
                m['size'] = (width, height)
            tc = tags.get('timecode')
            if tc:
                m['timecode'] = timecode_frames(tc, rate_values(m['tpf'])[0])*m['tpf']
            elif not video or m['video'] is not None:
                # No declared timecode: no phantom XMP offset in interchange.
                m['timecode'] = 0
        except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
            p.warn({'name': m['name']}, 'probe_failed', 'Não foi possível conferir os metadados: '+str(exc)[:220])


def prepare_holds(p, assets, stop, log):
    """Generate stills only for Resolve, whose 0.1% remap differs from Premiere."""
    ffmpeg = find_ffmpeg()
    for c in p.clips.values():
        if c['kind']!='video' or not c['media'] or not 0<abs(c['speed'])<.01:
            continue
        if stop.is_set():
            raise Cancelled()
        m = p.media[c['media']]
        import math
        frame = math.floor(c['in']/m['tpf'])
        target = assets/(p.path.stem+'_'+c['id']+'_quadro_'+str(frame)+'.png')
        if not ffmpeg:
            p.warn(c, 'frame_hold_unavailable', 'FFmpeg não encontrado: conferir velocidade abaixo de 1% no Resolve.')
            continue
        assets.mkdir(parents=True, exist_ok=True)
        log('Preparando quadro estático: '+c['name'])
        try:
            result = subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-i', m['path'],
                '-vf', f'select=eq(n\\,{frame})', '-frames:v', '1', '-update', '1', str(target)],
                capture_output=True, timeout=90, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode or not target.exists():
                raise ValueError(result.stderr.decode('utf-8', 'replace')[:180])
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            p.warn(c, 'frame_hold_failed', 'Falha ao extrair quadro estático: '+str(exc))


def write_report(report, folder):
    (folder/'RELATORIO.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    with (folder/'AVISOS.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['format','sequence','clip_id','clip','code','detail'], delimiter=';')
        writer.writeheader()
        writer.writerows(report['warnings'])
    esc = html.escape
    rows = ''.join('<tr>'+''.join('<td>'+esc(str(w.get(k,'')))+'</td>' for k in ('format','sequence','clip','detail'))+'</tr>' for w in report['warnings'])
    seqs = ''.join(f"<tr><td>{esc(s['name'])}</td><td>{s['fps']:.3f}</td><td>{s['width']} × {s['height']}</td><td>{s['seconds']:.2f} s</td></tr>" for s in report['sequences'])
    network = report.get('network_paths', {})
    network_rows = ''.join(f"<tr><td>{esc(c['original'])}</td><td>{esc(c['mapped'])}</td></tr>" for c in network.get('changes', []))
    network_note = 'Caminhos sem unidade mapeada equivalente foram mantidos.'
    if network.get('unresolved_hosts'):
        network_note += ' Servidores sem resolução confirmada: ' + ', '.join(network['unresolved_hosts']) + '.'
    if network.get('discovery_error'):
        network_note += ' Não foi possível consultar os mapeamentos: ' + network['discovery_error']
    text = f'''<!doctype html><html lang="pt-BR"><meta charset="utf-8"><title>PremiereToAll — relatório</title>
<style>body{{font:16px system-ui;background:#10151d;color:#e5eaf2;max-width:1100px;margin:40px auto;padding:0 24px}}h1{{color:#8adbc4}}table{{border-collapse:collapse;width:100%;margin:20px 0}}td,th{{padding:10px;border-bottom:1px solid #33404f;text-align:left}}small{{color:#9aa8b9}}.note{{background:#202b38;padding:18px;border-radius:12px}}a{{color:#8adbc4}}</style>
<h1>{esc(report['project'])}</h1><p>Conversão local concluída · {len(report['sequences'])} sequências · {report['native_clips']} clipes no original.</p>
<p class="note">Os arquivos de mídia continuam nas pastas de origem. Nests e camadas de ajuste são preservados no .fcproj. Efeitos compatíveis, controles de cor e LUTs são mapeados; plugins, gráficos Adobe e controles sem equivalente precisam de revisão. XML e .fcproj são montagens editáveis; não incluem um vídeo renderizado.</p>
<p>Perfil XML: {esc(report['xml_profile'])}. Mídias não encontradas: {len(report['missing_media'])}. Original preservado.</p>
<p><b>FilmCraft:</b> abra o arquivo .fcproj por File → Open Project. Para XML, use File → Import. O perfil FilmCraft usa limites de corte explícitos para preservar clipes com fades nas duas pontas.</p>
<table><tr><th>Sequência</th><th>FPS</th><th>Resolução</th><th>Duração</th></tr>{seqs}</table>
<h2>Compatibilidade e ajustes</h2><table><tr><th>Formato</th><th>Sequência</th><th>Clipe</th><th>Detalhe</th></tr>{rows or '<tr><td colspan="4">Nenhum aviso registrado.</td></tr>'}</table>
<h2>Mídias não encontradas</h2><ul>{''.join('<li>'+esc(p)+'</li>' for p in report['missing_media']) or '<li>Nenhuma, ou verificação desativada.</li>'}</ul>
<h2>Caminhos de rede</h2><p>{network.get('changed_paths_count', 0)} caminhos ajustados para unidades mapeadas. {esc(network_note)}</p>
<table><tr><th>Caminho original</th><th>Caminho salvo</th></tr>{network_rows or '<tr><td colspan="2">Nenhuma substituição necessária.</td></tr>'}</table>
<small>SHA-256 do original: {report['source_sha256']}<br>PremiereToAll {esc(report['version'])} · {esc(report['created'])}</small></html>'''
    (folder/'RELATORIO.html').write_text(text, encoding='utf-8')


def convert_project(path, options: Options, log=lambda s: None, stop=None):
    stop = stop or threading.Event()
    if not options.xml and not options.fcproj:
        raise ValueError('Selecione ao menos um formato de saída.')
    source = Path(path).resolve()
    if source.suffix.lower()!='.prproj':
        raise ValueError('Selecione um arquivo .prproj do Adobe Premiere.')
    log('Lendo '+source.name)
    p = Project(source)
    verify_project(p)
    if stop.is_set():
        raise Cancelled()
    log('Identificando unidades de rede mapeadas…')
    network = NetworkPathMapper.discover()
    p.media_path_resolver = lambda path: network.map_path(mapped_path(path, source.parent, options.remaps))
    p.output_path_resolver = network.map_path
    for m in p.media.values():
        if stop.is_set():
            raise Cancelled()
        if m['path'] and not m['synthetic']:
            m['path'] = p.media_path_resolver(m['path'])
    log(f"Rede: {network.report()['changed_paths_count']} caminhos ajustados para unidades mapeadas.")
    if options.probe_media:
        probe_project(p, stop, log)
    folder = unique_directory(options.output if options.output is not None else source.parent, source.stem)
    report = {'version': __version__, 'created': datetime.now().isoformat(timespec='seconds'),
              'project': source.stem, 'source': str(source), 'source_sha256': p.sha256,
              'xml_profile': options.xml_profile if options.xml else 'não solicitado',
              'native_clips': len(p.clips), 'warnings': [], 'outputs': [], 'sequences': [],
              'media_check_enabled': options.check_media, 'missing_media': [], 'status': 'running',
              'network_paths': network.report()}
    try:
        if options.check_media:
            paths = sorted({m['path'] for m in p.media.values() if m['path'] and not m['synthetic']})
            for i, media_path in enumerate(paths, 1):
                if stop.is_set():
                    raise Cancelled()
                if i%20==1:
                    log(f'Localizando mídias {i}/{len(paths)}')
                if not Path(media_path).is_file():
                    report['missing_media'].append(media_path)
        if options.fcproj:
            log('Gerando projeto nativo do FilmCraft…')
            fw = FilmCraftWriter(p,folder/'recursos')
            doc = fw.document()
            validate_document(doc)
            fcfile = folder/(source.stem+'.fcproj')
            fcfile.write_text(json.dumps(doc, ensure_ascii=False, separators=(',', ':'), allow_nan=False), encoding='utf-8')
            report['outputs'].append(fcfile.name)
            report['warnings'].extend({'format':'fcproj',**w} for w in fw.warnings)
            report['effects_converted'] = fw.effect_mapper.converted
            # Keep a readable, deduplicated copy of unmapped effect data.
            # This is a recovery record, not an effect FilmCraft can execute.
            from .premiere import txt
            import xml.etree.ElementTree as ET
            unsupported = {str(w['clip_id']) for w in fw.warnings if w['code']=='unsupported_effect'}
            archive = {'source':str(source),'source_sha256':p.sha256,'clips':{},'components':{},'parameters':{},'binary':{}}
            for cid in unsupported:
                c=p.clips[cid]
                refs=[]
                for e in c['effects']:
                    eid=e.get('ObjectID') or str(len(archive['components']))
                    archive['components'][eid]=ET.tostring(e,encoding='unicode')
                    refs.append(eid)
                    for v in p.params(e):
                        archive['parameters'][v.get('ObjectID')]=ET.tostring(v,encoding='unicode')
                    for node in [e,*p.params(e)]:
                        for v in node.iter():
                            h=v.get('BinaryHash')
                            if h in fw.effect_mapper.binary:
                                archive['binary'][h]=fw.effect_mapper.binary[h]
                archive['clips'][cid]={'name':c['name'],'sequence':c['sequence'],'components':refs}
            if unsupported:
                (folder/'EFEITOS_PREMIERE.json').write_text(json.dumps(archive,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
                report['outputs'].append('EFEITOS_PREMIERE.json')
            report['outputs'].extend(str(f.relative_to(folder)) for f in (folder/'recursos').glob('*.cube'))
            report['warnings'].extend({'format':'fcproj',**w} for w in p.warnings if w['code'] in ('media_orientation','probe_unavailable','probe_failed','media_metadata_alignment'))
        if stop.is_set():
            raise Cancelled()
        if options.xml:
            log('Gerando XML — '+options.xml_profile)
            assets = folder/'midias_geradas'
            if options.xml_profile=='resolve':
                prepare_holds(p, assets, stop, log)
            cls = writer_class(options.xml_profile)
            xmlfile = folder/(source.stem+'.xml')
            save_xml(cls(p, assets).document(), xmlfile)
            report['outputs'].append(xmlfile.name)
            if options.individual_xml:
                for i, (uid, seq) in enumerate(p.sequences.items(), 1):
                    if stop.is_set():
                        raise Cancelled()
                    target = folder/'sequencias'/f'{i:02d}_{safe_name(seq["name"])}.xml'
                    save_xml(cls(p, assets).document(uid), target)
                    report['outputs'].append(str(target.relative_to(folder)))
            report['warnings'].extend({'format':'xml',**w} for w in p.warnings)
        for uid, seq in p.sequences.items():
            report['sequences'].append({'name': seq['name'], 'uid': uid, 'fps': TICKS/seq['tpf'],
                                       'width': seq['size'][0], 'height': seq['size'][1], 'seconds': seq['duration']/TICKS})
        report['status'] = 'complete'
        report['network_paths'] = network.report()
        report['output_sha256'] = {f:hashlib.sha256((folder/f).read_bytes()).hexdigest() for f in report['outputs']}
        write_report(report, folder)
        log(f'Concluído: {len(p.sequences)} sequências, {len(report["warnings"])} avisos.')
        return folder, report
    except Exception as exc:
        report['status'] = 'cancelled' if isinstance(exc, Cancelled) else 'failed'
        report['network_paths'] = network.report()
        report['error'] = str(exc) or 'Cancelado pelo usuário'
        write_report(report, folder)
        raise
