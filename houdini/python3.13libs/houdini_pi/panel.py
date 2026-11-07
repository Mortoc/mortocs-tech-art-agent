"""The Python Panel: a QWebEngineView onto the agent core's page. Nothing else lives here.

Startup is asynchronous on purpose: createInterface runs on Houdini's main thread, so the connection to the
core is polled from a QTimer while a placeholder shows. A blocking wait here froze Houdini for seconds.
"""

from __future__ import annotations

import os
import traceback

import json

from PySide6 import QtCore, QtWidgets

from houdini_pi import drops, runtime
from houdini_pi.settings import pane_zoom, software_render_wanted

POLL_MS = 250
GIVE_UP_AFTER_POLLS = 120  # 30 s


def _houdini_zoom() -> float:
    """Zoom for the page so it matches Houdini's Global UI Size preference."""
    try:
        import hou

        return pane_zoom(hou.ui.globalScaleFactor())
    except Exception:  # no UI (hython) or an old hou: render at 1x
        return 1.0


class _GluedPage:
    """A QWebEnginePage that never navigates away from the agent URL.

    This pane is an app, not a browser: the only navigation allowed is the
    initial load (and reloads of the exact same URL, e.g. the reconnect button).
    Link clicks, window.location changes, form submissions and target=_blank
    are all refused. Built lazily because QtWebEngineCore may not be importable
    until the view is created.
    """

    def __new__(cls, allowed_url: str, parent):
        from PySide6 import QtWebEngineCore, QtWebEngineWidgets

        class GluedPage(QtWebEngineCore.QWebEnginePage):
            def __init__(self, url: str, parent):
                super().__init__(parent)
                self._allowed = QtCore.QUrl(url)

            def acceptNavigationRequest(self, url, _nav_type, is_main_frame):
                # Only the exact initial URL, only in the main frame. Reloads
                # of the same URL pass; everything else is refused.
                return is_main_frame and url == self._allowed

        return GluedPage(allowed_url, parent)


class AgentPane(QtWidgets.QStackedWidget):
    def __init__(self):
        super().__init__()
        self._label = QtWidgets.QLabel("Connecting to the agent core…")
        self._label.setWordWrap(True)
        self._label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self._label.setAlignment(QtCore.Qt.AlignTop)
        self.addWidget(self._label)
        self._polls = 0
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)
        try:
            runtime.start()  # normally already running since Houdini started; idempotent
        except Exception:
            self._fail(traceback.format_exc())
            return
        self._timer.start()
        self._poll()

    def _poll(self):
        self._polls += 1
        if runtime.connected():
            self._timer.stop()
            self._show(runtime.pane_url())
        elif self._polls >= GIVE_UP_AFTER_POLLS:
            self._timer.stop()
            self._fail(
                f"Could not reach the agent core within {GIVE_UP_AFTER_POLLS * POLL_MS // 1000}s.\n\n"
                "On NixOS it starts by itself when something connects to 127.0.0.1:9100 (services.tech-art-agent). "
                "Elsewhere run `tech-art-core`, or record how to start it in ~/.tech-art-agent/install.json.\n\n"
                + "\n".join(runtime.LOG[-5:])
            )

    def _show(self, url: str):
        if software_render_wanted():
            _keep_the_pane_off_the_gpu()
        try:
            from PySide6 import QtWebEngineWidgets
        except ImportError:
            self._fail("This Houdini's PySide6 has no QtWebEngineWidgets; the agent pane needs it.")
            return
        view = QtWebEngineWidgets.QWebEngineView()
        # Glue the page: no navigation away from the agent, ever.
        view.setPage(_GluedPage(url, view))
        self._drops = DropForwarder(view)
        # Follow Houdini's Global UI Size. Chromium can reset zoom on load, so
        # set it again once the page is in.
        zoom = _houdini_zoom()
        view.setZoomFactor(zoom)
        view.loadFinished.connect(lambda _ok: view.setZoomFactor(zoom))
        view.setUrl(QtCore.QUrl(url))
        self.addWidget(view)
        self.setCurrentWidget(view)

    def _fail(self, text: str):
        self._label.setText(text)
        self.setCurrentWidget(self._label)


