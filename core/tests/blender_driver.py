"""Run inside `blender --background`: load the staged extension, connect to the core, and service calls.

    blender -b --python blender_driver.py -- <stage-parent-dir> <seconds>
"""
import sys
import time

stage, seconds = sys.argv[sys.argv.index("--") + 1:][:2]
sys.path.insert(0, stage)
import bpy  # noqa: E402

import tech_art_agent as ext  # noqa: E402  (the staged extension package)

bpy.ops.object.select_all(action="DESELECT")
bpy.ops.mesh.primitive_cube_add()
bpy.context.active_object.name = "Wheel"
ext.start()
end = time.time() + float(seconds)
while time.time() < end:
    ext._drain()  # timers do not run under --background; pump the main-thread queue ourselves
    time.sleep(0.02)
print('DRIVER: connected =', ext._connector.connected if ext._connector else None, 'log =', ext._status['log'])
ext.stop()
