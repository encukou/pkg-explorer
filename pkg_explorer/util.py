from PySide2.QtGui import QIcon, QFontMetrics, QColor, QBrush

_icons = {}
def get_icon(name):
    try:
        return _icons[name]
    except KeyError:
        icon = QIcon(f'icons-fontawesome/{name}.svg')
        _icons[name] = icon
        return icon
