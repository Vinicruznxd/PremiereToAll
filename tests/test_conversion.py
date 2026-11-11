import json
from pathlib import Path
import threading
import xml.etree.ElementTree as ET
import pytest
from premieretoall.premiere import Project, Writer, TICKS
from premieretoall.xml_writer import FilmCraftXMLWriter
from premieretoall.filmcraft import FilmCraftWriter, validate_document
from premieretoall.service import Options, convert_project, Cancelled, mapped_path, unique_directory

INPUTS = Path(__file__).resolve().parent/'fixtures'


@pytest.fixture(scope='module')
def editing_project():
    path=INPUTS/'editing_regression.prproj'
    if not path.exists():
        pytest.skip('Projetos de regressão locais não incluídos no código distribuído')
    return Project(path)


def test_filmcraft_xml_keeps_clips_with_two_fades(editing_project,tmp_path):
    uid=next(u for u,s in editing_project.sequences.items() if any(c['head'] and c['tail'] for tr in s['audio'] for c in tr['clips']))
    seq=editing_project.sequences[uid]
    double=[c for tr in seq['audio'] for c in tr['clips'] if c['head'] and c['tail']]
    assert double, 'A regressão precisa de clipes com transição nas duas pontas'
    root=FilmCraftXMLWriter(editing_project,tmp_path).document(uid)
    clips=root.findall('sequence/media/audio/track/clipitem')
    assert len(clips)==sum(len(c['channels']) for tr in seq['audio'] for c in tr['clips'])
    assert all(int(c.findtext('start'))>=0 and int(c.findtext('end'))>=0 for c in clips)
    for native in double:
        match=[c for c in clips if c.findtext('pproTicksIn')==str(native['in'])]
        assert match
    assert root.findall('sequence/media/audio/track/transitionitem')


def test_native_roundtrip_preserves_all_edit_times_and_transitions(editing_project):
    writer=FilmCraftWriter(editing_project)
    doc=writer.document()
    validate_document(doc)
    nativeclips={c['id']:c for s in editing_project.sequences.values() for k in ('video','audio') for t in s[k] for c in t['clips']}
    actual={c['id']:c for i in doc['project']['items'].values() if 'Sequence' in i['kind']
            for k in ('video_tracks','audio_tracks') for t in i['kind']['Sequence'][k] for c in t['items']}
    assert len(actual)==len(nativeclips)==566
    for cid,c in nativeclips.items():
        got=actual[writer.clip_ids[cid]]
        assert (got['start'],got['duration'],got['source_in'],got['speed'],got['reverse']) == (
            c['start'],c['end']-c['start'],c['in'],abs(c['speed']),c['reverse'] or c['speed']<0)
    for uid,s in editing_project.sequences.items():
        got=doc['project']['items'][str(writer.seq_ids[uid])]['kind']['Sequence']
        for k in ('video','audio'):
            assert sum(len(t['transitions']) for t in got[k+'_tracks'])==sum(len(t['transitions']) for t in s[k])
    assert any('Bin' in e for e in doc['project']['root']['children'])


def test_native_validator_rejects_missing_media_reference(editing_project):
    doc=FilmCraftWriter(editing_project).document()
    seq=next(i['kind']['Sequence'] for i in doc['project']['items'].values() if 'Sequence' in i['kind'])
    seq['video_tracks'][0]['items'][0]['item']=999999999
    with pytest.raises(ValueError,match='Clipe inválido'):
        validate_document(doc)


def test_path_remap_is_prefix_bounded():
    assert mapped_path('X:\\VIDEOS\\a.mov',Path('C:/'),[('x:/videos','D:/copia')])=='D:/copia/a.mov'
    assert mapped_path('X:\\VIDEOS_OUTROS\\a.mov',Path('C:/'),[('X:/VIDEOS','D:/copia')])=='X:/VIDEOS_OUTROS/a.mov'


def test_no_overwrite_existing_output(tmp_path):
    first=unique_directory(tmp_path,'projeto')
    (first/'original.txt').write_text('preservar')
    second=unique_directory(tmp_path,'projeto')
    assert first!=second
    assert (first/'original.txt').read_text()=='preservar'


def test_cancel_before_writing_does_not_create_result(editing_project,tmp_path):
    stop=threading.Event();stop.set()
    with pytest.raises(Cancelled):
        convert_project(editing_project.path,Options(tmp_path,probe_media=False),stop=stop)
    assert not list(tmp_path.iterdir())


def test_bad_project_reports_error_and_does_not_touch_source(tmp_path):
    source=tmp_path/'bad.prproj'; source.write_bytes(b'not premiere')
    with pytest.raises(ET.ParseError):
        convert_project(source,Options(tmp_path/'out',probe_media=False))
    assert source.read_bytes()==b'not premiere'
    assert not (tmp_path/'out').exists()
