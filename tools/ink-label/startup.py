import sys
import traceback
from pathlib import Path
root=Path(__file__).resolve().parent
log=(root/"application.log").open("a",encoding="utf-8",buffering=1)
sys.stdout=log; sys.stderr=log
def error_hook(kind,value,tb):
    traceback.print_exception(kind,value,tb,file=log)
    try:
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(None,"InkLabel",f"程序遇到错误：{value}\n详细信息已写入 application.log")
    except Exception: pass
sys.excepthook=error_hook
try:
    from app import main
    main()
except Exception:
    error_hook(*sys.exc_info())
