# Componentes e avisos de terceiros

O pacote portátil inclui os componentes abaixo sem alterações em seu código. Os textos e metadados de licença acompanham `licencas/`.

| Componente | Versão usada | Referência |
| --- | --- | --- |
| CPython | 3.12.10 | [Código-fonte](https://github.com/python/cpython/tree/v3.12.10) e `licencas/Python-LICENSE.txt` |
| PySide6, Shiboken6 e Qt | 6.11.0 | [Qt for Python](https://doc.qt.io/qtforpython-6/), [fontes PySide/Shiboken](https://code.qt.io/cgit/pyside/pyside-setup.git/tag/?h=v6.11.0), [fontes Qt](https://code.qt.io/cgit/qt/qt5.git/tag/?h=v6.11.0) |
| PyInstaller | 6.20.0 | [Fontes e licenças](https://github.com/pyinstaller/pyinstaller/tree/v6.20.0) e `licencas/PyInstaller/` |

Qt/PySide6/Shiboken6 são utilizados sob **GNU LGPL versão 3**. Os textos da LGPLv3 e GPLv3 estão em `licencas/Qt/`. Os avisos de oferta comercial que acompanham as distribuições originais foram preservados; esta distribuição utiliza a alternativa livre aplicável aos componentes incluídos.

As bibliotecas Qt/PySide6/Shiboken6 são carregadas dinamicamente da pasta `_internal`. Elas podem ser substituídas por versões modificadas com interface binária compatível. Não há bloqueio de substituição ou restrição à engenharia reversa necessária para depurar essas modificações. O código da aplicação, o arquivo `.spec`, as dependências fixadas e as instruções de compilação acompanham `codigo_fonte/` no ZIP. Para reconstruir, instale as versões desejadas em um ambiente Python e execute `python build.py`.

O pacote exclui os plugins Qt PDF e Qt Virtual Keyboard e suas bibliotecas QML/Quick não utilizadas. O runtime Python inclui avisos de suas dependências em `Python-LICENSE.txt`; os avisos adicionais do Qt estão em `licencas/Qt/`.

O catálogo de parâmetros e os padrões do formato FilmCraft foram consultados no **FilmCraft 0.19.0**, schema 17, e em suas fontes no commit `2d536e6af1c886887e8299edfb34b5d565e30a69`. Os avisos MIT/Apache 2.0 originais estão em `licencas/FilmCraft/`. Copyright © 2026 ArtCraft Team and the FilmCraft contributors. O editor FilmCraft não está incluído.

FFmpeg e FFprobe são ferramentas externas opcionais e não integram este pacote. Adobe Premiere, plugins de efeitos, arquivos de mídia e LUTs de terceiros também não estão incluídos.
