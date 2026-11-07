"""Run inside hython: the Houdini plugin's pane, panel interface and menu, as the real Qt widget against a real core.

    hython houdini_pane_driver.py <repo root>

Prints PASS/FAIL lines. Offscreen Qt, so no display is needed; Houdini's own GUI cannot run headless, and this covers
what it would exercise: the panel interface Houdini discovers, the menu entries, the pane widget and QtWebEngine.
"""
import json
import os
import sys
import time
import xml.etree.ElementTree as ET

root = sys.argv[1]
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--no-sandbox --disable-gpu --disable-software-rasterizer"
sys.path.insert(0, os.path.join(root, "houdini", "python3.13libs"))
import hou  # noqa: E402


def show(label, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + label + (f"  [{extra}]" if extra else ""), flush=True)


# 1. the panel interface file installs and names the right module
hou.pypanel.installFile(os.path.join(root, "houdini", "python_panels", "houdini_pi.pypanel"))
iface = hou.pypanel.interfaceByName("houdini_pi")
show("panel interface registered", iface is not None, iface.label() if iface else "")

# 2. the menu entries exist and what they run resolves
menu = ET.parse(os.path.join(root, "houdini", "MainMenuCommon.xml")).getroot()
items = {i.get("id"): i.findtext("scriptCode") for i in menu.iter("scriptItem")}
show("menu has Houdini Agent and Reload Agent", {"houdini_pi_open", "houdini_pi_reload"} <= set(items))
import houdini_pi  # noqa: E402
import houdini_pi.panel  # noqa: E402
show("menu commands resolve", callable(houdini_pi.reload) and callable(houdini_pi.panel.open_pane))

# 3. the pane: real Qt widget, real QtWebEngine, real core
from PySide6 import QtCore, QtWidgets  # noqa: E402

QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_ShareOpenGLContexts)
from PySide6 import QtWebEngineWidgets  # noqa: E402,F401  (before the QApplication exists)

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
import houdini_pi.runtime as rt  # noqa: E402

rt.start()
pane = houdini_pi.panel.AgentPane()
JS = "JSON.stringify([!!document.querySelector('[data-testid=\"prompt\"]'), (document.querySelector('[data-testid=\"welcome\"] h2')||{}).textContent || ''])"
dom, shown, next_ask, end = {}, False, 0, time.time() + 45
while time.time() < end:
    app.processEvents()
    time.sleep(0.05)
    widget = pane.currentWidget()
    if type(widget).__name__ != "QWebEngineView":
        continue
    shown = True
    if time.time() >= next_ask:  # React renders after the page loads and the socket says hello: keep asking
        next_ask = time.time() + 0.7
        widget.page().runJavaScript(JS, lambda r: dom.update(result=json.loads(r) if isinstance(r, str) else r))
    if dom.get("result") and dom["result"][0] and dom["result"][1]:
        break
result = dom.get("result") or [False, ""]
show("pane left its placeholder for the web view", shown)
show("the chat page rendered inside the pane", bool(result[0]))
show("the pane says it belongs to Houdini", "Houdini" in result[1], result[1])
show("connector connected to the core", rt.connected())
rt.stop()
