import sys
import os
from functools import cached_property
from contextlib import contextmanager

from PySide2.QtCore import QAbstractItemModel, Qt, QModelIndex, QTimer, QSize
from PySide2.QtWidgets import QApplication, QWidget, QAction
from PySide2.QtUiTools import QUiLoader
from PySide2.QtGui import QIcon

import dnf

cachedir = '_dnf_cache'
releasever = 'rawhide'
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

    def __init__(self, name=None, arch=[the_arch, 'noarch'], *, parent, **kwargs):
        super().__init__(parent=parent)
        self.label = name

        q = self.model.base.sack.query()
        q = q.available()
        if name:
            q = q.filter(name=name)
        if kwargs:
            q = q.filter(**kwargs)
        if arch:
            q = q.filter(arch=arch)
        self._query = q

    @cached_property
    def packages(self):
        return [Package(p, parent=self) for p in self._query]

    @property
    def replacement(self):
        if len(self.packages) == 1:
            return self.packages[0]

    @property
    def row_count(self):
        return len(self.packages)

    def get_child(self, row, column):
        return self.packages[row]

class Subject(ModelItem):
    icon_name = 'list-alt'

    def __init__(self, text, *, parent):
        super().__init__(parent=parent)
        self.label = text
        self.subject = dnf.subject.Subject(text)

    @cached_property
    def packages(self):
        q = self.subject.get_best_query(self.model.base.sack)
        return [Package(p, parent=self) for p in q]

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
        self.label = f'{pkg.name}.{pkg.version}.{pkg.arch}'
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

    @cached_property
    def collapsed_reqs(self):
        collapsed = []
        rest = []
        collapsed_pkgs = set()
        for req in self.reqs:
            if len(req.pkgs) == 1:
                [pkg] = req.pkgs
                if pkg.pkg not in collapsed_pkgs:
                    collapsed.append(Package(pkg.pkg, parent=self))
                    collapsed_pkgs.add(pkg.pkg)
            else:
                rest.append(req)
        return collapsed + rest

    @property
    def row_count(self):
        count = 0
        if self.source:
            count += 1
        if self.model.collapse_reqs:
            count += len(self.collapsed_reqs)
        else:
            count += len(self.reqs)
        return count

    def get_child(self, row, column):
        if self.source:
            if row == 0:
                return self.source
            row -= 1
        if self.model.collapse_reqs:
            return self.collapsed_reqs[row]
        else:
            return self.reqs[row]


class Requirement(ModelItem):
    icon_name = 'puzzle-piece'

    def __init__(self, reldep, *, parent):
        super().__init__(parent=parent)
        self.label = str(reldep)
        self.reldep = reldep

    @cached_property
    def pkgs(self):
        result = []
        q = self.model.base.sack.query()
        q = q.available()
        q = q.filter(provides=self.reldep, arch=[the_arch, 'noarch'])
        return [Package(pkg, parent=self) for pkg in q]

    @property
    def row_count(self):
        return len(self.pkgs)

    def get_child(self, row, column):
        return self.pkgs[row]

class Recommendation(Requirement):
    icon_name = 'plus'



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

    def set_expand_reqs(self, value):
        self.qt_model.layoutAboutToBeChanged.emit()
        self.collapse_reqs = not value
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


class WidgetFinder:
    def __init__(self, obj, cls=QWidget):
        self.obj = obj
        self.cls = cls

    def __getattr__(self, name):
        widget = self.obj.findChild(self.cls, name)
        if widget is None:
            raise AttributeError(name)
        return widget

def _ignore(obj):
    pass

def keep_alive(obj):
    QTimer.singleShot(1, lambda: _ignore(obj))

def get_main():
    window = QUiLoader().load('main.ui')
    wf = WidgetFinder(window)

    pkg_model = PkgModel()
    ri = pkg_model.get_queryset_index()
    pkg_model.add_query(ri, 'scipy', name='python3-scipy')

    wf.tvMainView.setModel(pkg_model.qt_model)
    wf.tvMainView.setRootIndex(ri)
    pkg_model.add_query(ri, 'python3-nose', name='python3-nose')

    with open('want.txt') as f:
        for line in f:
            pkg_model.add_subject(ri, line.strip())

    act = WidgetFinder(window, QAction)
    act.actExpandReqs.setIcon(get_icon('puzzle-piece'))
    act.actExpandReqs.toggled.connect(pkg_model.set_expand_reqs)

    return window, pkg_model

if __name__ == '__main__':
    print(os.getpid())
    app = QApplication(sys.argv)
    window, model = get_main()
    window.show()
    with model:
        sys.exit(app.exec_())
