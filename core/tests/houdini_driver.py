"""Run inside hython: build a little scene, connect to the core, service calls, and hot-reload part-way.

    hython houdini_driver.py <houdini/python3.13libs dir> <seconds>
"""
import queue
import sys
import threading
import time

libs, seconds = sys.argv[1], float(sys.argv[2])
sys.path.insert(0, libs)
import hou  # noqa: E402

geo = hou.node("/obj").createNode("geo", "geo1")
box = geo.createNode("box", "box1")
box.setSelected(True, clear_all_selected=True)

calls: "queue.Queue" = queue.Queue()


def runner(fn):
    done, box_ = threading.Event(), {}
    calls.put((fn, box_, done))
    if not done.wait(60):
        raise TimeoutError("main thread never ran the call")
    if "error" in box_:
        raise box_["error"]
    return box_["value"]


import houdini_pi  # noqa: E402  (also puts the shared library on sys.path)
import houdini_pi.runtime as runtime  # noqa: E402

runtime.set_main_runner(runner)
runtime.start()
reloaded = False
end = time.time() + seconds
while time.time() < end:
    try:
        fn, box_, done = calls.get(timeout=0.02)
    except queue.Empty:
        pass
    else:
        try:
            box_["value"] = fn()
        except Exception as error:
            box_["error"] = error
        finally:
            done.set()
    if not reloaded and time.time() > end - seconds + float(sys.argv[3]) if len(sys.argv) > 3 else False:
        reloaded = True
        old = sys.modules["houdini_pi.runtime"]
        houdini_pi.reload()
        fresh = sys.modules["houdini_pi.runtime"]
        assert fresh is not old, "reload kept the stale module"
        fresh.set_main_runner(runner)
        runtime = fresh
print("DRIVER: connected =", runtime.connected(), "reloaded =", reloaded)
runtime.stop()
