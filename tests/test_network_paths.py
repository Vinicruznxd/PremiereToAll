"""Network aliases must not relink media from a different share or directory."""
import hashlib
import json
from pathlib import Path
import socket
import threading
import xml.etree.ElementTree as ET
from urllib.parse import unquote
import pytest
from premieretoall.network_paths import NetworkDrive, NetworkPathMapper, resolve_host_addresses
from premieretoall import service
from premieretoall.premiere import Project


def resolver(host):
    return {'media-server': {'192.0.2.10'}, 'media-server.local': {'192.0.2.10'},
            'other-server': {'192.0.2.11'}}.get(host, set())


def mapper():
    return NetworkPathMapper([NetworkDrive('X:', r'\\192.0.2.10\media'),
                              NetworkDrive('R:', r'\\192.0.2.10\archive')], resolver=resolver)


@pytest.mark.parametrize('path,expected', [
    (r'\\Media-server\media\Tools', 'X:/Tools'),
    (r'\\192.0.2.10\media\Vídeos\cena #1.mov', 'X:/Vídeos/cena #1.mov'),
    ('//MEDIA-SERVER/MEDIA/Videos/a.mov', 'X:/Videos/a.mov'),
    ('//media-server.local./media/a.mov', 'X:/a.mov'),
    ('//media-server/archive/a.wav', 'R:/a.wav'),
    ('//media-server/media', 'X:/'),
    (r'\\?\UNC\Media-server\media\Vídeos\a.mov', 'X:/Vídeos/a.mov'),
    (r'X:\Videos\a.mov', r'X:\Videos\a.mov'),
    (r'D:\Videos\a.mov', r'D:\Videos\a.mov'),
    ('//other-server/media/a.mov', '//other-server/media/a.mov'),
    ('//media-server/media-extra/a.mov', '//media-server/media-extra/a.mov'),
    ('//media-server/sem_mapeamento/a.mov', '//media-server/sem_mapeamento/a.mov'),
    ('//unknown/media/a.mov', '//unknown/media/a.mov'),
    ('file://media-server/media/a.mov', 'file://media-server/media/a.mov'),
    ('relative/a.mov', 'relative/a.mov'),
])
def test_only_equivalent_network_roots_are_replaced(path, expected):
    assert mapper().map_path(path) == expected


def test_mapped_subfolders_use_longest_bounded_prefix():
    m = NetworkPathMapper([NetworkDrive('X:', '//NAS/share'),
                           NetworkDrive('P:', '//NAS/share/Projetos')])
    assert m.map_path('//nas/share/PROJETOS/Filme/a.mov') == 'P:/Filme/a.mov'
    assert m.map_path('//nas/share/Projetos_2026/a.mov') == 'X:/Projetos_2026/a.mov'
    assert m.map_path('//nas/share/Projetos/../Musica/a.wav') == 'X:/Musica/a.wav'
    assert m.map_path('X:/Projetos/Filme/a.mov') == 'X:/Projetos/Filme/a.mov'
    only_subfolder = NetworkPathMapper([NetworkDrive('P:', '//NAS/share/Projetos')])
    assert only_subfolder.map_path('//nas/share/Outros/a.mov') == '//nas/share/Outros/a.mov'


def test_no_drives_preserves_unc_without_dns():
    def unexpected_lookup(host):
        raise AssertionError('There is no candidate drive; DNS should not be used')
    m = NetworkPathMapper(resolver=unexpected_lookup)
    path = r'\\MEDIA-SERVER\media\a.mov'
    assert m.map_path(path) == path
    assert m.report()['changes'] == []


def test_exact_server_still_maps_when_dns_is_unavailable():
    def unavailable(host):
        raise socket.gaierror('No network')
    m = NetworkPathMapper([NetworkDrive('Z:', '//NAS/Shared')], resolver=unavailable)
    assert m.map_path('//nas/SHARED/a.mov') == 'Z:/a.mov'
    assert m.map_path('//alias/Shared/a.mov') == '//alias/Shared/a.mov'
    assert 'alias' in m.report()['unresolved_hosts']


def test_dns_is_cached_per_conversion_but_not_between_conversions():
    calls = []
    def lookup(host):
        calls.append(host)
        return {'192.0.2.10'}
    drives = [NetworkDrive('X:', '//192.0.2.10/media')]
    first = NetworkPathMapper(drives, resolver=lookup)
    for i in range(40):
        assert first.map_path(f'//NAS/media/cena{i}.mov') == f'X:/cena{i}.mov'
    assert calls == ['nas']
    second = NetworkPathMapper(drives, resolver=lambda host: {'192.0.2.11'})
    assert second.map_path('//NAS/media/a.mov') == '//NAS/media/a.mov'


