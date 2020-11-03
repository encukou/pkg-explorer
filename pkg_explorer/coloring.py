import enum

from PySide2.QtCore import Qt

from .modelitems import Label, Workload, UnwantedSubject, Subject, Package

class Color(enum.Enum):
    RED = Qt.red
    GREEN = Qt.darkGreen
    DARK_BLUE = Qt.darkBlue
    BLUE = Qt.blue
    GRAY = Qt.gray

    @property
    def title(self):
        return self.name.capitalize().replace('_', ' ')


def is_active(model, item, cls):
    return isinstance(item, cls) and item.underlying_object == model.active_indexes.get(cls)

def colorize(model):
    for item in model.labels_root.children:
        if is_active(model, item, Label):
            yield item, Color.BLUE
        else:
            yield item, Color.GRAY
    active_workload = None
    for item in sorted(
            model.sources_root.children,
            key=lambda wl: (
                wl.color != Color.BLUE,
                not getattr(wl, 'unwanted_packages', None),
                len(wl.children) // 100,
            ),
        ):
        if is_active(model, item, Workload) or item.color == Color.BLUE:
            yield from colorize_workload(item, Color.BLUE)
        else:
            yield from colorize_workload(item)


def colorize_workload(wl, color=None):
    model = wl.model
    for item in wl.labels:
        if is_active(model, item, Label):
            break
    else:
        yield wl, Color.GRAY
        return
    yield wl, color or Color.DARK_BLUE
    for item in wl.children:
        if isinstance(item, UnwantedSubject):
            yield item, Color.RED
        if isinstance(item, Subject):
            if isinstance(item, UnwantedSubject):
                color = Color.RED
            else:
                color = color or Color.GREEN
            yield from colorize_subject(item, color)

def colorize_subject(subj, color):
    yield subj, color
    for item in subj.children:
        yield item, color
        if isinstance(item, Package) and color == Color.GREEN:
            for src in item.sources:
                yield src, item.color or color
