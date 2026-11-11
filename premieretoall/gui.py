"""Native Windows desktop interface for PremiereToAll."""
from __future__ import annotations
import os
import sys
import threading
from pathlib import Path
from PySide6.QtCore import Qt, QThread, Signal, QUrl, QSize, QPointF
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPixmap, QFont, QPen, QPainterPath
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QTableWidget, QTableWidgetItem, QHeaderView, QFileDialog, QCheckBox,
    QComboBox, QLineEdit, QProgressBar, QPlainTextEdit, QMessageBox, QAbstractItemView,
    QGridLayout, QSizePolicy, QStackedWidget, QScrollArea, QMenu,
)
from .service import Options, convert_project, Cancelled, find_ffmpeg
from . import __version__

STYLE = r'''
QMainWindow, QWidget#canvas {background:#0e141d;color:#edf1f7;}
QWidget {font-family:"Segoe UI";font-size:14px;color:#e8edf5;}
QLabel#title {font-size:32px;font-weight:700;color:#f5f7fb;}
QLabel#subtitle {color:#a6b7cc;font-size:16px;}
QLabel#section {font-size:19px;font-weight:600;}
QLabel#muted {color:#9cacbf;}
QLabel#eyebrow {color:#a7bdd8;font-weight:600;font-size:12px;}
QFrame#sidebar {background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #111923,stop:1 #0e141d);border-right:1px solid #2b394c;}
QLabel#brand {font-size:18px;font-weight:600;}
QFrame#rule {background:#2b394b;max-height:1px;border:0;}
QPushButton {background:#202b3a;border:1px solid #4c6079;border-radius:7px;padding:10px 18px;}
QPushButton:hover {background:#2a3a50;border-color:#79a5dc;}
QPushButton:disabled {color:#617187;background:#172130;border-color:#2c3a4e;}
QPushButton#primary {background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #378cff,stop:1 #216ce5);border:1px solid #5da0ff;font-weight:600;font-size:16px;padding:10px 23px;}
QPushButton#primary:hover {background:#418fff;}
QPushButton#primary:disabled {background:#1c3c66;border-color:#25486f;color:#7b93b0;}
QPushButton#quiet {background:transparent;border:0;color:#9cc8ff;padding:5px 3px;}
QPushButton#quiet:hover {color:#d0e6ff;}
QPushButton#resultOpen {padding:2px 6px;font-size:13px;border-radius:5px;}
QPushButton#step {border:0;background:transparent;text-align:left;padding:12px 0;font-size:15px;}
QPushButton#step:disabled {color:#9baabf;background:transparent;}
QFrame#drop {background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #1a2534,stop:1 #121b28);border:1px dashed #466182;border-radius:12px;}
QFrame#drop[hover="true"] {background:#172c46;border:2px dashed #4a99ff;}
QFrame#format {border:1px solid #3c4b60;border-radius:10px;background:#141e2b;}
QFrame#format[selected="true"] {border:2px solid #3e91ff;background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #1a2635,stop:1 #121b27);}
QLabel#formatTitle {font-size:25px;font-weight:700;}
QLineEdit,QComboBox {background:#101923;border:1px solid #3c5069;border-radius:7px;padding:10px;selection-background-color:#2b70bd;}
QLineEdit:focus,QComboBox:focus {border-color:#408fff;}
QComboBox::drop-down {border:0;width:24px;}
QComboBox QAbstractItemView {background:#1c2a3e;selection-background-color:#285c9b;}
QCheckBox {spacing:10px;padding:4px 0;}
QCheckBox::indicator {width:20px;height:20px;border:1px solid #60748d;border-radius:4px;background:#152130;}
QCheckBox::indicator:checked {background:#2b80f7;border-color:#4d99ff;image:url(:/qt-project.org/styles/commonstyle/images/standardbutton-apply-16.png);}
QTableWidget {background:#101924;alternate-background-color:#131e2d;border:1px solid #293c53;border-radius:8px;selection-background-color:#22456f;}
QTableWidget::item {padding:10px;border:0;}
QHeaderView::section {background:#192638;color:#a1b5ce;padding:11px;border:0;font-size:12px;font-weight:600;}
QTableCornerButton::section {background:#192638;border:0;}
QProgressBar {background:#1d2d43;border:0;border-radius:4px;min-height:7px;max-height:7px;}
QProgressBar::chunk {background:#368bff;border-radius:4px;}
QPlainTextEdit {background:#0b111a;border:1px solid #2d415c;border-radius:7px;color:#adc1db;font-family:Consolas;font-size:11px;padding:8px;}
QScrollArea {border:0;background:transparent;}
QScrollBar:vertical {background:#101923;width:9px;}
QScrollBar::handle:vertical {background:#344c6d;min-height:24px;border-radius:4px;}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0;}
QMessageBox,QFileDialog {background:#172231;}
QMenu {background:#172231;color:#edf1f7;border:1px solid #3b4d63;padding:5px;}
QMenu::item {padding:8px 18px;} QMenu::item:selected {background:#294d79;}
QToolTip {background:#26394f;color:#e8f2ff;border:1px solid #5c7b9f;padding:7px;}
'''



