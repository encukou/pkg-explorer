from functools import lru_cache

from PySide2.QtGui import QIcon, QImage, QColor, QBitmap, QPixmap

@lru_cache
def get_icon(name):
    return QIcon(f'icons-fontawesome/{name}.svg')
