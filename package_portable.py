"""Package an allowlisted Windows build without projects, media or local reports."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

from premieretoall import __version__

ROOT_FILES = ('README.md', 'LEIA-ME-PORTABLE.txt', 'COMPONENTES.md', 'CHANGELOG.md')
SOURCE_FILES = ('main.py', 'build.py', 'package_portable.py', 'PremiereToAll.spec',
                'requirements.txt', 'pytest.ini', '.gitignore', '.gitattributes', *ROOT_FILES)


def main():
    base = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist', type=Path, default=base / 'dist' / 'PremiereToAll')
    parser.add_argument('--output', type=Path, default=base / 'outputs')
    args = parser.parse_args()
    exe = args.dist / 'PremiereToAll.exe'
    if not exe.is_file() or not (args.dist / '_internal' / 'python312.dll').is_file():
        parser.error('Execute python build.py antes de empacotar a versão Windows.')
    members = {'PremiereToAll.exe': exe}
    for path in (args.dist / '_internal').rglob('*'):
        if path.is_file():
            members[path.relative_to(args.dist).as_posix()] = path
    for name in ROOT_FILES:
        members[name] = base / name
    for path in (base / 'licencas').rglob('*'):
        if path.is_file():
            members[path.relative_to(base).as_posix()] = path
    for name in SOURCE_FILES:
        members['codigo_fonte/' + name] = base / name
    for directory, suffixes in [('premieretoall', {'.py', '.json'}), ('tests', {'.py'})]:
        for path in (base / directory).glob('*'):
            if path.is_file() and path.suffix in suffixes:
                members['codigo_fonte/' + path.relative_to(base).as_posix()] = path
    for path in (base / 'licencas').rglob('*'):
        if path.is_file():
            members['codigo_fonte/' + path.relative_to(base).as_posix()] = path
    forbidden = {'icuuc.dll', 'qt6virtualkeyboard.dll', 'qtvirtualkeyboardplugin.dll', 'qpdf.dll'}
    if any(Path(name).name.lower() in forbidden for name in members):
        parser.error('A compilação contém bibliotecas incompatíveis ou plugins Qt não utilizados.')
    manifest = {'application': 'PremiereToAll', 'version': __version__,
                'platform': 'windows-x86_64', 'files': []}
    args.output.mkdir(parents=True, exist_ok=True)
    archive = args.output / f'PremiereToAll-{__version__}-Windows-portable.zip'
    prefix = f'PremiereToAll-{__version__}-portable/'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as package:
        for name, path in sorted(members.items()):
            data = path.read_bytes()
            manifest['files'].append({'path': name, 'size': len(data),
                                      'sha256': hashlib.sha256(data).hexdigest()})
            package.writestr(prefix + name, data)
        package.writestr(prefix + 'MANIFESTO.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    with zipfile.ZipFile(archive) as package:
        if package.testzip() is not None:
            raise RuntimeError('Falha na verificação do ZIP')
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (args.output / 'SHA256SUMS.txt').write_text(f'{digest}  {archive.name}\n', encoding='utf-8')
    print(json.dumps({'archive': str(archive), 'bytes': archive.stat().st_size,
                      'files': len(members) + 1, 'sha256': digest}))


if __name__ == '__main__':
    main()
