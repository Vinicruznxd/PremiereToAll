"""Isolated offscreen GUI test; never sends input to the user's desktop."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import time
from pathlib import Path
from PySide6.QtWidgets import QApplication
from premieretoall.gui import MainWindow
from PySide6.QtCore import QMimeData,QUrl,Qt,QPoint,QPointF
from PySide6.QtGui import QDragEnterEvent,QDropEvent
from PySide6.QtTest import QTest


def test_queue_worker_reports_invalid_file_and_continues(tmp_path):
    app=QApplication.instance() or QApplication([])
    bad=tmp_path/'corrompido.prproj';bad.write_text('bad xml')
    empty=tmp_path/'vazio.prproj';empty.write_text('<PremiereData/>')
    window=MainWindow([bad,empty])
    window.output.setText(str(tmp_path/'resultados'))
    window.source_output.setChecked(False)
    window.media_check.setChecked(False)
    window.probe_check.setChecked(False)
    assert window.convert_btn.isEnabled()
    window.start_conversion()
    deadline=time.monotonic()+20
    while window.running() and time.monotonic()<deadline:
        app.processEvents()
        time.sleep(.01)
    for _ in range(10):
        app.processEvents()
    assert not window.running()
    assert window.table.item(0,2).text()=='Falha'
    assert window.paths==[bad] and window.table.rowCount()==1
    assert window.result_table.item(0,1).text()=='Concluído'
    assert window.convert_btn.isEnabled()
    assert len(window.results)==1
    assert (Path(window.results[0]['folder'])/'vazio.fcproj').exists()
    assert (Path(window.results[0]['folder'])/'vazio.xml').exists()
    assert window.progress.value()==50
    window.close()


def test_drop_zone_wizard_formats_and_back_preserve_queue(tmp_path):
    app=QApplication.instance() or QApplication([])
    a=tmp_path/'a.prproj';a.write_text('<PremiereData/>')
    b=tmp_path/'b.PRPROJ';b.write_text('<PremiereData/>')
    w=MainWindow();w.show();app.processEvents()
    assert not w.convert_btn.isEnabled()
    mime=QMimeData();mime.setUrls([QUrl.fromLocalFile(str(p)) for p in [a,b,a,tmp_path/'bad.txt']])
    enter=QDragEnterEvent(QPoint(30,30),Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(w.drop_zone,enter);assert enter.isAccepted()
    event=QDropEvent(QPointF(30,30),Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(w.drop_zone,event);assert len(w.paths)==2
    QTest.mouseClick(w.convert_btn,Qt.MouseButton.LeftButton);assert w.step==1
    w.xml_check.setChecked(False);w.fc_check.setChecked(False);assert not w.convert_btn.isEnabled()
    w.fc_check.setChecked(True);QTest.mouseClick(w.convert_btn,Qt.MouseButton.LeftButton);assert w.step==2
    QTest.mouseClick(w.back_btn,Qt.MouseButton.LeftButton);assert w.step==1
    assert len(w.paths)==2 and not w.xml_check.isChecked()
    w.close()


def wait_for_conversion(w):
    app=QApplication.instance()
    deadline=time.monotonic()+30
    while w.running() and time.monotonic()<deadline:
        app.processEvents();time.sleep(.01)
    app.processEvents()
    assert not w.running()


def test_twenty_projects_save_beside_their_own_sources_and_leave_queue(tmp_path):
    app=QApplication.instance() or QApplication([])
    paths=[]
    for i in range(20):
        folder=tmp_path/str(i);folder.mkdir();p=folder/'mesmo_nome.prproj';p.write_text('<PremiereData/>');paths.append(p)
    w=MainWindow(paths);w.media_check.setChecked(False);w.probe_check.setChecked(False)
    assert w.source_output.isChecked()
    w.start_conversion();wait_for_conversion(w)
    assert w.paths==[] and w.table.rowCount()==0 and w.result_table.rowCount()==20
    assert len(w.results)==20 and w.progress.value()==100
    for result in w.results:
        assert Path(result['folder']).parent==result['source'].parent
        assert (Path(result['folder'])/'mesmo_nome.fcproj').exists()
        assert result['source'].read_text()=='<PremiereData/>'
    old_folders=[r['folder'] for r in w.results]
    w.start_conversion();app.processEvents()
    assert [r['folder'] for r in w.results]==old_folders and not w.running()
    w.add_files([paths[0]]);assert w.paths==[paths[0]]
    w.start_conversion();wait_for_conversion(w)
    assert not w.paths and len(w.results)==21
    assert w.results[-1]['folder']!=old_folders[0]
    w.close()


def test_right_click_removes_clicked_project_without_deleting_file(tmp_path):
    app=QApplication.instance() or QApplication([])
    paths=[]
    for name in ('first','second','third'):
        p=tmp_path/(name+'.prproj');p.write_text('<PremiereData/>');paths.append(p)
    w=MainWindow(paths);w.show();app.processEvents();w.table.selectRow(0)
    position=w.table.visualItemRect(w.table.item(1,0)).center()
    w.table.customContextMenuRequested.emit(position);app.processEvents()
    assert w.queue_menu.actions()[0].text()=='Remover da fila'
    w.queue_menu.actions()[0].trigger();w.queue_menu.close();app.processEvents()
    assert w.paths==[paths[0],paths[2]] and w.table.rowCount()==2
    assert all(p.exists() for p in paths)
    w.close()


def test_manual_destination_overrides_each_source_folder(tmp_path):
    app=QApplication.instance() or QApplication([])
    source=tmp_path/'source';source.mkdir();p=source/'test.prproj';p.write_text('<PremiereData/>')
    w=MainWindow([p]);w.source_output.setChecked(False);w.output.setText(str(tmp_path/'chosen'))
    w.media_check.setChecked(False);w.probe_check.setChecked(False)
    w.start_conversion();wait_for_conversion(w)
    assert Path(w.results[0]['folder']).parent==tmp_path/'chosen'
    assert not (source/'test').exists()
    w.close()
