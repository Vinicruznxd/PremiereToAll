"""PremiereToAll desktop entry point and batch CLI."""
import argparse
import json
import sys
from pathlib import Path


def main():
    for stream in (sys.stdout,sys.stderr):
        if stream is not None and hasattr(stream,'reconfigure'):
            stream.reconfigure(encoding='utf-8',errors='replace')
    parser=argparse.ArgumentParser(description='PremiereToAll — conversão local de .prproj para XML e FilmCraft .fcproj')
    parser.add_argument('projects', nargs='*', type=Path)
    parser.add_argument('--cli', action='store_true', help='Converter sem abrir a interface')
    parser.add_argument('--output', type=Path, help='Pasta comum opcional; por padrão, salva ao lado de cada .prproj')
    parser.add_argument('--format', choices=['both','xml','fcproj'], default='both')
    parser.add_argument('--xml-profile', choices=['filmcraft','resolve'], default='filmcraft')
    parser.add_argument('--no-individual', action='store_true')
    parser.add_argument('--no-media-check', action='store_true')
    parser.add_argument('--no-probe', action='store_true', help='Usar somente metadados do projeto')
    parser.add_argument('--remap', nargs=2, action='append', metavar=('ANTIGO','NOVO'), default=[])
    args=parser.parse_args()
    if not args.cli:
        from premieretoall.gui import main as gui
        return gui(args.projects)
    if not args.projects:
        parser.error('--cli requer ao menos um projeto')
    from premieretoall.service import Options,convert_project
    options=Options(args.output,xml=args.format!='fcproj',fcproj=args.format!='xml',xml_profile=args.xml_profile,
                    individual_xml=not args.no_individual,check_media=not args.no_media_check,probe_media=not args.no_probe,remaps=args.remap)
    status=0
    for project in args.projects:
        try:
            folder,report=convert_project(project,options,lambda msg: print(msg,file=sys.stderr,flush=True))
            print(json.dumps({'project':str(project),'folder':str(folder),'sequences':len(report['sequences']),'warnings':len(report['warnings'])},ensure_ascii=False))
        except Exception as exc:
            print(json.dumps({'project':str(project),'error':str(exc)},ensure_ascii=False),file=sys.stderr)
            status=1
    return status


if __name__=='__main__':
    raise SystemExit(main())
