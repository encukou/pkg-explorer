from functools import cached_property

from PySide2.QtCore import Qt
from PySide2.QtGui import QBrush, QColor

import dnf

from .consts import the_arch
from .util import get_icon

class ModelItem:
    label = '???'
    col_count = 1
    icon_name = None
    children = ()
    autoreplace = False

    def __init__(self, underlying_object, *, model=None, parent=None):
        if parent:
            self.parent = parent
            self.model = parent.model
        else:
            self.model = model
            self.parent = None
        self.underlying_object = underlying_object

    def data(self, role=Qt.DisplayRole):
        if role == Qt.DisplayRole:
            return self.label
        elif role == Qt.DecorationRole:
            if self.icon_name:
                return get_icon(self.icon_name)
        elif role == Qt.ForegroundRole:
            if color := self.model.package_colors.get(self.underlying_object):
                return QBrush(QColor(color))
        return None

    def get_replacement(self):
        replacement = self
        if parent := replacement.replacement:
            replacement = parent
        return replacement

    def get_child(self, row, column):
        return self.children[row]

    @property
    def replacement(self):
        if self.autoreplace and len(self.children) == 1:
            return self.children[0]

    @property
    def row_count(self):
        return len(self.children)


class QuerySet(ModelItem):
    def __init__(self, *, model):
        self.queries = []
        super().__init__(self, model=model)
        self.children = self.queries


class Query(ModelItem):
    icon_name = 'list-alt'
    autoreplace = True

    def __init__(self, name=None, arch=[the_arch, 'noarch'], *, parent, **kwargs):
        self.label = name

        q = parent.model.base.sack.query()
        q = q.available()
        if name:
            q = q.filter(name=name)
        if kwargs:
            q = q.filter(**kwargs)
        if arch:
            q = q.filter(arch=arch)
        self._query = q

        super().__init__(q, parent=parent)

    @cached_property
    def children(self):
        return [Package(p, parent=self) for p in self._query]


class Subject(ModelItem):
    icon_name = 'list-alt'
    autoreplace = True

    def __init__(self, text, *, parent):
        self.label = text
        self.subject = dnf.subject.Subject(text)
        super().__init__(self.subject, parent=parent)

    @cached_property
    def children(self):
        q = self.subject.get_best_query(self.model.base.sack)
        return [
            Package(p, parent=self)
            for p in q
            if p.arch in (the_arch, 'noarch')
        ]


class Package(ModelItem):
    icon_name = 'archive'

    def __init__(self, pkg, *, parent):
        self.pkg = pkg
        self.label = pkg.name
        if not self.pkg.source_name:
            self.icon_name = 'wrench'
        super().__init__(self.pkg, parent=parent)

    @cached_property
    def source(self):
        if self.pkg.source_name:
            return Query(self.pkg.source_name, arch='src', parent=self)

    @cached_property
    def reqs(self):
        reqs = sorted((Requirement(r, parent=self) for r in self.pkg.requires), key=lambda r: r.label)
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
        return sorted(collapsed, key=lambda r: r.label) + rest

    @property
    def children(self):
        result = []
        if self.source:
            result.append(self.source)
        if self.model.collapse_reqs:
            result.extend(self.collapsed_reqs)
        else:
            result.extend(self.reqs)
        return result


class Requirement(ModelItem):
    icon_name = 'puzzle-piece'

    def __init__(self, reldep, *, parent):
        super().__init__(reldep, parent=parent)
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
    def children(self):
        return self.pkgs


class Recommendation(Requirement):
    icon_name = 'plus'
