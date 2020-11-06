from functools import cached_property
import json
import traceback
from pathlib import Path

from PySide2.QtCore import Qt
from PySide2.QtGui import QBrush, QColor

import dnf
import yaml

from .consts import the_arch, yaml_cacheir
from .util import get_icon

AutoexpandRole = Qt.UserRole + 1
ColorRole = Qt.UserRole + 2

def read_yaml(path):
    stat = path.stat()
    key = [stat.st_mtime, stat.st_size]
    cache_file = Path(yaml_cacheir) / path.with_suffix('.jsonlines').name
    if cache_file.exists():
        with cache_file.open() as f:
            key2 = json.loads(f.readline())
            if key == key2:
                return json.load(f)
    if stat.st_size > 1024 * 100:
        return {'$icon': 'weight-hanging'}
    print('Reading', path)
    with path.open() as f:
        try:
            data = yaml.safe_load(f)
        except Exception as e:
            print(e)
            return {'$icon': 'bug'}
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    with cache_file.open('w') as f:
        json.dump(key, f)
        print(file=f)
        json.dump(data, f)
    return data

class ModelItem:
    label = '???'
    col_count = 1
    icon_name = None
    children = ()
    autoexpand = False
    key = None

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
        if role == Qt.ToolTipRole:
            return self.extended_label
        if role == Qt.StatusTipRole:
            return self.extended_label
        elif role == Qt.DecorationRole:
            if self.icon_name:
                if self.color:
                    return get_icon(self.icon_name, self.color.value)
                else:
                    return get_icon(self.icon_name)
        elif role == Qt.ForegroundRole:
            if color := self.color:
                return QBrush(color.value)
        elif role == ColorRole:
            return self.color
        elif role == AutoexpandRole:
            return self.autoexpand
        return None

    @property
    def color(self):
        if mod := self.model.mods_root.mods.get(self.key):
            return mod.color
        if color := self.model.obj_colors.get(self.underlying_object):
            return color

    def get_child(self, row, column):
        return self.children[row]

    @property
    def row_count(self):
        return len(self.children)

    @property
    def extended_label(self):
        return self.label

    has_children = row_count


class ResolverInput(ModelItem):
    def __init__(self, root_path, /, *, model):
        super().__init__(self, model=model)
        self.children = []
        for path in sorted(root_path.glob('*.yaml'), key=self.path_sort_key):
            self.children.append(Workload(path, parent=self))

    def path_sort_key(self, path):
        return 'python' not in path.name, path.name

class Labels(ModelItem):
    def __init__(self, *, model):
        super().__init__(self, model=model)

    @property
    def children(self):
        return self.model._sorted_labels


class Workset(ModelItem):
    label = 'Workset'

    def __init__(self, *, model):
        super().__init__(self, model=model)
        self.children = []


class Workload(ModelItem):
    def __init__(self, path, *, parent):
        super().__init__(self, parent=parent)
        self.key = ('workload', path.name)
        self.path = path
        list(self.labels)

    @cached_property
    def yaml_data(self):
        return read_yaml(self.path)

    @cached_property
    def yaml_data_data(self):
        return self.yaml_data.get('data', {})

    @cached_property
    def label(self):
        return self.yaml_data_data.get('name', self.path.name)

    @cached_property
    def icon_name(self):
        if icon := self.yaml_data.get('$icon'):
            return icon
        document = self.yaml_data.get('document')
        if document == 'feedback-pipeline-workload':
            return 'toolbox'
        elif document == 'feedback-pipeline-unwanted':
            return 'angry'
        else:
            return 'question'

    @cached_property
    def packages(self):
        return [
            Subject(pkg, parent=self)
            for pkg in (
                self.yaml_data_data.get('packages', [])
                + self.yaml_data_data.get('arch_packages', {}).get(the_arch, [])
                + list(self.yaml_data_data.get('package_placeholders', ()))
            )
        ]

    @cached_property
    def unwanted_packages(self):
        return [
            UnwantedSubject(pkg, parent=self)
            for pkg in (
                self.yaml_data_data.get('unwanted_packages', [])
                + self.yaml_data_data.get('unwanted_arch_packages', {}).get(the_arch, [])
            )
        ] + [
            UnwantedSubject(pkg, arches=['src'], parent=self)
            for pkg in (self.yaml_data_data.get('unwanted_source_packages', ()))
        ]

    @cached_property
    def labels(self):
        return [
            Label(lbl, parent=self)
            for lbl in self.yaml_data_data.get('labels', ())
        ]

    @property
    def children(self):
        return self.labels + self.packages + self.unwanted_packages


class Label(ModelItem):
    icon_name = 'tag'

    def __init__(self, lbl, *, parent):
        super().__init__(('label', lbl), parent=parent)
        self.label = lbl
        self.model.ensure_label(lbl)

    @property
    def key(self):
        return 'label', self.label


