from PySide2.QtCore import Qt

from .modelitems import Label, Workload, UnwantedSubject, Subject, Package

def is_active(model, item, cls):
    return isinstance(item, cls) and item.underlying_object == model.active_indexes.get(cls)

def colorize(model):
    with model.changing_layout():
        for item in model.labels_root.children:
            if is_active(model, item, Label):
                model._colorize(item, Qt.blue)
            else:
                model._colorize(item, Qt.gray)
    yield
    active_workload = None
    for item in model.sources_root.children:
        if is_active(model, item, Workload):
            active_workload = item
            colorize_workload(item, Qt.blue)
        else:
            with model.changing_layout():
                colorize_workload(item)
            yield


def colorize_workload(wl, color=None):
    model = wl.model
    for item in wl.children:
        if is_active(model, item, Label):
            break
    else:
        model._colorize(wl, Qt.gray)
        return
    model._colorize(wl, color or Qt.darkBlue)
    for item in wl.children:
        if isinstance(item, UnwantedSubject):
            model._colorize(item, Qt.red)
        if isinstance(item, Subject):
            if isinstance(item, UnwantedSubject):
                color = Qt.red
            else:
                color = color or Qt.darkGreen
            colorize_subject(item, color)

def colorize_subject(subj, color):
    model = subj.model
    model._colorize(subj, color)
    for item in subj.children:
        model._colorize(item, color)
        if isinstance(item, Package) and color == Qt.darkGreen:
            for src in item.sources:
                model._colorize(src, color)
