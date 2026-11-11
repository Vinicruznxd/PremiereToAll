# PremiereToAll

Conversor local de projetos **Adobe Premiere (`.prproj`)** para **FilmCraft (`.fcproj`)** e **XML Final Cut Pro 7**, com interface para Windows e conversão em lote. Não precisa de Premiere instalado nem de serviço de conversão.

**[Baixar a versão portátil para Windows](https://github.com/Vinicruznxd/PremiereToAll/releases/latest)** · [Como compilar](#desenvolvimento) · [Limitações](#compatibilidade)

## Versão portátil

1. Baixe `PremiereToAll-1.3.0-Windows-portable.zip` na página de versões.
2. Extraia **todo** o ZIP para uma pasta no computador ou em um pendrive.
3. Abra `PremiereToAll.exe` dentro da pasta extraída. Mantenha `_internal` ao lado dele.

O pacote inclui Python e as bibliotecas da interface. Não usa instalador, não precisa instalar Python e pode ser movido com a pasta inteira. Distribuição para **Windows de 64 bits**. Os caminhos das mídias nos projetos continuam apontando para suas localizações originais; copiar o aplicativo não copia os vídeos.

FFprobe e FFmpeg são **opcionais e não estão incluídos**. Se estiverem disponíveis no computador, ajudam na conferência de orientação/FPS e em certos trechos quase congelados do perfil DaVinci. Sem eles, a conversão usa os metadados do projeto e registra as limitações no relatório.

## Como usar

1. Arraste os arquivos `.prproj` para a área **Projetos**, ou clique em **Adicionar projetos**. O botão direito oferece **Remover da fila**.
2. Escolha FilmCraft, XML ou ambos. Cada resultado é salvo, por padrão, em uma **subpasta ao lado do seu próprio projeto de origem**, mesmo em lotes com pastas diferentes. É possível escolher uma pasta comum.
3. Clique em **Converter**. Projetos concluídos saem da fila e aparecem em **Resultados**, com o botão **Abrir pasta**. Falhas permanecem para nova tentativa.

No FilmCraft, use **File → Open Project** para `.fcproj`, e **File → Import** para XML. Consulte `RELATORIO.html` na pasta gerada. O conversor preserva os arquivos de origem e cria outra pasta numerada se o destino já existir.

## Recursos

- Sequências, cortes, trilhas, recortes de origem, velocidades e transições compatíveis.
- Nests e camadas de ajuste no formato nativo do FilmCraft.
- Combinação de automações de volume em nests; perfis XML para FilmCraft e DaVinci/FCP7.
- Mapeamento explícito de efeitos compatíveis: Lumetri/Color Grading, Tint, Ultra Key, Crop, Transform, desfoques e outros.
- LUTs reconhecidas incorporadas à biblioteca do `.fcproj`, incluindo tabelas recuperadas do projeto.
- Sequências de imagens numeradas no formato nativo, preservando o FPS.
- Reconhecimento de caminhos UNC equivalentes às unidades mapeadas do Windows, inclusive quando o servidor aparece por nome ou IP. Sem mapeamento confirmado, o caminho UNC permanece.
- Relatório de conversão e arquivo de dados dos efeitos sem equivalente.

## Compatibilidade

O formato nativo foi conferido com **FilmCraft 0.19.0, schema 17**. Para esse editor, prefira `.fcproj`: o XML FCP7 oferece menos recursos e representa camadas de ajuste como marcadores.

Conversão concluída não garante reprodução audiovisual idêntica. Os motores de cor, áudio, interpolação e transições diferem. Plugins Adobe/Film Impact/After Effects, MOGRTs, alguns gráficos, máscaras, Warp Stabilizer e controles avançados podem exigir reconstrução ou renderização no programa de origem. Os efeitos sem conversão são registrados em `EFEITOS_PREMIERE.json`; esse registro não executa plugins ausentes.

O conversor não renderiza uma cópia dos vídeos. Mantenha as mídias e unidades de rede acessíveis na sessão do Windows utilizada para converter e editar. **Localizar mídias** permite remapear uma pasta de origem.

## Desenvolvimento

Use **Python 3.12 de 64 bits** no Windows. Na raiz do repositório:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

Conversão pela linha de comando:

```powershell
python main.py --cli "C:\Projetos\montagem.prproj" --format both --xml-profile filmcraft
python main.py --cli "C:\Projetos\montagem.prproj" --output "C:\Convertidos" --xml-profile resolve
```

Sem `--output`, o resultado vai para a pasta do projeto de origem. `--help` lista as demais opções. O executável portátil aceita os mesmos argumentos.

### Testes

```powershell
python -m pytest -q
```

Os testes de interface usam a plataforma Qt `offscreen`. Casos unitários usam dados sintéticos. Algumas regressões opcionais procuram projetos específicos em `tests/fixtures/` e são ignoradas quando esses arquivos não existem. Projetos privados, vídeos, LUTs de terceiros e relatórios reais não acompanham o repositório.

### Compilar e empacotar

```powershell
python build.py
python package_portable.py
```

O PyInstaller gera `dist/PremiereToAll/`. O empacotador cria o ZIP portátil e `SHA256SUMS.txt` em `outputs/`, incluindo os arquivos necessários, código-fonte, documentação e avisos de terceiros. O manifesto interno relaciona os arquivos e seus hashes SHA-256.

`PremiereToAll.spec` evita bibliotecas ICU incompatíveis capturadas de outros programas no `PATH` e remove plugins Qt PDF/Virtual Keyboard não utilizados pela interface.

## Referências e componentes

Consulte [COMPONENTES.md](COMPONENTES.md) e a pasta [licencas](licencas) para os avisos de terceiros, fontes e versões. PremiereToAll é um projeto independente; Adobe Premiere e FilmCraft pertencem aos seus respectivos responsáveis.

- [Formato de projetos FilmCraft](https://github.com/storytold/filmcraft/blob/main/docs/project-files.md)
- [Importador de presets FilmCraft](https://github.com/storytold/filmcraft/blob/main/crates/engine/src/prfpset.rs)
- [Enumerações observadas no Premiere](https://github.com/Pouf/py-premiere/blob/main/src/py_premiere/enums/general.py)
- [API Windows WNetGetConnectionW](https://learn.microsoft.com/en-us/windows/win32/api/winnetwk/nf-winnetwk-wnetgetconnectionw)
