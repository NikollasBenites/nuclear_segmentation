"""Auto-approve only the export-name dialog in regression tests."""
import itertools
import pytest

@pytest.fixture(autouse=True)
def approve_export_name(monkeypatch):
    from qtpy.QtWidgets import QDialog, QLineEdit
    original = QDialog.exec_
    serial = itertools.count()
    def execute(dialog):
        if dialog.windowTitle() == 'Review export folder name':
            field = dialog.findChild(QLineEdit)
            field.setText('test_export_' + str(next(serial)))
            return QDialog.Accepted
        return original(dialog)
    monkeypatch.setattr(QDialog, 'exec_', execute)
