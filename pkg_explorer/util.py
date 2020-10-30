from functools import lru_cache

from PySide2.QtCore import Qt
from PySide2.QtGui import QIcon, QPixmap, QPainter

@lru_cache
def get_icon(name, color=None):
    pixmap = QPixmap(f'icons-fontawesome/{name}.svg')
    if color != None:
        painter = QPainter(pixmap)
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(pixmap.rect(), color)
        painter.end()
    return QIcon(pixmap)
