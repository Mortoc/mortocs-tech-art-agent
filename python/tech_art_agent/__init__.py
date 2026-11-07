"""The host-independent half of the tech-art agent that lives inside a DCC (Houdini, Blender).

Pure standard library, no Qt, no DCC imports: the same files are loaded by
Houdini through PYTHONPATH and copied into the Blender extension at build
time (scripts/build_blender.py), so there is exactly one copy in source.
"""
