import sys
import os
from contextlib import contextmanager
import enum
from pathlib import Path

from PySide2.QtCore import QAbstractItemModel, Qt, QModelIndex, QTimer, QSize
from PySide2.QtCore import QPoint, QRect
from PySide2.QtWidgets import QApplication, QWidget, QAction, QStyle
from PySide2.QtWidgets import QStyledItemDelegate
from PySide2.QtUiTools import QUiLoader
from PySide2.QtGui import QFontMetrics

import dnf

from .modelitems import QuerySet, Query, Subject, Package
from .consts import cachedir, releasever, the_arch
from .util import get_icon


class Progress(dnf.callback.DownloadProgress):
    def start(self, total_files, total_size, total_drpms=0):
        print('starting...', total_files, total_size)
    def progress(self, payload, done):
        print('progress...', payload, done)
    def end(self, payload, status, msg):
        print('end...', payload, status, msg)


class CoroDriver:
    def __init__(self, coro):
        self.active = True
        self.coro = coro
        self.drive()

    def drive(self):
        if self.active:
            try:
                next(self.coro)
            except StopIteration:
                pass
            else:
                QTimer.singleShot(10, self.drive)
                return
        self.coro.close()
        self.active = False


def colorize(model):
    yield
    want, unwant, provide = model.querysets
    model.package_colors = {}

    def color(item, new_color):
        if item.underlying_object in model.package_colors:
            return
        yield model._colorize(item, new_color)
        if not isinstance(item, Package):
            for child in item.children:
                yield from color(child, new_color)

    yield from color(unwant, 'red')
    yield from color(provide, 'blue')


class PkgModel:
    def __init__(self):
        self.collapse_reqs = True

        self.qt_model = PkgQtModel(self)

        base = dnf.Base()
        conf = base.conf
        conf.cachedir = cachedir
        conf.substitutions['releasever'] = releasever
        conf.substitutions['basearch'] = the_arch
        base.repos.add_new_repo('rawhide', conf,
            baseurl=["http://download.fedoraproject.org/pub/fedora/linux/development/$releasever/Everything/$basearch/os/"])
        base.repos.add_new_repo('rawhide-source', conf,
            baseurl=["http://download.fedoraproject.org/pub/fedora/linux/development/$releasever/Everything/source/tree/"])
        print('Filling sack...')
        base.repos.all().set_progress_bar(Progress())
        base.fill_sack(load_system_repo=False)
        print('Done!')

        self.base = base

        self.querysets = []

        self.package_colors = {}

        self._color_driver = None
        self._recolor()

    def __enter__(self):
        pass

    def __exit__(self, *err):
        self.base.close()

    def get_queryset_index(self):
        qs = QuerySet(model=self)
        self.querysets.append(qs)
        return self.qt_model.createIndex(0, 0, qs)

    def add_query(self, queryset_index, text, **kwargs):
        with self._queryset_add_context(queryset_index, 1) as queryset:
            queryset.queries.append(Query(**kwargs, parent=queryset))

    def add_subject(self, queryset_index, text):
        with self._queryset_add_context(queryset_index, 1) as queryset:
            queryset.queries.append(Subject(text, parent=queryset))

    @contextmanager
    def _queryset_add_context(self, queryset_index, num_items):
        queryset = queryset_index.internalPointer()
        queries = queryset.queries
        self.qt_model.beginInsertRows(
            queryset_index, len(queries), len(queries) + num_items-1
        )
        yield queryset
        self.qt_model.endInsertRows()
        self._recolor()

    def set_expand_reqs(self, value):
        self.qt_model.layoutAboutToBeChanged.emit()
        self.collapse_reqs = not value
        self.qt_model.layoutChanged.emit()

    def _recolor(self):
        if self._color_driver:
            self._color_driver.active = False
        self._color_driver = CoroDriver(colorize(self))

    def _colorize(self, item, new_color):
        self.qt_model.layoutAboutToBeChanged.emit()
        self.package_colors[item.underlying_object] = new_color
        self.qt_model.layoutChanged.emit()


class PkgQtModel(QAbstractItemModel):
    def __init__(self, model):
        super().__init__()
        self._mod = model

    def flags(self, index):
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        #self.checkIndex(index)
        item = index.internalPointer().get_replacement()
        return item.data(role)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole:
            return 'Name'

    def rowCount(self, parent):
        if not parent.isValid():
            return 1
        #self.checkIndex(parent)
        item = parent.internalPointer().get_replacement()
        return item.row_count

    def columnCount(self, parent):
        if not parent.isValid():
            return 1
        #self.checkIndex(parent)
        item = parent.internalPointer().get_replacement()
        return item.col_count

    def index(self, row, column, parent):
        if not parent.isValid():
            return QModelIndex()
        #self.checkIndex(parent)
        item = parent.internalPointer().get_replacement()
        if item.row_count <= row or item.col_count <= column:
            return QModelIndex()
        child = item.get_child(row, column)
        return self.createIndex(row, column, child)

    def parent(self, index):
        if not index.isValid():
            return QModelIndex()
        #self.checkIndex(index, QAbstractItemModel.CheckIndexOption.DoNotUseParent)
        item = index.internalPointer()
        parent = item.parent
        while parent and parent.get_replacement() == item:
            parent = parent.parent
        if parent is None:
            return QModelIndex()
        return self.createIndex(0, 0, parent)


class ItemDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(20, QFontMetrics(option.font).lineSpacing())


class WidgetFinder:
    def __init__(self, obj, cls=QWidget):
        self.obj = obj
        self.cls = cls

    def __getattr__(self, name):
        widget = self.obj.findChild(self.cls, name)
        if widget is None:
            raise AttributeError(name)
        return widget


def get_main():
    window = QUiLoader().load(str(Path(__file__).parent / 'main.ui'))
    wf = WidgetFinder(window)

    pkg_model = PkgModel()
    ri = pkg_model.get_queryset_index()
    pkg_model.add_query(ri, 'scipy', name='python3-scipy')

    with open('want.txt') as f:
        for line in f:
            pkg_model.add_subject(ri, line.strip())

    wf.tvMainView.setModel(pkg_model.qt_model)
    wf.tvMainView.setRootIndex(ri)
    wf.tvMainView.setItemDelegate(ItemDelegate())
    pkg_model.add_query(ri, 'python3-nose', name='python3-nose')

    ri = pkg_model.get_queryset_index()
    with open('unwant.txt') as f:
        for line in f:
            pkg_model.add_subject(ri, line.strip())

    wf.tvUnwant.setModel(pkg_model.qt_model)
    wf.tvUnwant.setItemDelegate(ItemDelegate())
    wf.tvUnwant.setRootIndex(ri)

    ri = pkg_model.get_queryset_index()
    with open('provided.txt') as f:
        for line in f:
            pkg_model.add_subject(ri, line.strip())

    wf.tvProvided.setModel(pkg_model.qt_model)
    wf.tvProvided.setItemDelegate(ItemDelegate())
    wf.tvProvided.setRootIndex(ri)

    act = WidgetFinder(window, QAction)
    act.actExpandReqs.setIcon(get_icon('puzzle-piece'))
    act.actExpandReqs.toggled.connect(pkg_model.set_expand_reqs)

    return window, pkg_model

def main():
    print(os.getpid())
    app = QApplication(sys.argv)
    window, model = get_main()
    window.show()
    with model:
        sys.exit(app.exec_())