def test_duplicate_roots_are_deterministic_and_existing_letters_are_kept():
    m = NetworkPathMapper([NetworkDrive('Z:', '//NAS/share'), NetworkDrive('X:', '//NAS/share')])
    assert m.map_path('//nas/share/a.mov') == 'X:/a.mov'
    assert m.map_path('Z:/a.mov') == 'Z:/a.mov'


def test_ipv6_and_ipv4_mapped_addresses_are_comparable():
    m = NetworkPathMapper([NetworkDrive('V:', '//2001-db8--1.ipv6-literal.net/share')],
                          resolver=lambda host: {'2001:db8::1'})
    assert m.map_path('//NAS/share/a.mov') == 'V:/a.mov'
    m = NetworkPathMapper([NetworkDrive('V:', '//192.0.2.10/share')],
                          resolver=lambda host: {'::ffff:192.0.2.10'})
    assert m.map_path('//NAS/share/a.mov') == 'V:/a.mov'


def test_slow_resolution_returns_without_waiting_for_system_timeout(monkeypatch):
    release = threading.Event()
    def blocked(*args, **kwargs):
        release.wait(5)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.0.2.10', 0))]
    monkeypatch.setattr(socket, 'getaddrinfo', blocked)
    try:
        assert resolve_host_addresses('NAS', timeout=.01) == frozenset()
    finally:
        release.set()


def test_explicit_manual_remap_runs_before_network_mapping():
    path = service.mapped_path('//old-nas/old-share/a.mov', Path('C:/'),
                               [('//old-nas/old-share', '//Media-server/media')])
    assert mapper().map_path(path) == 'X:/a.mov'
    path = service.mapped_path('//Media-server/media/a.mov', Path('C:/'),
                               [('//Media-server/media', 'D:/Local')])
    assert mapper().map_path(path) == 'D:/Local/a.mov'


@pytest.mark.parametrize('profile', ['filmcraft', 'resolve'])
def test_conversion_relinks_native_and_every_xml_without_changing_source(tmp_path, monkeypatch, profile):
    source = Path(__file__).resolve().parent/'fixtures/editing_regression.prproj'
    if not source.exists():
        pytest.skip('Projeto local de regressão não distribuído')
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    project = Project(source)
    media = list(dict.fromkeys(c['media'] for c in project.clips.values() if c['media']))[:4]
    paths = [r'\\Media-server\media\Vídeos\a #1.mov', 'X:/Vídeos/b.mov',
             r'\\192.0.2.10\archive\c.wav', r'\\other-server\media\d.mov']
    for uid, path in zip(media, paths):
        project.media[uid]['path'] = path
    monkeypatch.setattr(service, 'Project', lambda path: project)
    monkeypatch.setattr(service.NetworkPathMapper, 'discover', lambda: mapper())
    folder, report = service.convert_project(source, service.Options(tmp_path/profile,
        xml_profile=profile, check_media=False, probe_media=False))
    expected = ['X:/Vídeos/a #1.mov', 'X:/Vídeos/b.mov', 'R:/c.wav', '//other-server/media/d.mov']
    doc = json.loads((folder/(source.stem+'.fcproj')).read_text(encoding='utf-8'))
    native_paths = [i['kind']['Media']['media']['File']['path'] for i in doc['project']['items'].values()
                    if 'File' in i['kind'].get('Media', {}).get('media', {})]
    assert all(p in native_paths for p in expected)
    urls = [unquote(n.text) for n in ET.parse(folder/(source.stem+'.xml')).iter('pathurl')]
    assert all(any(url.endswith(p) for url in urls) for p in expected)
    for xml in folder.rglob('*.xml'):
        assert all(not unquote(n.text).casefold().startswith(('file://media-server/', 'file://192.0.2.10/'))
                   for n in ET.parse(xml).iter('pathurl'))
    changes = report['network_paths']['changes']
    assert any(x['mapped'] == expected[0] for x in changes)
    assert any(x['mapped'] == expected[2] for x in changes)
    assert not any(x['mapped'] == expected[1] for x in changes)
    assert 'Caminhos de rede' in (folder/'RELATORIO.html').read_text(encoding='utf-8')
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
