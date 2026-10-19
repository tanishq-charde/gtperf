"""Drive headless LibreOffice Calc over UNO, to recalculate workbooks and run VBA in tests."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path


def available():
    if not shutil.which("soffice"):
        return False
    try:
        import uno  # noqa: F401
    except ImportError:
        return False
    return True


class Calc:
    """A private headless soffice process. Use as a context manager."""

    def __enter__(self):
        import uno

        self._profile = tempfile.mkdtemp(prefix="lo_profile_")
        pipe = f"gtperf_{uuid.uuid4().hex[:8]}"
        self._proc = subprocess.Popen(
            ["soffice", "--headless", "--invisible", "--norestore", "--nologo", "--nodefault",
             f"-env:UserInstallation=file://{self._profile}", f"--accept=pipe,name={pipe};urp;"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        local = uno.getComponentContext()
        resolver = local.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", local)
        for _ in range(120):
            try:
                ctx = resolver.resolve(f"uno:pipe,name={pipe};urp;StarOffice.ComponentContext")
                break
            except Exception:
                time.sleep(0.25)
        else:
            raise RuntimeError("LibreOffice did not start")
        self.desktop = ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
        return self

    def __exit__(self, *exc):
        try:
            self.desktop.terminate()
        except Exception:
            pass
        try:
            self._proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        shutil.rmtree(self._profile, ignore_errors=True)

    @staticmethod
    def _props(**kw):
        from com.sun.star.beans import PropertyValue

        out = []
        for k, v in kw.items():
            p = PropertyValue()
            p.Name, p.Value = k, v
            out.append(p)
        return tuple(out)

    def open(self, path, macros=False):
        import uno

        # MacroExecutionMode 4 = ALWAYS_EXECUTE_NO_WARN, needed to call document Basic from cells
        props = self._props(Hidden=True, MacroExecutionMode=4 if macros else 0)
        return self.desktop.loadComponentFromURL(uno.systemPathToFileUrl(str(Path(path).resolve())),
                                                 "_blank", 0, props)

    @staticmethod
    def add_vba_module(doc, name, code):
        """Insert VBA source as a Basic module running in VBA compatibility mode."""
        code = "\n".join(line for line in code.splitlines() if not line.startswith("Attribute "))
        libs = doc.BasicLibraries
        lib = libs.getByName("Standard") if libs.hasByName("Standard") else libs.createLibrary("Standard")
        lib.insertByName(name, "Option VBASupport 1\n" + code)

    def save_as(self, doc, path, filter_name):
        import uno

        doc.storeToURL(uno.systemPathToFileUrl(str(Path(path).resolve())), self._props(FilterName=filter_name))


def cell(doc, sheet, address):
    """Value of a cell: a number, or the string for text and errors."""
    c = doc.Sheets.getByName(sheet).getCellRangeByName(address)
    from com.sun.star.table.CellContentType import FORMULA, TEXT

    if c.Type == TEXT or (c.Type == FORMULA and c.getError() != 0) or (c.Type == FORMULA and c.FormulaResultType2 ==
                                                                        2):
        return c.String
    return c.Value


def set_formula(doc, sheet, address, formula):
    doc.Sheets.getByName(sheet).getCellRangeByName(address).Formula = formula


os.environ.setdefault("SAL_USE_VCLPLUGIN", "svp")
