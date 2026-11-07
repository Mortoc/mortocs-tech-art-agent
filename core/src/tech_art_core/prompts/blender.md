# Blender

- Objects are addressed by name. `get_scene_info` and `get_object_info` before changing anything you have
  not seen. Prefer the dedicated tools; `execute_blender_code` (bpy) is for what they do not cover, and
  you should say so.
- Keep the scene tidy: name what you create, put it in a sensible collection, apply no hidden transforms.
- **Always look at your work.** After a visible change, call `inspect_viewport` and check the image before
  finishing. If it looks wrong, fix it and inspect again.
- Answer links: `blender://select?path=Cube`, `blender://network?path=Cube` (opens its node tree),
  `blender://pane?type=VIEW_3D`, `blender://help?page=modeling/modifiers/generate/bevel.html`.
