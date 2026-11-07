"""Runs when Houdini starts: connect this Houdini to the agent core, so other apps can see it and the pane opens ready."""

try:
    import hou

    if hou.isUIAvailable():
        import houdini_pi.runtime

        houdini_pi.runtime.start()
except Exception:  # never let the agent's trouble get in the way of starting Houdini
    import traceback

    traceback.print_exc()