def app_root():
    return Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent


def make_icon():
    p = QPixmap(128,128)
    p.fill(QColor('#101b28'))
    q = QPainter(p)
    q.setRenderHint(QPainter.RenderHint.Antialiasing)
    q.setPen(Qt.PenStyle.NoPen)
    q.setBrush(QColor('#398cff'))
    q.drawRoundedRect(12,12,104,104,24,24)
    q.setPen(QColor('#ffffff'))
    q.setFont(QFont('Segoe UI',52,QFont.Weight.Bold))
    q.drawText(p.rect(), Qt.AlignmentFlag.AlignCenter, 'P')
    q.end()
    return QIcon(p)


class ConversionWorker(QThread):
    log_line = Signal(int, str)
    job_done = Signal(int, str, object)
    job_failed = Signal(int, str)
    job_cancelled = Signal(int)
    def __init__(self, paths, options):
        super().__init__()
        self.paths = paths
        self.options = options
        self.stop = threading.Event()

    def run(self):
        for i, path in enumerate(self.paths):
            if self.stop.is_set():
                break
            try:
                folder, report = convert_project(path, self.options, lambda message: self.log_line.emit(i,message), self.stop)
                self.job_done.emit(i, str(folder), report)
            except Cancelled:
                self.job_cancelled.emit(i)
                break
            except Exception as exc:
                self.job_failed.emit(i, str(exc))


class StepButton(QPushButton):
    def paintEvent(self,event):
        q=QPainter(self);q.setRenderHint(QPainter.RenderHint.Antialiasing)
        active=bool(self.property('active'));done=bool(self.property('done'))
        q.setPen(QPen(QColor('#3b8cff' if active or done else '#53677f'),1.4))
        q.setBrush(QColor('#2d80ef' if active or done else '#1b2736'));q.drawEllipse(1,7,27,27)
        q.setPen(QPen(QColor('#ffffff' if active or done else '#b5c8df'),2,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin))
        if done:
            path=QPainterPath(QPointF(8,20));path.lineTo(13,25);path.lineTo(22,15);q.drawPath(path)
        else:
            q.setFont(QFont('Segoe UI',10));q.drawText(1,7,27,27,Qt.AlignmentFlag.AlignCenter,str(self.property('number')))
        if self.property('number')!=3:
            q.setPen(QPen(QColor('#46566b'),1));q.drawLine(14,39,14,self.height()-1)
        lines=self.text().split('\n');title=lines[0].split('    ',1)[-1];desc=lines[-1].strip()
        q.setPen(QColor('#f0f4fa' if active or done else '#b8c8db'));q.setFont(QFont('Segoe UI',11,QFont.Weight.DemiBold));q.drawText(44,6,self.width()-44,25,Qt.AlignmentFlag.AlignLeft,title)
        q.setPen(QColor('#a3b8d2'));q.setFont(QFont('Segoe UI',9));q.drawText(44,33,self.width()-44,35,Qt.AlignmentFlag.AlignLeft|Qt.TextFlag.TextWordWrap,desc)


class LineIcon(QWidget):
    def __init__(self, kind, size=66):
        super().__init__()
        self.kind=kind
        self.setFixedSize(size,size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, event):
        q=QPainter(self)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        q.scale(self.width()/80,self.height()/80)
        q.setPen(QPen(QColor('#9bb9df'),2.7,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin))
        if self.kind=='upload':
            q.drawLine(40,49,40,15);q.drawLine(40,15,29,27);q.drawLine(40,15,51,27)
            path=QPainterPath(QPointF(22,44));path.lineTo(22,61);path.lineTo(58,61);path.lineTo(58,44);q.drawPath(path)
        else:
            path=QPainterPath(QPointF(17,7));path.lineTo(48,7);path.lineTo(65,24);path.lineTo(65,72);path.lineTo(17,72);path.closeSubpath();q.drawPath(path)
            path=QPainterPath(QPointF(48,7));path.lineTo(48,24);path.lineTo(65,24);q.drawPath(path)
            if self.kind=='xml':
                q.setFont(QFont('Consolas',20,QFont.Weight.Bold));q.drawText(20,32,42,30,Qt.AlignmentFlag.AlignCenter,'</>')
            else:
                q.setPen(Qt.PenStyle.NoPen);q.setBrush(QColor('#8bbcff'))
                for x,y,w in [(30,34,25),(26,44,22),(22,54,18)]:
                    path=QPainterPath(QPointF(x+4,y));path.lineTo(x+w,y);path.lineTo(x+w-4,y+6);path.lineTo(x,y+6);path.closeSubpath();q.drawPath(path)


