"""Inline CSS shared by the archival HTML report and the local console.

Everything is one string: the HTML report must stay self-contained and the
console must not fetch external assets.
"""

BASE_CSS = """
:root {
  color-scheme: light dark;
  --bg: #f4f4f0; --surface: #ffffff; --surface-2: #ecece6; --border: #d6d6ce;
  --text: #1c1d1a; --muted: #66675f; --accent: #20584c; --accent-soft: #dcece7;
  --danger: #9d352c; --danger-soft: #f6e3df; --warn: #7d5300; --warn-soft: #f6ecd6;
  --ok: #2e6438; --ok-soft: #dfefdf;
  --mono: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
  --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans SC", "Microsoft YaHei", sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #111310; --surface: #1a1d18; --surface-2: #22261f; --border: #363b31;
    --text: #eceee7; --muted: #a3a79b; --accent: #85c9b4; --accent-soft: #233c34;
    --danger: #f09286; --danger-soft: #402522; --warn: #e1b25b; --warn-soft: #3c321d;
    --ok: #8cc895; --ok-soft: #203522;
  }
}
* { box-sizing: border-box; }
html { background: var(--bg); }
body {
  margin: 0 auto; max-width: 1180px; padding: 36px 22px 56px;
  background: var(--bg); color: var(--text); font: 14px/1.6 var(--sans);
}
h1, h2, h3 { line-height: 1.25; margin: 0 0 10px; }
h1 { font-size: 27px; letter-spacing: -0.02em; }
h2 { font-size: 19px; margin-top: 32px; }
h3 { font-size: 16px; }
p { margin: 0 0 12px; }
a { color: var(--accent); text-decoration-thickness: 1px; text-underline-offset: 2px; }
code, pre { font-family: var(--mono); font-size: 12.5px; }
code { background: var(--surface-2); border-radius: 4px; padding: 1px 5px; overflow-wrap: anywhere; }
pre {
  margin: 0; padding: 12px 14px; background: var(--surface-2);
  border: 1px solid var(--border); border-radius: 8px; overflow: auto;
  white-space: pre-wrap; overflow-wrap: anywhere;
}
.eyebrow {
  margin: 0 0 6px; color: var(--muted); font-size: 12px; font-weight: 650;
  letter-spacing: 0.12em; text-transform: uppercase;
}
.lede { color: var(--muted); max-width: 780px; }
.meta { display: flex; flex-wrap: wrap; gap: 8px; margin: 18px 0 4px; }
.pill {
  display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--border);
  border-radius: 999px; padding: 3px 10px; background: var(--surface);
  color: var(--muted); font-size: 12px;
}
.status-fail { color: var(--danger); border-color: var(--danger); background: var(--danger-soft); }
.status-warn { color: var(--warn); border-color: var(--warn); background: var(--warn-soft); }
.status-pass { color: var(--ok); border-color: var(--ok); background: var(--ok-soft); }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(132px, 1fr)); gap: 10px; margin: 20px 0 4px; }
.card { padding: 14px 16px; background: var(--surface); border: 1px solid var(--border); border-radius: 10px; }
.card .label { color: var(--muted); font-size: 12px; }
.card .value { font-size: 23px; font-weight: 680; letter-spacing: -0.02em; }
.card .value.danger { color: var(--danger); }
.card .value.warn { color: var(--warn); }
.card .value.ok { color: var(--ok); }
.table-wrap { overflow-x: auto; border: 1px solid var(--border); border-radius: 10px; background: var(--surface); }
table { width: 100%; border-collapse: collapse; }
th, td { padding: 8px 10px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }
th { background: var(--surface-2); color: var(--muted); font-size: 12px; font-weight: 650; white-space: nowrap; }
tbody tr:last-child td { border-bottom: 0; }
tbody tr:hover { background: var(--surface-2); }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.sev { display: inline-block; border-radius: 4px; padding: 1px 6px; font-size: 11px; font-weight: 700; text-transform: uppercase; }
.sev-error { color: var(--danger); background: var(--danger-soft); }
.sev-warning { color: var(--warn); background: var(--warn-soft); }
.sev-info { color: var(--accent); background: var(--accent-soft); }
.location { font-family: var(--mono); font-size: 12px; overflow-wrap: anywhere; }
.raw { font-family: var(--mono); font-size: 12px; overflow-wrap: anywhere; }
.muted { color: var(--muted); }
.empty { padding: 22px; border: 1px dashed var(--border); border-radius: 10px; color: var(--muted); text-align: center; }
footer { margin-top: 40px; padding-top: 16px; border-top: 1px solid var(--border); color: var(--muted); font-size: 12px; }
.filter {
  width: 100%; margin: 8px 0 12px; padding: 9px 12px; border: 1px solid var(--border);
  border-radius: 8px; background: var(--surface); color: var(--text); font: inherit;
}
.filter:focus { outline: 2px solid var(--accent); outline-offset: 1px; }
@media (max-width: 680px) {
  body { padding: 24px 14px 40px; }
  h1 { font-size: 23px; }
  th, td { padding: 7px 8px; }
}
@media print {
  body { max-width: none; padding: 0; background: #fff; color: #000; }
  .filter { display: none; }
  .table-wrap { border-color: #bbb; }
}
"""

CONSOLE_CSS = """
nav.tabs { display: flex; gap: 8px; flex-wrap: wrap; margin: 18px 0; }
nav.tabs a { padding: 5px 10px; border: 1px solid var(--border); border-radius: 7px; background: var(--surface); text-decoration: none; font-size: 13px; }
nav.tabs a.active { background: var(--accent-soft); border-color: var(--accent); }
.filters { display: flex; gap: 10px; flex-wrap: wrap; align-items: end; padding: 14px; margin: 12px 0 16px; border: 1px solid var(--border); border-radius: 10px; background: var(--surface); }
.filters label { display: grid; gap: 4px; color: var(--muted); font-size: 12px; }
.filters select, .filters button { padding: 7px 9px; border: 1px solid var(--border); border-radius: 7px; background: var(--surface); color: var(--text); font: inherit; }
.filters button { background: var(--accent); color: var(--surface); border-color: var(--accent); cursor: pointer; }
.detail-grid { display: grid; grid-template-columns: minmax(110px, 180px) 1fr; gap: 0; margin: 18px 0; border: 1px solid var(--border); border-radius: 10px; overflow: hidden; background: var(--surface); }
.detail-grid dt, .detail-grid dd { margin: 0; padding: 9px 12px; border-bottom: 1px solid var(--border); }
.detail-grid dt { color: var(--muted); background: var(--surface-2); font-weight: 600; }
.detail-grid dd { overflow-wrap: anywhere; }
.detail-grid dt:last-of-type, .detail-grid dd:last-of-type { border-bottom: 0; }
.back { display: inline-block; margin-bottom: 14px; }
.error-box { padding: 18px; border: 1px solid var(--danger); border-radius: 10px; background: var(--danger-soft); color: var(--danger); }
"""

