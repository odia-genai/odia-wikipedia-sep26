// Files: the dataset folder as a tree with sizes. Big folders show counts and totals only.
import { h, append, api, dsApi, num, bytes, when } from "../util.js";

export async function render({ main, ds }) {
  const r = await api(dsApi(ds, "files"));
  main.append(h("h1", null, "Files"), h("p", { class: "muted" }, h("code", null, r.root),
    ` · ${num(r.tree.files)} files · ${bytes(r.tree.size)}. Folders with many entries or many bytes are summarised, not listed.`));
  main.append(h("div", { class: "card tree" }, h("ul", null, node(r.tree, true))));
}

function node(n, root = false) {
  if (n.type === "file") {
    return h("li", null, h("span", { class: "name" }, n.name), h("span", { class: "size", title: when(n.mtime) }, bytes(n.size)));
  }
  const li = h("li", { class: "dir" }, h("span", { class: "name" }, `${n.name}/`),
    h("span", { class: "size" }, `${num(n.files)} files · ${bytes(n.size)}`));
  if (n.summarised) {
    const exts = Object.entries(n.extensions || {}).sort((a, b) => b[1] - a[1]).slice(0, 6).map(([e, c]) => `${num(c)} ${e}`).join(", ");
    append(li, [h("span", { class: "chip", style: { marginLeft: "8px" } }, `not expanded: ${n.reason}`),
      n.dirs ? h("span", { class: "size" }, `${num(n.dirs)} subfolders`) : null,
      exts ? h("span", { class: "size" }, `top level: ${exts}`) : null]);
    return li;
  }
  if (n.children && n.children.length) {
    const ul = h("ul");
    const kids = [...n.children].sort((a, b) => (a.type === b.type ? a.name.localeCompare(b.name) : a.type === "dir" ? -1 : 1));
    for (const c of kids) ul.append(node(c));
    li.append(ul);
  }
  if (root) li.open = true;
  return li;
}
