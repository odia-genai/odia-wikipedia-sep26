// Rendered documents (Methodology, reports, docs): heading anchors, table of contents,
// in-document links, and the section currently in view. The HTML comes from the server's
// Markdown renderer with raw HTML disabled; references are already app links (class "ref").
import { h, typeset } from "./util.js";

// hrefFor(id) -> the app URL that shows this document scrolled to heading `id`
export function renderDoc(container, doc, hrefFor) {
  container.innerHTML = doc.html; // server-rendered Markdown, raw HTML disabled
  typeset(container);
  for (const hd of container.querySelectorAll("h1[id], h2[id], h3[id], h4[id], h5[id], h6[id]")) {
    hd.append(h("a", { class: "anchor", href: hrefFor(hd.id), title: "Link to this section", "aria-label": `Link to section ${hd.textContent}` }, "#"));
  }
  for (const a of container.querySelectorAll("a[href]")) {
    const href = a.getAttribute("href");
    if (href.startsWith("#") && !href.startsWith("#/")) {
      // [text](#section) inside the document: keep it inside the app's hash routes
      let id = href.slice(1);
      try { id = decodeURIComponent(id); } catch { /* keep as written */ }
      a.setAttribute("href", hrefFor(id));
    } else if (/^https?:/i.test(href)) {
      a.target = "_blank";
      a.rel = "noopener";
    }
  }
}

export function tocNav(toc, hrefFor, { minLevel = 2, maxLevel = 4 } = {}) {
  const nav = h("nav", { class: "toc", "aria-label": "Contents" });
  const items = toc.filter((t) => t.level >= minLevel && t.level <= maxLevel);
  if (!items.length) return null;
  for (const t of items) {
    nav.append(h("a", { href: hrefFor(t.id), class: `l${t.level - minLevel + 1}`, "data-id": t.id, title: t.text }, t.text));
  }
  return nav;
}

export function scrollToHeading(container, id, smooth = false) {
  if (!id) return false;
  const el = container.querySelector(`[id="${CSS.escape(id)}"]`);
  if (!el) return false;
  // smooth only for short hops: a long smooth scroll takes over a second; the flash shows where you are
  const near = Math.abs(el.getBoundingClientRect().top) < 1.5 * innerHeight;
  el.scrollIntoView({ behavior: smooth && near ? "smooth" : "auto", block: "start" });
  el.classList.add("flash-target");
  setTimeout(() => el.classList.remove("flash-target"), 1600);
  return true;
}

// Mark the table-of-contents entry of the section at the top of the window. Returns a cleanup.
export function trackActive(container, nav) {
  if (!nav) return () => {};
  const links = new Map([...nav.querySelectorAll("a[data-id]")].map((a) => [a.dataset.id, a]));
  let ticking = false;
  const update = () => {
    ticking = false;
    let current = null;
    for (const hd of container.querySelectorAll("[id]")) {
      if (!links.has(hd.id)) continue;
      if (hd.getBoundingClientRect().top < 110) current = hd.id; else break;
    }
    for (const [id, a] of links) a.classList.toggle("active", id === current);
    // keep the active entry visible in the contents column. Not with scrollIntoView: that would
    // also touch the window's scroll and cut short a smooth scroll to a heading.
    const act = current && links.get(current);
    const box = act && scrollParent(nav);
    if (act && box) {
      const r = act.getBoundingClientRect(), b = box.getBoundingClientRect();
      if (r.top < b.top) box.scrollTop -= b.top - r.top + 8;
      else if (r.bottom > b.bottom) box.scrollTop += r.bottom - b.bottom + 8;
    }
  };
  const onScroll = () => { if (!ticking) { ticking = true; requestAnimationFrame(update); } };
  window.addEventListener("scroll", onScroll, { passive: true });
  update();
  return () => window.removeEventListener("scroll", onScroll);
}

function scrollParent(el) {
  for (let p = el; p && p !== document.body; p = p.parentElement) {
    const o = getComputedStyle(p).overflowY;
    if ((o === "auto" || o === "scroll") && p.scrollHeight > p.clientHeight) return p;
  }
  return null;
}

// The heading nearest the top of the window (to keep the reader's place across a live reload).
export function topHeading(container) {
  let current = null;
  for (const hd of container.querySelectorAll("h1[id], h2[id], h3[id], h4[id]")) {
    if (hd.getBoundingClientRect().top < 110) current = hd; else break;
  }
  return current ? { id: current.id, offset: current.getBoundingClientRect().top } : null;
}

export function restoreHeading(container, mark) {
  if (!mark) return;
  const el = container.querySelector(`[id="${CSS.escape(mark.id)}"]`);
  if (el) window.scrollBy(0, el.getBoundingClientRect().top - mark.offset);
}
