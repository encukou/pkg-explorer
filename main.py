import sys
from functools import cached_property

from PySide2.QtCore import QAbstractItemModel, Qt, QModelIndex, QTimer, QSize
from PySide2.QtWidgets import QApplication, QWidget
from PySide2.QtUiTools import QUiLoader
from PySide2.QtGui import QIcon

import dnf

cachedir = '_dnf_cache'
releasever = '33'
the_arch = 'x86_64'

_icons = {}
def get_icon(name):
    try:
        return _icons[name]
    except KeyError:
        icon = QIcon(f'icons-fontawesome/{name}.svg')
        _icons[name] = icon
        return icon


class Progress(dnf.callback.DownloadProgress):
    def start(self, total_files, total_size, total_drpms=0):
        print('starting...', total_files, total_size)
    def progress(self, payload, done):
        print('progress...', payload, done)
    def end(self, payload, status, msg):
        print('end...', payload, status, msg)


class ModelItem:
    label = '???'
    row_count = 0
    col_count = 1
    icon_name = None
    replacement = None

    def __init__(self, *, model=None, parent=None):
        if parent:
            self.parent = parent
            self.model = parent.model
        else:
            self.model = model
            self.parent = None

    def data(self, role=Qt.DisplayRole):
        if role == Qt.DisplayRole:
            return self.label
        elif role == Qt.DecorationRole:
            if self.icon_name:
                return get_icon(self.icon_name)
        elif role == Qt.SizeHintRole:
            return QSize(18, 18)
        return None

    def get_replacement(self):
        replacement = self
        if parent := replacement.replacement:
            replacement = parent
        return replacement

    def get_child(self, row, column):
        return None


class QuerySet(ModelItem):
    def __init__(self, *, model):
        super().__init__(model=model)
        self.queries = []

    @property
    def row_count(self):
        return len(self.queries)

    def get_child(self, row, column):
        return self.queries[row]

class Query(ModelItem):
    icon_name = 'list-alt'
    _packages = None

    def __init__(self, name, arch=None, *, parent):
        super().__init__(parent=parent)
        self.label = name

        q = self.model.base.sack.query()
        q = q.available()
        q = q.filter(name=name)
        if arch:
            q = q.filter(arch=arch)
        self._query = q

    @cached_property
    def packages(self):
        if self._packages == None:
            self._packages = [Package(p, parent=self) for p in self._query]
        return self._packages

    @property
    def replacement(self):
        if len(self.packages) == 1:
            return self.packages[0]

    @property
    def row_count(self):
        return len(self.packages)

    def get_child(self, row, column):
        return self.packages[row]


class Package(ModelItem):
    icon_name = 'archive'

    def __init__(self, pkg, *, parent):
        super().__init__(parent=parent)
        self.pkg = pkg
        self.label = pkg.name
        if not self.pkg.source_name:
            self.icon_name = 'wrench'

    @cached_property
    def source(self):
        if self.pkg.source_name:
            return Query(self.pkg.source_name, arch='src', parent=self)

    @cached_property
    def reqs(self):
        reqs = [Requirement(r, parent=self) for r in self.pkg.requires]
        reqs += [Recommendation(r, parent=self) for r in self.pkg.recommends]
        reqs += [Recommendation(r, parent=self) for r in self.pkg.suggests]
        return reqs

    @property
    def row_count(self):
        count = len(self.reqs)
        if self.source:
            count += 1
        return count

    def get_child(self, row, column):
        if self.source:
            if row == 0:
                return self.source
            row -= 1
        return self.reqs[row]


class Requirement(ModelItem):
    icon_name = 'puzzle-piece'

    def __init__(self, reldep, *, parent):
        super().__init__(parent=parent)
        self.label = str(reldep)

class Recommendation(Requirement):
    icon_name = 'plus'



class PkgModel:
    def __init__(self):
        self.qt_model = PkgQtModel(self)

        base = dnf.Base()
        conf = base.conf
        conf.cachedir = cachedir
        conf.substitutions['releasever'] = releasever
        conf.substitutions['basearch'] = the_arch
        base.repos.add_new_repo('rawhide', conf,
            baseurl=["http://download.fedoraproject.org/pub/fedora/linux/development/rawhide//Everything/$basearch/os/"])
        base.repos.add_new_repo('rawhide-source', conf,
            baseurl=["http://download.fedoraproject.org/pub/fedora/linux/development/rawhide/Everything/source/tree/"])
        print('Filling sack...')
        base.repos.all().set_progress_bar(Progress())
        base.fill_sack(load_system_repo=False)
        print('Done!')

        self.base = base

        self.querysets = []

    def get_queryset_index(self):
        qs = QuerySet(model=self)
        self.querysets.append(qs)
        return self.qt_model.createIndex(0, 0, qs)

    def add_query(self, queryset_index, text):
        if self.qt_model.checkIndex(queryset_index):
            queryset = queryset_index.internalPointer()
            queries = queryset.queries
            self.qt_model.beginInsertRows(queryset_index, len(queries), len(queries))
            queryset.queries.append(Query(text, parent=queryset))
            self.qt_model.endInsertRows()

class PkgQtModel(QAbstractItemModel):
    def __init__(self, model):
        super().__init__()
        self._mod = model

    def flags(self, index):
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        if self.checkIndex(index, QAbstractItemModel.CheckIndexOption.IndexIsValid):
            item = index.internalPointer().get_replacement()
            return item.data(role)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole:
            return 'Name'

    def rowCount(self, parent):
        if not parent.isValid():
            return 1
        if self.checkIndex(parent, QAbstractItemModel.CheckIndexOption.IndexIsValid):
            item = parent.internalPointer().get_replacement()
            return item.row_count

    def columnCount(self, parent):
        if not parent.isValid():
            return 1
        if self.checkIndex(parent):
            item = parent.internalPointer().get_replacement()
            return item.col_count

    def index(self, row, column, parent):
        if not parent.isValid():
            return QModelIndex()
        if self.checkIndex(parent):
            item = parent.internalPointer().get_replacement()
            if item.row_count <= row or item.col_count <= column:
                return QModelIndex()
            child = item.get_child(row, column)
            return self.createIndex(row, column, child)

    def parent(self, index):
        if not index.isValid():
            return QModelIndex()
        if self.checkIndex(
                index,
                QAbstractItemModel.CheckIndexOption.DoNotUseParent
            ):
            item = index.internalPointer().get_replacement()
            parent = item.parent
            while parent and parent.get_replacement() == item:
                parent = parent.parent
            if parent is None:
                return QModelIndex()
            return self.createIndex(0, 0, parent)
        return QModelIndex()


class WidgetFinder:
    def __init__(self, obj):
        self.obj = obj

    def __getattr__(self, name):
        widget = self.obj.findChild(QWidget, name)
        if widget is None:
            raise AttributeError(name)
        return widget

def _ignore(obj):
    pass

def keep_alive(obj):
    QTimer.singleShot(1, lambda: _ignore(obj))

def get_main_window():
    window = QUiLoader().load('main.ui')
    wf = WidgetFinder(window)

    pkg_model = PkgModel()
    ri = pkg_model.get_queryset_index()
    pkg_model.add_query(ri, 'python3-scipy')

    wf.tvMainView.setModel(pkg_model.qt_model)
    wf.tvMainView.setRootIndex(ri)
    pkg_model.add_query(ri, 'python3-nose')

    return window

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = get_main_window()
    window.show()
    sys.exit(app.exec_())
