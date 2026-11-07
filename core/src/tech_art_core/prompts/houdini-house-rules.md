# House rules

Defaults for how the agent builds networks. These are the maintainer's
preferences, not laws: project memory overrides anything here, and a project
that wants different conventions should say so in its project memory.

## NULLs mark finished parts, object merges mark inputs

As each piece of an asset is created, end it with a NULL named in
CAPITAL_CASE with an `_OUT` suffix: `FLOORS_OUT`, `WALLS_OUT`, `ROOF_OUT`,
`TRIM_OUT`. Anytime you think "this part is done now from these nodes", that
is when you add the NULL. The NULL is the output of that sub-tree; connect
downstream nodes to it, not to the last SOP inside.

At the top of the network, object merges that bring in source geometry get
the matching `_IN` name: `FLOOR_IN`, `FLOOR_IN1`, `FLOOR_IN2` for multiple
sources of the same part, `WALLS_IN`, `ROOF_IN`. The `_IN`/`_OUT` pair makes
the data flow obvious at a glance: `FLOOR_IN` → … → `FLOOR_OUT`.

CAPITAL_CASE so they sort to the top of lists and treeviews and are easy to
spot in a network.

## UI design

When building an HDA, subnet or any node with a user-facing interface:

- **Do not add more functionality than the user asked for.** If they ask for
  a thing that does X, build X. Extra buttons, hidden parameters and
  "might be useful later" controls are scope creep. If you think something
  else would help, ask first.
- **Agree on the controls before you build.** Before writing the UI, state
  what controls will be present, what each does, and what units they use.
  Get the user's yes before you start. A wrong UI is worse than no UI.
- **A switch on an int value is a dropdown, not a raw int.** Use a menu
  parameter with named options (`0: Clay`, `1: Wood`, `2: Metal`) so the user
  never has to guess what `2` means. The internal value can stay an int; the
  UI should not show one.
- **Organise the UI.** Use folders and separators so related controls sit
  together. Put the most-used controls at the top. Use disable-when scripts
  to grey out controls that do not apply in the current mode, rather than
  hiding them or leaving them misleadingly active.
- **Label for units.** If a parameter is a distance, say so in the label
  (`Thickness (m)`), or the user will not know what the hip's units are.
- **Make it nice.** Consistent spacing, sensible defaults, help cards on
  non-obvious controls. A UI the user enjoys using is part of the deliverable.
