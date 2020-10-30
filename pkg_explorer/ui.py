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

from .modelitems import Workload, ResolverInput, Labels, Label, AutoexpandRole
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


class PkgModel:
    def __init__(self, root_path):
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

        self.package_colors = {}

        self._color_driver = None
        self._recolor()

        self.labels = {}

        self.labels_root = Labels(model=self)
        self.sources_root = ResolverInput(root_path, model=self)
        self.roots = [
            self.sources_root,
            self.labels_root,
        ]

    def __enter__(self):
        pass

    def __exit__(self, *err):
        self.base.close()

    def get_main_index(self, idx):
        return self.qt_model.createIndex(self.roots.index(idx), 0, idx)

    def set_expand_reqs(self, value):
        self.qt_model.layoutAboutToBeChanged.emit()
        self.collapse_reqs = not value
        self.qt_model.layoutChanged.emit()

    def ensure_label(self, lbl):
        if lbl not in self.labels:
            self.labels[lbl] = None
            self.qt_model.layoutAboutToBeChanged.emit()
            self.labels[lbl] = Label(lbl, parent=self.labels_root)
            self._sorted_labels = [v for k, v in sorted(self.labels.items())]
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
        item = index.internalPointer()
        return item.data(role)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole:
            return 'Name'

    def rowCount(self, parent):
        if not parent.isValid():
            return 1
        #self.checkIndex(parent)
        item = parent.internalPointer()
        return item.row_count

    def columnCount(self, parent):
        if not parent.isValid():
            return 1
        #self.checkIndex(parent)
        item = parent.internalPointer()
        return item.col_count

    def index(self, row, column, parent):
        if not parent.isValid():
            return QModelIndex()
        #self.checkIndex(parent)
        item = parent.internalPointer()
        if item.row_count <= row or item.col_count <= column:
            return QModelIndex()
        child = item.get_child(row, column)
        return self.createIndex(row, column, child)

    def hasChildren(self, parent):
        if not parent.isValid():
            return QModelIndex()
        #self.checkIndex(parent)
        item = parent.internalPointer()
        return bool(item.has_children)

    def parent(self, index):
        if not index.isValid():
            return QModelIndex()
        #self.checkIndex(index, QAbstractItemModel.CheckIndexOption.DoNotUseParent)
        item = index.internalPointer()
        parent = item.parent
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


def setup_treeview(view, index):
    view.setModel(index.model())
    view.setRootIndex(index)
    view.setItemDelegate(ItemDelegate())

    def expand_more(index):
        model = index.model()
        if model.data(index, AutoexpandRole) and model.rowCount(index) == 1:
            view.expand(model.index(0, 0, index))

    view.expanded.connect(expand_more)

def get_main():
    window = QUiLoader().load(str(Path(__file__).parent / 'main.ui'))
    wf = WidgetFinder(window)

    pkg_model = PkgModel(Path('content-resolver-input/configs'))
    setup_treeview(wf.tvMainView, pkg_model.get_main_index(pkg_model.sources_root))

    setup_treeview(wf.tvSources, pkg_model.get_main_index(pkg_model.sources_root))
    setup_treeview(wf.tvLabels, pkg_model.get_main_index(pkg_model.labels_root))

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