class DropZone(QFrame):
    files_dropped=Signal(list)
    clicked=Signal()
    def __init__(self):
        super().__init__(objectName='drop')
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName('Arraste seus arquivos .prproj ou adicione projetos')
        self.setMinimumHeight(192)
        self.setMaximumHeight(230)
        box=QVBoxLayout(self);box.setContentsMargins(20,16,20,18);box.setSpacing(3)
        box.addStretch();box.addWidget(LineIcon('upload',60),0,Qt.AlignmentFlag.AlignHCenter)
        title=QLabel('Arraste seus arquivos .prproj',objectName='section');title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        box.addWidget(title)
        sub=QLabel('ou clique em Adicionar projetos',objectName='muted');sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents);box.addWidget(sub);box.addStretch()
    def highlight(self, yes):
        self.setProperty('hover',yes);self.style().unpolish(self);self.style().polish(self)
    def enterEvent(self,event):
        self.highlight(True)
    def leaveEvent(self,event):
        self.highlight(False)
    def mouseReleaseEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:self.clicked.emit()
    def dragEnterEvent(self,event):
        if any(u.isLocalFile() and u.toLocalFile().lower().endswith('.prproj') for u in event.mimeData().urls()):
            self.highlight(True);event.acceptProposedAction()
    def dragLeaveEvent(self,event):
        self.highlight(False)
    def dropEvent(self,event):
        self.highlight(False);self.files_dropped.emit([u.toLocalFile() for u in event.mimeData().urls()]);event.acceptProposedAction()


class FormatCard(QFrame):
    def __init__(self,title,extension,description,kind):
        super().__init__(objectName='format')
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(154)
        self.check=QCheckBox();self.check.setAccessibleName(title+' '+extension);self.check.setChecked(True)
        self.setProperty('selected',True)
        grid=QGridLayout(self);grid.setContentsMargins(24,13,16,20);grid.setHorizontalSpacing(17)
        grid.addWidget(self.check,0,2,Qt.AlignmentFlag.AlignRight)
        grid.addWidget(LineIcon(kind,73),1,0,3,1)
        for row,text,obj in [(1,title,'formatTitle'),(2,extension,'subtitle'),(3,description,'muted')]:
            label=QLabel(text,objectName=obj);label.setWordWrap(True);label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            grid.addWidget(label,row,1,1,2)
        grid.setColumnStretch(1,1)
        self.check.toggled.connect(self.changed)
    def changed(self,value):
        self.setProperty('selected',value);self.style().unpolish(self);self.style().polish(self)
    def mouseReleaseEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:self.check.toggle()


def rule():
    line=QFrame(objectName='rule');line.setFixedHeight(1);return line


