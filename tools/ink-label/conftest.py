"""Keep automated GUI tests away from the operator's desktop settings."""
import pytest
from PySide6.QtCore import QSettings

@pytest.fixture(scope="session",autouse=True)
def isolated_qsettings(tmp_path_factory):
    old=QSettings.defaultFormat()
    folder=tmp_path_factory.mktemp("inklabel_settings")
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat,QSettings.UserScope,str(folder))
    yield
    QSettings.setDefaultFormat(old)
