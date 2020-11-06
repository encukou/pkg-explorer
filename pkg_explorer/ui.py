import sys
import os
from contextlib import contextmanager
import enum
from pathlib import Path
from functools import partial

from PySide2.QtCore import QAbstractItemModel, Qt, QModelIndex, QTimer, QSize
from PySide2.QtCore import QPoint, QRect
from PySide2.QtWidgets import QApplication, QWidget, QAction, QStyle, QMenu
from PySide2.QtWidgets import QStyledItemDelegate, QInputDialog
from PySide2.QtUiTools import QUiLoader
from PySide2.QtGui import QFontMetrics, QCursor

import dnf

from .modelitems import Workload, ResolverInput, Labels, Label, Mods, Mod
from .modelitems import Workset, Subject
from .modelitems import AutoexpandRole, ColorRole
from .consts import cachedir, releasever, the_arch
from .coloring import colorize, Color
from .util import get_icon


class Progress(dnf.callback.DownloadProgress):
    def start(self, total_files, total_size, total_drpms=0):
        print('starting...', total_files, total_size)
    def progress(self, payload, done):
        print('progress...', payload, done)
    def end(self, payload, status, msg):
        print('end...', payload, status, msg)


class CoroDriver:
    def __init__(self, coro, model):
        self.active = True
        self.coro = coro
        self.model = model
        self.drive()

    def drive(self):
        if self.active:
            try:
                with self.model.changing_layout():
                    for i in range(50):
                        self.model._colorize(*next(self.coro))
            except StopIteration:
                pass
            else:
                QTimer.singleShot(1, self.drive)
                return
        self.coro.close()
        self.active = False


class PkgModel:
    def __init__(self, root_path):
        self.collapse_reqs = True
        self.collapse_provides = True

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

        self.obj_colors = {}

        self._color_driver = None

        self.labels = {}

        self.labels_root = Labels(model=self)
        self.sources_root = ResolverInput(root_path, model=self)
        self.mods_root = Mods(model=self)
        self.workset_root = Workset(model=self)
        self.roots = [
            self.sources_root,
            self.labels_root,
            self.mods_root,
            self.workset_root,
        ]

        self.active_indexes = {}

    def __enter__(self):
        pass

    def __exit__(self, *err):
        self.base.close()

    def get_main_index(self, idx):
        return self.qt_model.createIndex(self.roots.index(idx), 0, idx)

    def set_expand_reqs(self, value):
        with self.changing_layout():
            self.collapse_reqs = not value

    def set_expand_provides(self, value):
        with self.changing_layout():
            self.collapse_provides = not value

    def ensure_label(self, lbl):
        if lbl not in self.labels:
            self.labels[lbl] = None
            with self.changing_layout():
                self.labels[lbl] = Label(lbl, parent=self.labels_root)
                self._sorted_labels = [v for k, v in sorted(self.labels.items())]

    def _recolor(self):
        if self._color_driver:
            self._color_driver.active = False
        self._color_driver = CoroDriver(colorize(self), self)

    def _colorize(self, item, new_color):
        if item.underlying_object not in self.obj_colors:
            self.obj_colors[item.underlying_object] = new_color

    def set_active_index(self, index):
        item = index.internalPointer()
        self.active_indexes[type(item)] = item.underlying_object
        with self.changing_layout():
            self.obj_colors.clear()
        self._recolor()

    @contextmanager
    def changing_layout(self):
        self.qt_model.layoutAboutToBeChanged.emit()
        yield
        for index in self.qt_model.persistentIndexList():
            if replaced := self._replaced_index(index):
                self.qt_model.changePersistentIndex(index, replaced)
        self.qt_model.layoutChanged.emit()

    def _replaced_index(self, index):
        item = index.internalPointer()
        parent = index.parent()
        if not parent.isValid():
            # this is a root
            return
        p_item = parent.internalPointer()
        p_children = p_item.children
        if len(p_children) > index.row() and p_children[index.row()] == item:
            # Same position
            return
        for i, child in enumerate(p_children):
            if child == item:
                return self.qt_model.index(i, 0, parent)
        # no longer exists
        return QModelIndex()

    def set_color(self, index, color):
        item = index.internalPointer()
        key = item.key
        if key != None:
            mods = self.mods_root
            mod = mods.mods.get(key)
            with self.changing_layout():
                if mod:
                    mod.color = color
                else:
                    mods.mods[key] = Mod(key, color, parent=mods)
        self._recolor()

    def add_subject(self, text):
        with self.changing_layout():
            ws = self.workset_root
            item = Subject(text, parent=ws)
            ws.children.append(item)
            return self.qt_model.index(
                len(ws.children) - 1, 0, self.get_main_index(ws),
            )
        self._recolor()


