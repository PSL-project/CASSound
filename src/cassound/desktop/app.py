import sys
from PySide6.QtWidgets import QApplication
from .theme import STUDIO
from .window import MainWindow


def run():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    from PySide6.QtGui import QIcon
    from importlib.resources import files

    app.setWindowIcon(QIcon(str(files("cassound") / "resources/cassound.ico")))
    app.setApplicationName("CASSound")
    app.setOrganizationName("CASSound")
    app.setStyle("Fusion")
    app.setStyleSheet(STUDIO)
    window = MainWindow()
    window.show()
    return app.exec()
