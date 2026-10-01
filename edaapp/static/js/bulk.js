// Bulk actions: preview exactly the events that would be appended, then confirm.
// The server recomputes the plan on apply and refuses if it differs from the preview (token).
import { h, api, dsApi, num, toast, openDialog, closeDialog, verdictBadge } from "./util.js";

const ACTION_TEXT = {
  drop_paragraphs: "mark the matching paragraphs as dropped",
  drop_articles: "set the verdict of the matching articles to drop",
  undo: "undo the latest change of each article",
  clear: "clear the decision of each article",
};

export async function bulkDialog(ds, v, spec, { onDone } = {}) {
  let plan;
  try {
    plan = await api(dsApi(ds, "bulk/preview"), { body: { v, spec } });
  } catch (e) {
    toast(e.message, "error");
    return;
  }
  const s = plan.summary;
  const body = h("div", { class: "stack" });
  body.append(h("p", null, `This will ${ACTION_TEXT[plan.action]}. `,
    h("strong", null, `${num(plan.events.length)} event${plan.events.length === 1 ? "" : "s"}`),
    ` will be appended to the review log`, plan.action === "drop_paragraphs" ? `, dropping ${num(s.paragraphs)} more paragraph${s.paragraphs === 1 ? "" : "s"}` : "",
    `. ${num(plan.matched_articles)} articles matched; ${num(s.unchanged)} already had this decision${s.not_in_version ? `; ${num(s.not_in_version)} are not in this version` : ""}.`));
  if (s.carried_drops) body.append(h("p", { class: "muted small" }, `${num(s.carried_drops)} earlier paragraph drops refer to text that is not in this version (already removed by a rebuild, or changed). They are kept in the new events, so the next build keeps dropping them.`));
  body.append(h("p", { class: "muted small" }, "Each event is the article's whole decision (existing verdict and note are kept, plus the change). Every event gets the same timestamp when written. Undo is per article, in Decisions."));

  const t = h("table", { class: "data" }, h("thead", null, h("tr", null, h("th", null, "article"), h("th", null, "verdict"), h("th", null, "dropped paragraphs"), h("th", null, "note"))));
  const tb = h("tbody");
  for (const ev of plan.events.slice(0, 300)) {
    const ex = plan.excerpts && plan.excerpts[ev.id];
    const drops = ev.drop_paragraphs.map((d) => h("div", { class: "odia small" }, h("strong", null, `¶${d.para} `),
      ex && ex[d.para] ? ex[d.para] : ex && ex[d.para] === null ? h("span", { class: "muted" }, "(kept from before; not in this text)") : ""));
    tb.append(h("tr", null, h("td", { class: "title" }, h("a", { href: `#/d/${encodeURIComponent(ds)}/a/${ev.id}`, target: "_blank" }, ev.title), h("div", { class: "muted small" }, `id ${ev.id}`)),
      h("td", null, verdictBadge(ev.verdict)), h("td", null, drops.length ? drops : h("span", { class: "muted" }, "none")), h("td", { class: "small" }, ev.note)));
  }
  t.append(tb);
  body.append(h("div", { class: "table-wrap" }, t));
  if (plan.events.length > 300) body.append(h("p", { class: "muted small" }, `Showing 300 of ${num(plan.events.length)}; all of them are in the JSON below.`));
  const jsonl = plan.events.map((e) => JSON.stringify({ ts: "<time of writing>", ...e })).join("\n");
  body.append(h("details", null, h("summary", null, `The exact lines to be appended (${num(plan.events.length)})`), h("pre", { class: "json" }, jsonl)));

  const confirm = h("button", { class: "primary", disabled: plan.events.length ? null : true, onclick: async () => {
    confirm.disabled = true;
    try {
      const r = await api(dsApi(ds, "bulk/apply"), { body: { v, spec, token: plan.token } });
      closeDialog();
      toast(`Recorded ${num(r.written)} events`);
      if (onDone) onDone(r);
    } catch (e) {
      toast(e.message, "error");
      confirm.disabled = false;
    }
  } }, plan.events.length ? `Record ${num(plan.events.length)} events` : "Nothing to record");
  openDialog("Confirm bulk action", body, [h("button", { onclick: closeDialog }, "Cancel"), confirm]);
}