class PkgQtModel(QAbstractItemModel):
    def __init__(self, model):
        super().__init__()
        self._mod = model

    def flags(self, index):
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    def data(self, index, role=Qt.DisplayRole):
        if role == Qt.SizeHintRole:
            return QSize(30, 30)
        if not index.isValid():
            return None
        item = index.internalPointer()
        return item.data(role)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole:
            return 'Name'

    def rowCount(self, parent):
        if not parent.isValid():
            return 1
        item = parent.internalPointer()
        return item.row_count

    def columnCount(self, parent):
        if not parent.isValid():
            return 1
        item = parent.internalPointer()
        return item.col_count

    def index(self, row, column, parent):
        if not parent.isValid():
            return QModelIndex()
        item = parent.internalPointer()
        if item.row_count <= row or item.col_count <= column:
            return QModelIndex()
        child = item.get_child(row, column)
        return self.createIndex(row, column, child)

    def hasChildren(self, parent):
        if not parent.isValid():
            return QModelIndex()
        item = parent.internalPointer()
        return bool(item.has_children)

    def parent(self, index):
        if not index.isValid():
            return QModelIndex()
        item = index.internalPointer()
        parent = item.parent
        if parent is None:
            return QModelIndex()
        return self.createIndex(0, 0, parent)


class ItemDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        size.setHeight(QFontMetrics(option.font).lineSpacing())
        return size

    def paint(self, painter, option, index):
        super().paint(painter, option, index)


class WidgetFinder:
    def __init__(self, obj, cls=QWidget):
        self.obj = obj
        self.cls = cls

    def __getattr__(self, name):
        widget = self.obj.findChild(self.cls, name)
        if widget is None:
            raise AttributeError(name)
        return widget


def setup_view(view, index):
    model = index.model()
    view.setModel(model)
    view.setRootIndex(index)
    view.setItemDelegate(ItemDelegate())

    def pressed(index):
        item = index.internalPointer()
        if QApplication.mouseButtons() & Qt.RightButton:
            if item.key is None:
                return
            menu = QMenu()
            actions = []
            for color in Color:
                print(color)
                act = QAction(get_icon('paintbrush', color.value), color.title, menu)
                act.setCheckable(True)
                if model.data(index, ColorRole) == color:
                    act.setChecked(True)
                menu.addAction(act)
                act.triggered.connect(partial(model._mod.set_color, index, color))
            act = QAction(get_icon('eraser'), 'No color', menu)
            act.setCheckable(True)
            if model.data(index, ColorRole) == None:
                act.setChecked(True)
            menu.addAction(act)
            act.triggered.connect(partial(model._mod.set_color, index, None))
            menu.exec_(QCursor.pos())

    view.pressed.connect(pressed)
    view.clicked.connect(print)

def setup_treeview(view, index):
    model = index.model()
    setup_view(view, index)

    def expand_more(index):
        if model.data(index, AutoexpandRole) and model.rowCount(index) == 1:
            view.expand(model.index(0, 0, index))

    view.expanded.connect(expand_more)

    view.header().resizeSection(0, 100);

def get_main():
    window = QUiLoader().load(str(Path(__file__).parent / 'main.ui'))
    wf = WidgetFinder(window)

    pkg_model = PkgModel(Path('content-resolver-input/configs'))
    setup_treeview(wf.tvMainView, pkg_model.get_main_index(pkg_model.workset_root))
    setup_treeview(wf.tvSources, pkg_model.get_main_index(pkg_model.sources_root))
    setup_treeview(wf.tvLabels, pkg_model.get_main_index(pkg_model.labels_root))
    setup_treeview(wf.tvMods, pkg_model.get_main_index(pkg_model.mods_root))

    def set_main_workload(index):
        item = index.internalPointer()
        if isinstance(item, Label):
            pkg_model.set_active_index(index)
    wf.tvSources.doubleClicked.connect(set_main_workload)

    wf.tvLabels.doubleClicked.connect(pkg_model.set_active_index)

    act = WidgetFinder(window, QAction)

    act.actExpandReqs.setIcon(get_icon('puzzle-piece'))
    act.actExpandReqs.toggled.connect(pkg_model.set_expand_reqs)

    act.actExpandProvides.setIcon(get_icon('hand-holding'))
    act.actExpandProvides.toggled.connect(pkg_model.set_expand_provides)

    def add_pkg():
        text, ok = QInputDialog.getText(
            window, 'Add Subject',
            'Add subject (package name):',
        )
        if ok:
            index = pkg_model.add_subject(text)
            wf.tvMainView.expand(index)

    act.actAddPkg.triggered.connect(add_pkg)

    pkg_model.add_subject('python3-dnf')
    pkg_model.add_subject('libselinux-python3')

    return window, pkg_model

def main():
    print('pid', os.getpid())
    app = QApplication(sys.argv)
    window, model = get_main()
    window.show()
    with model:
        sys.exit(app.exec_())