class MainWindow(QMainWindow):
    def __init__(self, initial_files=()):
        super().__init__()
        self.paths=[];self.results=[];self.worker=None;self.done_count=0;self.step=0
        self.batch_paths=[];self.batch_count=0;self.batch_results=[];self.queue_menu=None;self._converting=False
        self.setWindowTitle('PremiereToAll')
        self.setWindowIcon(make_icon());self.resize(1190,800);self.setMinimumSize(1020,730);self.setAcceptDrops(True)
        canvas=QWidget(objectName='canvas');self.setCentralWidget(canvas)
        root=QHBoxLayout(canvas);root.setContentsMargins(0,0,0,0);root.setSpacing(0)
        side=QFrame(objectName='sidebar');side.setFixedWidth(225);sl=QVBoxLayout(side);sl.setContentsMargins(23,27,23,23);sl.setSpacing(10)
        brand=QHBoxLayout();logo=QLabel();logo.setPixmap(make_icon().pixmap(29,29));brand.addWidget(logo);brand.addWidget(QLabel('PremiereToAll',objectName='brand'));brand.addStretch();sl.addLayout(brand);sl.addSpacing(44)
        self.steps=[]
        for i,(title,desc) in enumerate([('Projetos','Adicionar arquivos'),('Saída','Formatos e destino'),('Converter','Revisar e executar')]):
            button=StepButton(objectName='step');button.setMinimumHeight(70);button.setProperty('number',i+1);button.clicked.connect(lambda checked=False,n=i:self.set_page(n));sl.addWidget(button);self.steps.append((button,title,desc))
        sl.addSpacing(16);sl.addWidget(rule());sl.addSpacing(15)
        self.side_projects=QLabel('Seus projetos aparecerão aqui.',objectName='muted');self.side_projects.setWordWrap(True);sl.addWidget(self.side_projects)
        sl.addStretch();sl.addWidget(QLabel(f'VERSÃO {__version__}  ·  LOCAL',objectName='eyebrow'));root.addWidget(side)
        content=QVBoxLayout();content.setContentsMargins(39,22,33,24);content.setSpacing(17)
        help_row=QHBoxLayout();help_row.addStretch();help_btn=QPushButton('Ajuda',objectName='quiet');help_btn.clicked.connect(self.show_help);help_row.addWidget(help_btn);content.addLayout(help_row)
        self.heading=QLabel(objectName='title');self.subtitle=QLabel(objectName='subtitle');content.addWidget(self.heading);content.addWidget(self.subtitle)
        self.pages=QStackedWidget();content.addWidget(self.pages,1)
        projects=QWidget();pl=QVBoxLayout(projects);pl.setContentsMargins(0,13,0,0);pl.setSpacing(16)
        self.drop_zone=DropZone();self.drop_zone.clicked.connect(self.choose_files);self.drop_zone.files_dropped.connect(self.add_files);pl.addWidget(self.drop_zone)
        queue_head=QHBoxLayout();self.queue_label=QLabel(objectName='eyebrow');queue_head.addWidget(self.queue_label);queue_head.addStretch()
        self.remove_btn=QPushButton('Remover selecionados',objectName='quiet');self.remove_btn.clicked.connect(self.remove_selected);queue_head.addWidget(self.remove_btn)
        self.add_btn=QPushButton('Adicionar projetos');self.add_btn.setObjectName('addProjects');self.add_btn.clicked.connect(self.choose_files);queue_head.addWidget(self.add_btn);pl.addLayout(queue_head)
        self.table=QTableWidget(0,3);self.table.setObjectName('projectQueue');self.table.setHorizontalHeaderLabels(['PROJETO','SEQUÊNCIAS','STATUS'])
        self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch);self.table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.Fixed);self.table.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(1,100);self.table.setColumnWidth(2,190);self.table.verticalHeader().hide();self.table.verticalHeader().setDefaultSectionSize(49)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows);self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);self.table.setAlternatingRowColors(True);self.table.setShowGrid(False)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu);self.table.customContextMenuRequested.connect(self.queue_context_menu)
        self.project_table_layout=QVBoxLayout();self.project_table_layout.addWidget(self.table);pl.addLayout(self.project_table_layout,1)
        self.queue_hint=QLabel('Adicione um ou vários projetos para converter em lote.',objectName='muted');pl.addWidget(self.queue_hint);self.pages.addWidget(projects)
        output_page=QScrollArea();output_page.setWidgetResizable(True);self.settings=QWidget(objectName='canvas');output_page.setWidget(self.settings);ol=QVBoxLayout(self.settings);ol.setContentsMargins(0,14,4,8);ol.setSpacing(19)
        formats=QHBoxLayout();formats.setSpacing(19);fc=FormatCard('FilmCraft','.fcproj','Continue sua edição no FilmCraft','fc');xml=FormatCard('XML','.xml','Formato Final Cut Pro 7','xml');formats.addWidget(fc,1);formats.addWidget(xml,1);ol.addLayout(formats)
        self.fc_check=fc.check;self.fc_check.setObjectName('formatFilmcraft');self.xml_check=xml.check;self.xml_check.setObjectName('formatXML')
        xml_options=QHBoxLayout();xml_options.addStretch();self.individual=QCheckBox('Um XML por sequência');self.individual.setChecked(True);xml_options.addWidget(self.individual);self.profile=QComboBox();self.profile.setObjectName('xmlProfile');self.profile.addItem('XML para FilmCraft','filmcraft');self.profile.addItem('XML para DaVinci / FCP7','resolve');xml_options.addWidget(self.profile);ol.addLayout(xml_options)
        ol.addWidget(rule());ol.addWidget(QLabel('Pasta de destino',objectName='section'))
        self.source_output=QCheckBox('Salvar na pasta de cada projeto (padrão)');self.source_output.setChecked(True);self.source_output.setObjectName('saveBesideSource');ol.addWidget(self.source_output)
        destination=QHBoxLayout();self.output=QLineEdit();self.output.setPlaceholderText('Uma subpasta ao lado de cada arquivo .prproj');self.output.setEnabled(False);self.output.setObjectName('outputFolder');destination.addWidget(self.output,1);self.browse_btn=QPushButton('Escolher outra pasta');self.browse_btn.clicked.connect(self.choose_output);destination.addWidget(self.browse_btn);ol.addLayout(destination)
        self.source_output.toggled.connect(lambda checked:self.output.setEnabled(not checked))
        ol.addWidget(rule());media_head=QHBoxLayout();self.media_expand=QPushButton('⌄  Verificações de mídia',objectName='quiet');self.media_expand.setCheckable(True);media_head.addWidget(self.media_expand);media_head.addStretch();self.remap_btn=QPushButton('Localizar mídias',objectName='quiet');self.remap_btn.setCheckable(True);self.remap_btn.toggled.connect(self.toggle_remap);media_head.addWidget(self.remap_btn);ol.addLayout(media_head)
        ol.addWidget(QLabel('Arquivos de origem, orientação e FPS',objectName='muted'))
        network_note=QLabel('Caminhos de rede equivalentes são ajustados automaticamente para as unidades mapeadas neste Windows.',objectName='muted');network_note.setWordWrap(True);ol.addWidget(network_note)
        self.media_widget=QWidget();ml=QVBoxLayout(self.media_widget);ml.setContentsMargins(8,0,0,0);self.media_check=QCheckBox('Verificar arquivos de origem');self.media_check.setChecked(True);self.probe_check=QCheckBox('Conferir orientação e FPS');self.probe_check.setObjectName('probeMedia');self.probe_check.setChecked(True);ml.addWidget(self.media_check);ml.addWidget(self.probe_check);note=QLabel('A conferência usa FFprobe e pode levar mais tempo em unidades de rede.',objectName='muted');note.setWordWrap(True);ml.addWidget(note);ol.addWidget(self.media_widget);self.media_widget.hide();self.media_expand.toggled.connect(self.media_widget.setVisible)
        self.remap_widget=QWidget();rl=QVBoxLayout(self.remap_widget);rl.setContentsMargins(0,0,0,0);self.old_folder=QLineEdit();self.old_folder.setPlaceholderText('Pasta antiga das mídias, por exemplo X:/VIDEOS');self.new_folder=QLineEdit();self.new_folder.setPlaceholderText('Nova pasta das mídias, por exemplo D:/VIDEOS');rl.addWidget(self.old_folder);rl.addWidget(self.new_folder);ol.addWidget(self.remap_widget);self.remap_widget.hide();ol.addStretch();self.pages.addWidget(output_page)
        review=QWidget();rv=QVBoxLayout(review);rv.setContentsMargins(0,13,0,0);rv.setSpacing(15);self.review_summary=QLabel(objectName='subtitle');self.review_summary.setWordWrap(True);rv.addWidget(self.review_summary)
        self.review_table_layout=QVBoxLayout();rv.addLayout(self.review_table_layout,1)
        self.result_label=QLabel('RESULTADOS',objectName='eyebrow');rv.addWidget(self.result_label)
        self.result_table=QTableWidget(0,3);self.result_table.setObjectName('conversionResults');self.result_table.setHorizontalHeaderLabels(['PROJETO','RESULTADO',''])
        self.result_table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch);self.result_table.setColumnWidth(1,180);self.result_table.setColumnWidth(2,120);self.result_table.verticalHeader().hide();self.result_table.verticalHeader().setDefaultSectionSize(46)
        self.result_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows);self.result_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);self.result_table.setMaximumHeight(210);self.result_table.itemDoubleClicked.connect(self.open_result_row);rv.addWidget(self.result_table)
        self.result_label.hide();self.result_table.hide()
        self.compat_note=QLabel('Nests e efeitos compatíveis ficam editáveis. O relatório identifica recursos que precisam de revisão.',objectName='muted');self.compat_note.setWordWrap(True);rv.addWidget(self.compat_note)
        self.progress=QProgressBar();self.progress.setRange(0,100);self.progress.setValue(0);self.progress.setTextVisible(False);rv.addWidget(self.progress)
        self.status=QLabel('Pronto para converter.',objectName='muted');self.status.setWordWrap(True);rv.addWidget(self.status)
        details=QHBoxLayout();self.log_btn=QPushButton('Detalhes da conversão',objectName='quiet');self.log_btn.setCheckable(True);self.log_btn.toggled.connect(self.toggle_log);details.addWidget(self.log_btn);details.addStretch();self.open_btn=QPushButton('Abrir resultados');self.open_btn.setEnabled(False);self.open_btn.clicked.connect(self.open_output);details.addWidget(self.open_btn);rv.addLayout(details)
        self.log=QPlainTextEdit();self.log.setReadOnly(True);self.log.setMaximumBlockCount(3000);self.log.setFixedHeight(132);self.log.hide();rv.addWidget(self.log);self.pages.addWidget(review)
        footer=QHBoxLayout();self.back_btn=QPushButton('Voltar');self.back_btn.clicked.connect(lambda:self.set_page(max(0,self.step-1)));footer.addWidget(self.back_btn);footer.addStretch();self.footer_note=QLabel('Seus arquivos originais são preservados.',objectName='muted');footer.addWidget(self.footer_note);footer.addStretch();self.cancel_btn=QPushButton('Cancelar');self.cancel_btn.clicked.connect(self.cancel);self.cancel_btn.hide();footer.addWidget(self.cancel_btn);self.convert_btn=QPushButton('Continuar  →',objectName='primary');self.convert_btn.clicked.connect(self.advance);footer.addWidget(self.convert_btn);content.addLayout(footer);root.addLayout(content,1)
        self.xml_check.toggled.connect(self.xml_controls);self.xml_check.toggled.connect(self.refresh_count);self.fc_check.toggled.connect(self.refresh_count)
        self.add_files(initial_files);self.set_page(0)

    def show_help(self):
        QMessageBox.information(self,'Como usar','1. Adicione ou arraste seus projetos .prproj.\n2. Escolha os formatos e a pasta de destino.\n3. Revise e converta.\n\nNo FilmCraft, abra o .fcproj em File > Open Project. XML é importado em File > Import.\n\nConfira RELATORIO.html: plugins, gráficos e recursos sem equivalente podem exigir ajustes.')

    def set_page(self,index):
        if self.running() or (index>0 and not self.paths and not (index==2 and self.results)):return
        self.step=index;self.pages.setCurrentIndex(index)
        titles=[('Quais projetos vamos converter?','Traga seus projetos do Premiere. A conversão acontece no seu computador.'),('Como você quer salvar?','Escolha os formatos e a pasta de destino.'),('Tudo pronto para converter','Revise os projetos e acompanhe seus resultados.')]
        self.heading.setText(titles[index][0]);self.subtitle.setText(titles[index][1])
        if index==0:self.project_table_layout.addWidget(self.table)
        elif index==2:
            self.review_table_layout.addWidget(self.table)
            formats=' + '.join(n for n,c in [('FilmCraft',self.fc_check),('XML',self.xml_check)] if c.isChecked())
            destination='Uma subpasta na origem de cada projeto' if self.source_output.isChecked() else self.output.text()
            self.review_summary.setText(f'{len(self.paths)} projeto(s) na fila  ·  {formats}\n{destination}')
            if not self.paths and self.results:
                self.heading.setText('Conversões concluídas');self.subtitle.setText('Abra a pasta de cada resultado abaixo ou adicione novos projetos.')
                self.review_summary.setText(f'{len(self.results)} resultado(s) disponíveis')
        self.refresh_count()

    def advance(self):
        if self.step==2 and not self.paths:
            self.choose_files()
            if self.paths:self.set_page(0)
            return
        if self.step<2:self.set_page(self.step+1)
        else:self.start_conversion()

    def running(self):
        return self._converting

    def refresh_count(self):
        count=len(self.paths);self.queue_label.setText(f'PROJETOS  ·  {count}')
        self.side_projects.setText(('\n\n'.join(p.name.replace('_','_\u200b').replace('.','.\u200b') for p in self.paths[:3])+ (f'\n\n+ {count-3} projetos' if count>3 else '')) if count else 'Seus projetos aparecerão aqui.')
        self.convert_btn.setText(('Converter '+str(count)+' projeto'+('s' if count!=1 else '')) if self.step==2 else 'Continuar  →')
        if self.step==2 and not count:self.convert_btn.setText('Adicionar projetos')
        ready=bool(count) and (self.xml_check.isChecked() or self.fc_check.isChecked() or self.step==0)
        if self.step==2 and not count:ready=True
        self.convert_btn.setEnabled(ready and not self.running());self.back_btn.setEnabled(self.step>0 and not self.running())
        self.remove_btn.setEnabled(bool(count) and not self.running())
        self.table.setVisible(self.step!=2 or bool(count))
        for i,(button,title,desc) in enumerate(self.steps):
            mark='✓' if i<self.step else str(i+1)
            extra=f'{count} projeto'+('s adicionados' if count!=1 else ' adicionado') if i==0 and count else desc
            button.setText(f'{mark}    {title}\n      {extra}');button.setStyleSheet('color:'+('#67a9ff' if i==self.step else '#d4deec' if i<self.step else '#8c9db4')+';')
            button.setProperty('active',i==self.step);button.setProperty('done',i<self.step);button.update()
            button.setEnabled(not self.running() and (i==0 or count>0 or i==2 and bool(self.results)))

    def xml_controls(self, enabled):
        self.profile.setEnabled(enabled)
        self.individual.setEnabled(enabled)

    def choose_files(self):
        paths,_ = QFileDialog.getOpenFileNames(self,'Adicionar projetos Premiere','','Adobe Premiere (*.prproj)')
        self.add_files(paths)

    def add_files(self, files):
        if self.running():
            return
        was_empty=not self.paths
        known = {str(p).casefold() for p in self.paths}
        for file in files:
            p = Path(file)
            if p.suffix.lower()!='.prproj' or str(p).casefold() in known:
                continue
            known.add(str(p).casefold())
            self.paths.append(p)
            row = self.table.rowCount()
            self.table.insertRow(row)
            for col, text in enumerate((p.name,'—','Na fila')):
                item = QTableWidgetItem(text)
                item.setToolTip(str(p))
                if col==1:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row,col,item)
        self.refresh_count()
        if was_empty and self.paths and self.step==2:self.set_page(0)

    def remove_selected(self):
        if self.running():
            return
        rows = sorted({i.row() for i in self.table.selectedIndexes()},reverse=True)
        for row in rows:
            self.paths.pop(row)
            self.table.removeRow(row)
        self.refresh_count()

    def queue_context_menu(self, position):
        item=self.table.itemAt(position)
        if item is None:return
        if item.row() not in {i.row() for i in self.table.selectedIndexes()}:
            self.table.selectRow(item.row())
        self.queue_menu=QMenu(self.table)
        action=self.queue_menu.addAction('Remover da fila');action.setEnabled(not self.running());action.triggered.connect(self.remove_selected)
        self.queue_menu.popup(self.table.viewport().mapToGlobal(position))

    def choose_output(self):
        initial=self.output.text() or (str(self.paths[0].parent) if self.paths else str(app_root()))
        path = QFileDialog.getExistingDirectory(self,'Pasta de saída',initial)
        if path:
            self.output.setText(path)
            self.source_output.setChecked(False)

    def toggle_remap(self, enabled):
        self.remap_widget.setVisible(enabled)

    def toggle_log(self, enabled):
        self.log.setVisible(enabled)

    def start_conversion(self):
        if self.running() or not self.paths:
            return
        if not self.source_output.isChecked() and not self.output.text().strip():
            QMessageBox.information(self,'Pasta de saída','Escolha uma pasta para salvar os resultados.')
            return
        self.set_page(2)
        remaps=[]
        if self.remap_btn.isChecked():
            old,new=self.old_folder.text().strip(),self.new_folder.text().strip()
            if bool(old)!=bool(new):
                QMessageBox.information(self,'Caminhos das mídias','Preencha a pasta antiga e a nova, ou deixe ambas vazias.')
                return
            if old and new:
                remaps=[(old,new)]
        options=Options(None if self.source_output.isChecked() else Path(self.output.text().strip()),xml=self.xml_check.isChecked(),fcproj=self.fc_check.isChecked(),
                        xml_profile=self.profile.currentData(),individual_xml=self.individual.isChecked(),
                        check_media=self.media_check.isChecked(),probe_media=self.probe_check.isChecked(),remaps=remaps)
        self.done_count=0; self.batch_results=[];self.batch_paths=list(self.paths);self.batch_count=len(self.batch_paths);self.log.clear()
        for row in range(len(self.paths)):
            self.table.item(row,2).setText('Na fila')
            self.table.item(row,2).setForeground(QColor('#a4b3c5'))
        self.progress.setRange(0,0)
        self.worker=ConversionWorker(self.batch_paths,options)
        self.worker.log_line.connect(self.on_log)
        self.worker.job_done.connect(self.on_done)
        self.worker.job_failed.connect(self.on_failed)
        self.worker.job_cancelled.connect(self.on_cancelled)
        self.worker.finished.connect(self.finished)
        for widget in (self.add_btn,self.remove_btn,self.settings,self.output,self.browse_btn,self.remap_btn,self.remap_widget,self.convert_btn,self.drop_zone,self.back_btn):
            widget.setEnabled(False)
        self.cancel_btn.show(); self.cancel_btn.setEnabled(True)
        self._converting=True
        self.worker.start()
        self.refresh_count()

    def pending_row(self,job):
        path=self.batch_paths[job]
        return self.paths.index(path) if path in self.paths else None

    def on_log(self,job,message):
        self.status.setText(message)
        row=self.pending_row(job)
        if row is not None:
            self.table.item(row,2).setText('Convertendo…')
            self.table.item(row,2).setForeground(QColor('#398cff'))
        self.log.appendPlainText(f'[{self.batch_paths[job].name}] {message}')

    def on_done(self,job,folder,report):
        source=self.batch_paths[job]
        self.results.append({'source':source,'folder':folder})
        self.batch_results.append(folder)
        self.done_count+=1
        label='Concluído' if not report['warnings'] else f"OK · {len(report['warnings'])} avisos"
        if report['missing_media']:
            label=f"OK · {len(report['missing_media'])} mídias ausentes"
        result_row=self.result_table.rowCount();self.result_table.insertRow(result_row)
        for col,text in enumerate((source.name,label)):
            item=QTableWidgetItem(text);item.setToolTip(folder);self.result_table.setItem(result_row,col,item)
        button=QPushButton('Abrir pasta',objectName='resultOpen');button.clicked.connect(lambda checked=False,f=folder:self.open_folder(f));self.result_table.setCellWidget(result_row,2,button)
        self.result_label.setText(f'RESULTADOS  ·  {len(self.results)}');self.result_label.show();self.result_table.show();self.open_btn.setEnabled(True)
        row=self.pending_row(job)
        if row is not None:
            self.paths.pop(row);self.table.removeRow(row)
        self.refresh_count()

    def on_failed(self,job,message):
        row=self.pending_row(job)
        if row is None:return
        self.table.item(row,2).setText('Falha')
        self.table.item(row,2).setForeground(QColor('#ffad9f'))
        self.table.item(row,2).setToolTip(message)
        self.log.appendPlainText(f'ERRO — {self.batch_paths[job].name}: {message}')
        self.log_btn.setChecked(True)

    def on_cancelled(self,job):
        row=self.pending_row(job)
        if row is not None:self.table.item(row,2).setText('Cancelado')
        self.log.appendPlainText('Conversão cancelada. Resultados já concluídos foram mantidos.')

    def finished(self):
        self._converting=False
        stopped=self.worker.stop.is_set()
        for widget in (self.add_btn,self.remove_btn,self.settings,self.output,self.browse_btn,self.remap_btn,self.remap_widget,self.drop_zone):
            widget.setEnabled(True)
        self.output.setEnabled(not self.source_output.isChecked())
        self.cancel_btn.hide()
        self.progress.setRange(0,100)
        self.progress.setValue(round(100*self.done_count/max(1,self.batch_count)))
        self.status.setText(f"{'Cancelado' if stopped else 'Finalizado'} · {self.done_count} de {self.batch_count} projetos concluídos. {len(self.paths)} na fila.")
        if not self.paths:
            self.heading.setText('Conversão concluída');self.subtitle.setText('Seus arquivos estão prontos. Abra a pasta de cada resultado abaixo.')
            self.review_summary.setText(f'{self.done_count} projeto(s) concluído(s) nesta conversão')
        self.refresh_count()

    def cancel(self):
        if self.running():
            self.worker.stop.set()
            self.cancel_btn.setEnabled(False)
            self.status.setText('Cancelando após a operação de mídia em andamento…')

    def open_folder(self,folder):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def open_result_row(self,item):
        self.open_folder(self.results[item.row()]['folder'])

    def open_output(self):
        if not self.results:return
        if len(self.results)==1:
            self.open_folder(self.results[0]['folder']);return
        self.result_menu=QMenu(self.open_btn)
        for result in self.results:
            action=self.result_menu.addAction(result['source'].name+' — '+result['folder'])
            action.triggered.connect(lambda checked=False,f=result['folder']:self.open_folder(f))
        self.result_menu.popup(self.open_btn.mapToGlobal(self.open_btn.rect().bottomLeft()))

    def dragEnterEvent(self,event):
        if not self.running() and event.mimeData().hasUrls() and any(u.toLocalFile().lower().endswith('.prproj') for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self,event):
        self.add_files([u.toLocalFile() for u in event.mimeData().urls()])
        event.acceptProposedAction()

    def closeEvent(self,event):
        if self.running():
            self.cancel()
            event.ignore()
            self.status.setText('Cancelando. Feche a janela quando a operação terminar.')
        else:
            event.accept()


def main(files=()):
    app=QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName('PremiereToAll')
    app.setOrganizationName('PremiereToAll')
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    app.setWindowIcon(make_icon())
    window=MainWindow(files)
    window.show()
    return app.exec()
