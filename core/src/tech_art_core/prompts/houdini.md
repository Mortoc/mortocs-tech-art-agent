# Houdini

- Node paths are absolute (`/obj/geo1/wire1`). Parameter names are the internal names, not the labels:
  `get_parameters` before `set_parameter`. `get_node_card` or `explain_node` for a node you do not know.
- **Place nodes coherently.** The network editor's Y axis points down (y=0 at the top); X increases to the
  right. Lay out SOP chains top-to-bottom (increasing Y). Lay out COP chains left-to-right (increasing X).
  Set positions with `set_parameter` on `tx`/`ty` (or pass `position` to `create_node`) so the network reads
  cleanly without a manual tidy-up.
- Tools that are not direct are reachable through tool search; search before concluding a tool is missing.
- **Always look at your work in COPs.** After any change inside a Copernicus network, call `inspect_cop` on
  the network's output node and check the image before finishing. If it looks wrong, fix it and inspect again.
- Answer links: `houdini://select?path=/obj/geo1`, `houdini://parm?path=/obj/geo1/wire1/radius`,
  `houdini://network?path=/obj/geo1`, `houdini://pane?type=Parm`, `houdini://help?page=/nodes/sop/polyextrude`.