def _keep_the_pane_off_the_gpu():
    """Render the web view in software, so it never competes with COPs for the GPU.

    QWebEngineView draws through a Qt Quick scene graph that wants its own GL
    context. With the card full (Copernicus + a model server), that context
    creation failed and Qt treated it as fatal: Houdini aborted in
    QSGRenderLoop::handleContextCreationFailure on 2026-10-12. Software Quick
    plus Chromium without GPU compositing takes the pane out of that contest,
    at the cost of GPU-smooth scrolling. Both must be set before QtWebEngine
    starts.
    """
    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    for flag in ("--disable-gpu", "--disable-gpu-compositing"):
        if flag not in flags:
            flags = f"{flags} {flag}".strip()
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = flags
    try:
        from PySide6 import QtQuick

        QtQuick.QQuickWindow.setGraphicsApi(QtQuick.QSGRendererInterface.GraphicsApi.Software)
    except Exception as exc:  # already decided by an earlier Quick window; nothing to do about it now
        print(f"[houdini-pi] could not select the software scene graph: {exc}")


class DropForwarder(QtCore.QObject):
    """Hand Houdini's own drags (nodes, parms, channels) to the page.

    Houdini drags carry SideFX mime types and no text, so the page's DOM drop
    sees nothing. The web view's render widget gets the Qt events; this filter
    sits on it, accepts Houdini drags, and calls window.houdiniPiDrop(paths).
    """

    def __init__(self, view):
        super().__init__(view)
        self._view = view
        view.installEventFilter(self)
        view.loadFinished.connect(self._watch_render_widget)
        self._watch_render_widget()

    def _watch_render_widget(self, *_):
        proxy = self._view.focusProxy()
        if proxy is not None:
            proxy.installEventFilter(self)
        for child in self._view.findChildren(QtWidgets.QWidget):
            child.installEventFilter(self)

    def eventFilter(self, watched, event):  # noqa: N802
        kind = event.type()
        is_drag = kind in (QtCore.QEvent.DragEnter, QtCore.QEvent.DragMove, QtCore.QEvent.Drop)
        if not is_drag:
            return False
        mime = event.mimeData()
        if not drops.is_houdini_drag(list(mime.formats())):
            return False
        if kind != QtCore.QEvent.Drop:
            event.acceptProposedAction()
            return True
        payload = {fmt: bytes(mime.data(fmt).data()) for fmt in mime.formats() if fmt in drops.HOUDINI_PATH_TYPES}
        paths = drops.paths_from_mime(payload)
        event.acceptProposedAction()
        self._view.page().runJavaScript(f"window.houdiniPiDrop && window.houdiniPiDrop({json.dumps(paths)})")
        return True


def createInterface():  # noqa: N802  (Houdini's required name)
    return AgentPane()


INTERFACE_NAME = "houdini_pi"


def open_pane():
    """Show the Pi pane as a docked tab: reuse an open one, else add a tab beside the Parameter view.

    The pane is a Python Panel interface, so it also appears in every pane's
    tab-type menu (New Tab ▸ Python Panel ▸ Pi) like the Geometry Spreadsheet.
    """
    import hou

    for tab in hou.ui.paneTabs():
        is_ours = tab.type() == hou.paneTabType.PythonPanel and tab.activeInterface() is not None and tab.activeInterface().name() == INTERFACE_NAME
        if is_ours:
            tab.setIsCurrentTab()
            return tab

    desktop = hou.ui.curDesktop()
    anchor = desktop.paneTabOfType(hou.paneTabType.Parm) or desktop.paneTabOfType(hou.paneTabType.NetworkEditor)
    pane = anchor.pane() if anchor is not None else None
    tab = pane.createTab(hou.paneTabType.PythonPanel) if pane is not None else desktop.createFloatingPaneTab(hou.paneTabType.PythonPanel)
    tab.setActiveInterface(hou.pypanel.interfaceByName(INTERFACE_NAME))
    tab.setIsCurrentTab()
    return tab
