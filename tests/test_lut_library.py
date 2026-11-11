import json
from pathlib import Path
import xml.etree.ElementTree as ET
import pytest
from premieretoall.premiere import Project, txt
from premieretoall.filmcraft import FilmCraftWriter, validate_document


def empty_project(tmp_path):
    path=tmp_path/'test.prproj';path.write_text('<PremiereData/>')
    return Project(path)


def test_lut_content_is_embedded_once_and_survives_source_deletion(tmp_path):
    p=empty_project(tmp_path);w=FilmCraftWriter(p)
    lut=tmp_path/'identity.cube';text='LUT_3D_SIZE 2\n'+'\n'.join(f'{r} {g} {b}' for b in range(2) for g in range(2) for r in range(2))+'\n';lut.write_text(text)
    copy=tmp_path/'copy.cube';copy.write_text(text)
    ref=w.import_lut(lut,{},'Identity')
    assert ref.startswith('lib:') and w.import_lut(copy,{})==ref
    stored_text=lut.read_bytes().decode('utf-8')
    lut.unlink();copy.unlink()
    doc=w.document();validate_document(doc)
    assert len(doc['project']['luts'])==1
    assert doc['project']['luts'][0]['text']==stored_text


def test_unreadable_lut_is_reported_without_an_unusable_file_reference(tmp_path):
    w=FilmCraftWriter(empty_project(tmp_path))
    assert w.import_lut(tmp_path/'missing.cube',{})==''
    assert w.warnings[0]['code']=='lut_unavailable'
    assert w.document()['project']['luts']==[]


def test_actual_adjustment_uses_embedded_creative_lut_with_empty_input_slot(tmp_path):
    source=Path(__file__).resolve().parent/'fixtures/adjustment_regression.prproj'
    if not source.exists():pytest.skip('Projeto local de reprodução não distribuído')
    p=Project(source)
    # The cached LUT must work even when the original share/path is unavailable.
    p.media_path_resolver=lambda path: str(tmp_path/'original_lut_offline.cube')
    w=FilmCraftWriter(p,tmp_path/'resources');doc=w.document();validate_document(doc)
    lumetri=[e for item in doc['project']['items'].values() if 'Sequence' in item['kind']
             for tr in item['kind']['Sequence']['video_tracks'] for c in tr['items'] for e in c['effects'] if e['effect']=='lumetri']
    assert len(lumetri)==1
    params=lumetri[0]['params']
    assert params['input_lut']['value']['Text']==''
    ref=params['look_lut']['value']['Text']
    assert ref.startswith('lib:')
    assert params['look_intensity']['value']['Float']==100
    assert params['exposure']['value']['Float']==0
    assert params['contrast']['value']['Float']==0
    assert params['shadow_tint']['value']['Color']==[.5,.5,.5,1.]
    assert params['highlight_tint']['value']['Color']==[.5,.5,.5,1.]
    assert len(doc['project']['luts'])==1
    entry=doc['project']['luts'][0]
    assert entry['id']==ref[4:] and 'LUT_3D_SIZE 33' in entry['text']
    assert 'DOMAIN_MIN 0 0 0' in entry['text']
    assert not any(warning['code'] in ('lut_missing','lut_unavailable') for warning in w.warnings)
    bad=json.loads(json.dumps(doc));bad['project']['luts']=[]
    with pytest.raises(ValueError,match='Referência de LUT'):
        validate_document(bad)
