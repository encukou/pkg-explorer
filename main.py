import sys
from PySide2.QtCore import QAbstractItemModel, Qt, QModelIndex, QTimer
from PySide2.QtWidgets import QApplication, QWidget
from PySide2.QtUiTools import QUiLoader

import dnf

cachedir = '_dnf_cache'
releasever = '33'
the_arch = 'x86_64'

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
        return None

    def child(self, row, column):
        return None


class QuerySet(ModelItem):
    def __init__(self, *, model):
        super().__init__(model=model)
        self.queries = []

    @property
    def row_count(self):
        return len(self.queries)

    def child(self, row, column):
        return self.queries[row]

class Query(ModelItem):
    def __init__(self, text, *, parent):
        super().__init__(parent=parent)
        self.label = text

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
            return index.internalPointer().data(role)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole:
            return 'Name'

    def rowCount(self, parent):
        if not parent.isValid():
            return 1
        if self.checkIndex(parent, QAbstractItemModel.CheckIndexOption.IndexIsValid):
            return parent.internalPointer().row_count

    def columnCount(self, parent):
        if not parent.isValid():
            return 1
        if self.checkIndex(parent):
            return parent.internalPointer().col_count

    def index(self, row, column, parent):
        if not parent.isValid():
            return QModelIndex()
        if self.checkIndex(parent):
            p = parent.internalPointer()
            if p.row_count <= row or p.col_count <= column:
                return QModelIndex()
            return self.createIndex(row, column, p.child(row, column))

    def parent(self, index):
        if not index.isValid():
            return QModelIndex()
        if self.checkIndex(
                index,
                QAbstractItemModel.CheckIndexOption.DoNotUseParent
            ):
            parent = index.internalPointer().parent
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
    pkg_model.add_query(ri, 'scipy')

    wf.tvMainView.setModel(pkg_model.qt_model)
    wf.tvMainView.setRootIndex(ri)
    pkg_model.add_query(ri, 'nose')

    return window

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = get_main_window()
    window.show()
    sys.exit(app.exec_())