class Package(ModelItem):
    icon_name = 'box-open'
    src_icon_name = 'wrench'

    def __init__(self, pkg, icon_name=None, label_prefix='', *, parent):
        self.pkg = pkg
        self.label = label_prefix + pkg.name
        if icon_name:
            self.icon_name = icon_name
        elif not self.pkg.source_name:
            self.icon_name = self.src_icon_name
        super().__init__(self.pkg, parent=parent)

    @property
    def extended_label(self):
        pkg = self.pkg
        if pkg.epoch:
            epoch_part = f'{pkg.epoch}!'
        else:
            epoch_part = ''
        return f'{pkg.name}–{epoch_part}{pkg.version}–{pkg.release}.{pkg.arch}'

    @cached_property
    def sources(self):
        if self.pkg.source_name:
            q = self.model.base.sack.query().filter(name=self.pkg.source_name, arch='src')
            return [type(self)(pkg, parent=self) for pkg in q]
        return []

    @cached_property
    def reqs(self):
        reqs = sorted((Requirement(r, parent=self) for r in self.pkg.requires), key=lambda r: r.label)
        reqs += [WeakReq(r, parent=self) for r in self.pkg.recommends]
        reqs += [WeakReq(r, parent=self) for r in self.pkg.suggests]
        return reqs

    @cached_property
    def provides(self):
        if self.pkg.arch == 'src':
            result = []
            for reldep in self.pkg.provides:
                q = self.model.base.sack.query().filter(provides=reldep, arch=(the_arch, 'noarch'))
                result.extend(q)
            return sorted((
                Package(r, icon_name='archive', label_prefix='→ ', parent=self)
                for r in result
            ), key=lambda r: r.label)
        else:
            return sorted((Provide(r, parent=self) for r in self.pkg.provides), key=lambda r: r.label)

    @cached_property
    def collapsed_reqs(self):
        collapsed = []
        rest = []
        collapsed_pkgs = set()
        for req in self.reqs:
            if len(req.pkgs) == 1 and req.strong:
                [pkg] = req.pkgs
                if pkg.pkg not in collapsed_pkgs:
                    collapsed.append(type(self)(pkg.pkg, parent=self))
                    collapsed_pkgs.add(pkg.pkg)
            else:
                rest.append(req)
        return sorted(collapsed, key=lambda r: r.label) + rest

    @cached_property
    def collapsed_provides(self):
        if self.pkg.arch == 'src':
            return self.provides
        if self.provides:
            pkgs = sorted(set(
                p.pkg
                for prov in self.provides
                for p in prov.pkgs
                if p.pkg != self.pkg
            ), key=lambda p: p.name)
            if pkgs:
                return [CollapsedProvides(pkgs, parent=self)]
        return []

    @property
    def has_children(self):
        return self.sources or self.reqs or self.provides

    @property
    def children(self):
        result = []
        if self.sources:
            result.extend(self.sources)
        if self.model.collapse_reqs:
            result.extend(self.collapsed_reqs)
        else:
            result.extend(self.reqs)
        if self.model.collapse_provides:
            result.extend(self.collapsed_provides)
        else:
            result.extend(self.provides)
        return result
    @property
    def key(self):
        return 'label', self.pkg.name


class CollapsedProvides(ModelItem):
    icon_name = 'hand-holding-medical'
    label = 'Dependent packages'

    def __init__(self, pkgs, parent):
        super().__init__(self, parent=parent)
        self.pkgs = pkgs
        self.label = f'Dependent packages ({len(pkgs)})'

    @cached_property
    def children(self):
        return [Package(p, parent=self) for p in self.pkgs]

class Subject(ModelItem):
    icon_name = 'list-alt'
    autoexpand = True
    _pkg_class = Package

    def __init__(self, text, arches=(the_arch, 'noarch'), *, parent):
        self.label = text
        self.subject = dnf.subject.Subject(text)
        self.key = 'subj', text
        self.arches = arches
        super().__init__(self.subject, parent=parent)

    @cached_property
    def children(self):
        q = self.subject.get_best_query(self.model.base.sack)
        return [
            self._pkg_class(p, parent=self)
            for p in q
            if p.arch in self.arches
        ]


class UnwantedSubject(Subject):
    icon_name = 'window-close'


class Requirement(ModelItem):
    icon_name = 'puzzle-piece'
    autoexpand = True
    key_category = 'req'
    strong = True

    def __init__(self, reldep, *, parent):
        super().__init__(reldep, parent=parent)
        self.label = str(reldep)
        self.key = self.key_category, str(reldep)
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


class WeakReq(Requirement):
    icon_name = 'plus'
    key_category = 'weak'
    strong = False

class Provide(Requirement):
    icon_name = 'hand-holding'
    key_category = 'prov'

    @cached_property
    def pkgs(self):
        result = []
        q = self.model.base.sack.query()
        q = q.available()
        q = q.filter(requires=self.reldep, arch=[the_arch, 'noarch', 'src'])
        return [Package(pkg, parent=self) for pkg in q]


class Mods(ModelItem):
    label = 'Modifications'

    def __init__(self, *, model):
        super().__init__(('mods',),model=model)
        self.mods = {}

    @property
    def children(self):
        return list(self.mods.values())

class Mod(ModelItem):
    color = None
    icon_name = 'paintbrush'

    def __init__(self, key, color, *, parent):
        super().__init__(('mod', key), parent=parent)
        self.label = ':'.join(key)
        self.key = key
        self.color = color
