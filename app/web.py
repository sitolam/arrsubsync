"""The dashboard, served as three self-contained pages (no CDN, no build step)."""

from __future__ import annotations

BASE_CSS = """
:root {
  color-scheme: dark;
  --bg: #0d1117; --panel: #151b23; --panel-2: #1b2330; --line: #253041;
  --text: #e6edf3; --muted: #8b98a8; --accent: #4f9cf9; --accent-2: #7c5cff;
  --ok: #3fb950; --warn: #d29922; --bad: #f85149; --radius: 14px;
  --shadow: 0 8px 30px rgba(0,0,0,.35);
  font-synthesis-weight: none;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: radial-gradient(1200px 600px at 80% -10%, #1a2740 0%, var(--bg) 55%);
  color: var(--text); min-height: 100vh;
  font: 15px/1.55 ui-sans-serif, -apple-system, "Segoe UI", Roboto, Inter, sans-serif;
}
a { color: var(--accent); }
button, input, select { font: inherit; }
.btn {
  background: var(--panel-2); color: var(--text); border: 1px solid var(--line);
  padding: 8px 14px; border-radius: 10px; cursor: pointer; transition: .15s;
}
.btn:hover { border-color: var(--accent); transform: translateY(-1px); }
.btn:disabled { opacity: .45; cursor: not-allowed; transform: none; }
.btn.primary { background: linear-gradient(135deg, var(--accent), var(--accent-2)); border: 0; color: #fff; font-weight: 600; }
.btn.danger { border-color: #5a2b2b; color: #ffb4ae; }
.btn.small { padding: 5px 10px; font-size: 13px; border-radius: 8px; }
input[type=text], input[type=password], input[type=number], select {
  background: #0f151d; color: var(--text); border: 1px solid var(--line);
  border-radius: 10px; padding: 9px 12px; width: 100%;
}
input:focus, select:focus { outline: 2px solid rgba(79,156,249,.35); border-color: var(--accent); }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); box-shadow: var(--shadow); }
.muted { color: var(--muted); }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; }
"""

CENTERED = """
.wrap { min-height: 100vh; display: grid; place-items: center; padding: 24px; }
.box { width: min(420px, 100%); padding: 30px; }
.logo { display:flex; align-items:center; gap:10px; font-size: 20px; font-weight: 700; margin-bottom: 6px; }
.mark { border-radius: 8px; }
label { display:block; margin: 16px 0 6px; font-size: 13px; color: var(--muted); }
.err { color: var(--bad); min-height: 20px; font-size: 13px; margin-top: 12px; }
"""

LOGIN_PAGE = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:image/svg+xml,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20viewBox%3D%220%200%2064%2064%22%20width%3D%2264%22%20height%3D%2264%22%20role%3D%22img%22%20aria-label%3D%22arrsubsync%22%3E%20%3Cdefs%3E%20%3ClinearGradient%20id%3D%22bg%22%20x1%3D%220%22%20y1%3D%220%22%20x2%3D%221%22%20y2%3D%221%22%3E%20%3Cstop%20offset%3D%220%22%20stop-color%3D%22%234f9cf9%22%2F%3E%20%3Cstop%20offset%3D%221%22%20stop-color%3D%22%237c5cff%22%2F%3E%20%3C%2FlinearGradient%3E%20%3C%2Fdefs%3E%20%3Crect%20width%3D%2264%22%20height%3D%2264%22%20rx%3D%2215%22%20fill%3D%22url%28%23bg%29%22%2F%3E%20%3C%21--%20the%20audio%3A%20what%20the%20subtitle%20is%20aligned%20against%20--%3E%20%3Cg%20fill%3D%22%23fff%22%20opacity%3D%22.95%22%3E%20%3Crect%20x%3D%2213%22%20y%3D%2222%22%20width%3D%224%22%20height%3D%2210%22%20rx%3D%222%22%2F%3E%20%3Crect%20x%3D%2221%22%20y%3D%2216%22%20width%3D%224%22%20height%3D%2222%22%20rx%3D%222%22%2F%3E%20%3Crect%20x%3D%2229%22%20y%3D%2220%22%20width%3D%224%22%20height%3D%2214%22%20rx%3D%222%22%2F%3E%20%3Crect%20x%3D%2237%22%20y%3D%2213%22%20width%3D%224%22%20height%3D%2228%22%20rx%3D%222%22%2F%3E%20%3Crect%20x%3D%2245%22%20y%3D%2219%22%20width%3D%224%22%20height%3D%2216%22%20rx%3D%222%22%2F%3E%20%3C%2Fg%3E%20%3C%21--%20the%20subtitle%2C%20sitting%20under%20it%2C%20in%20step%20--%3E%20%3Cg%20fill%3D%22%23fff%22%3E%20%3Crect%20x%3D%2213%22%20y%3D%2245%22%20width%3D%2228%22%20height%3D%225%22%20rx%3D%222.5%22%2F%3E%20%3Crect%20x%3D%2245%22%20y%3D%2245%22%20width%3D%226%22%20height%3D%225%22%20rx%3D%222.5%22%20opacity%3D%22.55%22%2F%3E%20%3C%2Fg%3E%20%3C%2Fsvg%3E"><title>arrsubsync</title>
<style>{BASE_CSS}{CENTERED}</style></head><body>
<div class="wrap"><form class="card box" onsubmit="go(event)">
  <div class="logo"><svg class="mark" viewBox="0 0 64 64" width="26" height="26" aria-hidden="true"><defs><linearGradient id="lg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#4f9cf9"/><stop offset="1" stop-color="#7c5cff"/></linearGradient></defs><rect width="64" height="64" rx="15" fill="url(#lg)"/><g fill="#fff" opacity=".95"><rect x="13" y="22" width="4" height="10" rx="2"/><rect x="21" y="16" width="4" height="22" rx="2"/><rect x="29" y="20" width="4" height="14" rx="2"/><rect x="37" y="13" width="4" height="28" rx="2"/><rect x="45" y="19" width="4" height="16" rx="2"/></g><g fill="#fff"><rect x="13" y="45" width="28" height="5" rx="2.5"/><rect x="45" y="45" width="6" height="5" rx="2.5" opacity=".55"/></g></svg> arrsubsync</div>
  <div class="muted" style="font-size:13px">Sign in to manage your subtitle library.</div>
  <label>Username</label><input type="text" id="u" autocomplete="username" autofocus>
  <label>Password</label><input type="password" id="p" autocomplete="current-password">
  <div class="err" id="e"></div>
  <button class="btn primary" style="width:100%">Sign in</button>
</form></div>
<script>
async function go(ev) {{
  ev.preventDefault();
  const body = new FormData();
  body.append('username', document.getElementById('u').value);
  body.append('password', document.getElementById('p').value);
  const r = await fetch('/login', {{ method: 'POST', body }});
  if (r.ok) location.reload();
  else document.getElementById('e').textContent = (await r.json()).error || 'Sign in failed';
}}
</script></body></html>"""

SETUP_PAGE = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:image/svg+xml,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20viewBox%3D%220%200%2064%2064%22%20width%3D%2264%22%20height%3D%2264%22%20role%3D%22img%22%20aria-label%3D%22arrsubsync%22%3E%20%3Cdefs%3E%20%3ClinearGradient%20id%3D%22bg%22%20x1%3D%220%22%20y1%3D%220%22%20x2%3D%221%22%20y2%3D%221%22%3E%20%3Cstop%20offset%3D%220%22%20stop-color%3D%22%234f9cf9%22%2F%3E%20%3Cstop%20offset%3D%221%22%20stop-color%3D%22%237c5cff%22%2F%3E%20%3C%2FlinearGradient%3E%20%3C%2Fdefs%3E%20%3Crect%20width%3D%2264%22%20height%3D%2264%22%20rx%3D%2215%22%20fill%3D%22url%28%23bg%29%22%2F%3E%20%3C%21--%20the%20audio%3A%20what%20the%20subtitle%20is%20aligned%20against%20--%3E%20%3Cg%20fill%3D%22%23fff%22%20opacity%3D%22.95%22%3E%20%3Crect%20x%3D%2213%22%20y%3D%2222%22%20width%3D%224%22%20height%3D%2210%22%20rx%3D%222%22%2F%3E%20%3Crect%20x%3D%2221%22%20y%3D%2216%22%20width%3D%224%22%20height%3D%2222%22%20rx%3D%222%22%2F%3E%20%3Crect%20x%3D%2229%22%20y%3D%2220%22%20width%3D%224%22%20height%3D%2214%22%20rx%3D%222%22%2F%3E%20%3Crect%20x%3D%2237%22%20y%3D%2213%22%20width%3D%224%22%20height%3D%2228%22%20rx%3D%222%22%2F%3E%20%3Crect%20x%3D%2245%22%20y%3D%2219%22%20width%3D%224%22%20height%3D%2216%22%20rx%3D%222%22%2F%3E%20%3C%2Fg%3E%20%3C%21--%20the%20subtitle%2C%20sitting%20under%20it%2C%20in%20step%20--%3E%20%3Cg%20fill%3D%22%23fff%22%3E%20%3Crect%20x%3D%2213%22%20y%3D%2245%22%20width%3D%2228%22%20height%3D%225%22%20rx%3D%222.5%22%2F%3E%20%3Crect%20x%3D%2245%22%20y%3D%2245%22%20width%3D%226%22%20height%3D%225%22%20rx%3D%222.5%22%20opacity%3D%22.55%22%2F%3E%20%3C%2Fg%3E%20%3C%2Fsvg%3E"><title>arrsubsync setup</title>
<style>{BASE_CSS}{CENTERED}</style></head><body>
<div class="wrap"><form class="card box" onsubmit="go(event)">
  <div class="logo"><svg class="mark" viewBox="0 0 64 64" width="26" height="26" aria-hidden="true"><defs><linearGradient id="lg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#4f9cf9"/><stop offset="1" stop-color="#7c5cff"/></linearGradient></defs><rect width="64" height="64" rx="15" fill="url(#lg)"/><g fill="#fff" opacity=".95"><rect x="13" y="22" width="4" height="10" rx="2"/><rect x="21" y="16" width="4" height="22" rx="2"/><rect x="29" y="20" width="4" height="14" rx="2"/><rect x="37" y="13" width="4" height="28" rx="2"/><rect x="45" y="19" width="4" height="16" rx="2"/></g><g fill="#fff"><rect x="13" y="45" width="28" height="5" rx="2.5"/><rect x="45" y="45" width="6" height="5" rx="2.5" opacity=".55"/></g></svg> arrsubsync</div>
  <div class="muted" style="font-size:13px">Choose a password for the <b>admin</b> account.
  You can also set <span class="mono">ARRSUBSYNC_PASSWORD</span> in compose instead.</div>
  <label>Password (8 characters or more)</label>
  <input type="password" id="p" autocomplete="new-password" autofocus>
  <div class="err" id="e"></div>
  <button class="btn primary" style="width:100%">Save and continue</button>
</form></div>
<script>
async function go(ev) {{
  ev.preventDefault();
  const body = new FormData();
  body.append('password', document.getElementById('p').value);
  const r = await fetch('/api/setup', {{ method: 'POST', body }});
  if (r.ok) location.reload();
  else document.getElementById('e').textContent = (await r.json()).error || 'Could not save';
}}
</script></body></html>"""

DASHBOARD_CSS = """
.top {
  position: sticky; top: 0; z-index: 5; backdrop-filter: blur(10px);
  background: rgba(13,17,23,.82); border-bottom: 1px solid var(--line);
}
.top-in { max-width: 1240px; margin: 0 auto; padding: 14px 22px; display: flex; align-items: center; gap: 14px; }
.logo { display:flex; align-items:center; gap:10px; font-size: 17px; font-weight: 700; letter-spacing:-.2px; }
.mark { border-radius: 8px; box-shadow: 0 0 18px rgba(79,156,249,.35); }
.spacer { flex: 1; }
.pill { font-size: 12px; padding: 4px 10px; border-radius: 999px; border: 1px solid var(--line); color: var(--muted); }
.pill.on { color: var(--ok); border-color: #1d3a26; background: rgba(63,185,80,.08); }
.pill.off { color: var(--muted); }
main { max-width: 1240px; margin: 0 auto; padding: 22px; }
.grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); }
.stat { padding: 18px; position: relative; overflow: hidden; }
.stat h3 { margin: 0; font-size: 12.5px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: .08em; }
.stat .n { font-size: 34px; font-weight: 700; margin-top: 8px; letter-spacing: -1px; }
.stat .sub { font-size: 12.5px; color: var(--muted); margin-top: 2px; }
.stat.good .n { color: var(--ok); } .stat.warn .n { color: var(--warn); } .stat.bad .n { color: var(--bad); }
.bar { height: 8px; border-radius: 999px; background: var(--panel-2); overflow: hidden; margin-top: 12px; }
.bar > i { display: block; height: 100%; background: linear-gradient(90deg, var(--accent), var(--accent-2)); transition: width .4s; }
.row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.section { margin-top: 22px; padding: 18px; }
.section h2 { margin: 0 0 14px; font-size: 15px; display:flex; align-items:center; gap:8px; }
.tabs { display: flex; gap: 6px; margin: 22px 0 0; flex-wrap: wrap; }
.tab { padding: 8px 14px; border-radius: 10px 10px 0 0; cursor: pointer; color: var(--muted); border: 1px solid transparent; border-bottom: none; }
.tab.active { color: var(--text); background: var(--panel); border-color: var(--line); }
table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
th { text-align: left; color: var(--muted); font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing:.06em; padding: 8px 10px; border-bottom: 1px solid var(--line); }
td { padding: 9px 10px; border-bottom: 1px solid #1b2330; vertical-align: top; }
tr:hover td { background: rgba(255,255,255,.02); }
.tag { font-size: 11px; padding: 3px 8px; border-radius: 999px; white-space: nowrap; font-weight: 600; }
.tag.ok { background: rgba(63,185,80,.12); color: var(--ok); }
.tag.fixed { background: rgba(79,156,249,.12); color: var(--accent); }
.tag.off { background: rgba(210,153,34,.14); color: var(--warn); }
.tag.reverted { background: rgba(248,81,73,.12); color: var(--bad); }
.tag.failed { background: rgba(248,81,73,.2); color: var(--bad); }
.tag.unknown { background: var(--panel-2); color: var(--muted); }
.chart { display: flex; align-items: flex-end; gap: 4px; height: 90px; }
.chart div { flex: 1; background: linear-gradient(180deg, var(--accent), rgba(124,92,255,.35)); border-radius: 4px 4px 0 0; min-height: 2px; position: relative; }
.chart div:hover::after { content: attr(data-n); position: absolute; top: -22px; left: 50%; transform: translateX(-50%); background: var(--panel-2); border: 1px solid var(--line); padding: 2px 6px; border-radius: 6px; font-size: 11px; }
.feed { max-height: 340px; overflow: auto; }
.feed div { padding: 8px 0; border-bottom: 1px solid #1b2330; font-size: 13px; display: flex; gap: 10px; }
.feed .when { color: var(--muted); white-space: nowrap; font-size: 12px; min-width: 84px; }
.feed .what b { font-weight: 600; }
.feed .lvl-warning .what { color: var(--warn); } .feed .lvl-error .what { color: var(--bad); }
.settings { display: grid; gap: 18px; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); }
.field label { display: block; font-size: 12.5px; color: var(--muted); margin-bottom: 6px; }
.field .hint { font-size: 12px; color: var(--muted); margin-top: 5px; }
.switch { display: flex; align-items: center; gap: 10px; cursor: pointer; line-height: 1.35; }
.switch input { flex: 0 0 40px; width: 40px; height: 22px; appearance: none; background: var(--panel-2); border-radius: 999px; position: relative; border: 1px solid var(--line); cursor: pointer; }
.switch input:checked { background: linear-gradient(135deg,var(--accent),var(--accent-2)); border-color: transparent; }
.switch input::after { content:''; position: absolute; width: 16px; height: 16px; border-radius: 50%; background: #fff; top: 2px; left: 2px; transition: .18s; }
.switch input:checked::after { left: 20px; }
.toast { position: fixed; right: 20px; bottom: 20px; background: var(--panel-2); border: 1px solid var(--line); padding: 12px 16px; border-radius: 12px; box-shadow: var(--shadow); opacity: 0; transform: translateY(8px); transition: .25s; }
.toast.show { opacity: 1; transform: none; }
.path { color: var(--muted); font-size: 11.5px; }
.hidden { display: none !important; }
@media (max-width: 640px) { .top-in, main { padding: 14px; } .stat .n { font-size: 27px; } }
"""

DASHBOARD = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:image/svg+xml,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20viewBox%3D%220%200%2064%2064%22%20width%3D%2264%22%20height%3D%2264%22%20role%3D%22img%22%20aria-label%3D%22arrsubsync%22%3E%20%3Cdefs%3E%20%3ClinearGradient%20id%3D%22bg%22%20x1%3D%220%22%20y1%3D%220%22%20x2%3D%221%22%20y2%3D%221%22%3E%20%3Cstop%20offset%3D%220%22%20stop-color%3D%22%234f9cf9%22%2F%3E%20%3Cstop%20offset%3D%221%22%20stop-color%3D%22%237c5cff%22%2F%3E%20%3C%2FlinearGradient%3E%20%3C%2Fdefs%3E%20%3Crect%20width%3D%2264%22%20height%3D%2264%22%20rx%3D%2215%22%20fill%3D%22url%28%23bg%29%22%2F%3E%20%3C%21--%20the%20audio%3A%20what%20the%20subtitle%20is%20aligned%20against%20--%3E%20%3Cg%20fill%3D%22%23fff%22%20opacity%3D%22.95%22%3E%20%3Crect%20x%3D%2213%22%20y%3D%2222%22%20width%3D%224%22%20height%3D%2210%22%20rx%3D%222%22%2F%3E%20%3Crect%20x%3D%2221%22%20y%3D%2216%22%20width%3D%224%22%20height%3D%2222%22%20rx%3D%222%22%2F%3E%20%3Crect%20x%3D%2229%22%20y%3D%2220%22%20width%3D%224%22%20height%3D%2214%22%20rx%3D%222%22%2F%3E%20%3Crect%20x%3D%2237%22%20y%3D%2213%22%20width%3D%224%22%20height%3D%2228%22%20rx%3D%222%22%2F%3E%20%3Crect%20x%3D%2245%22%20y%3D%2219%22%20width%3D%224%22%20height%3D%2216%22%20rx%3D%222%22%2F%3E%20%3C%2Fg%3E%20%3C%21--%20the%20subtitle%2C%20sitting%20under%20it%2C%20in%20step%20--%3E%20%3Cg%20fill%3D%22%23fff%22%3E%20%3Crect%20x%3D%2213%22%20y%3D%2245%22%20width%3D%2228%22%20height%3D%225%22%20rx%3D%222.5%22%2F%3E%20%3Crect%20x%3D%2245%22%20y%3D%2245%22%20width%3D%226%22%20height%3D%225%22%20rx%3D%222.5%22%20opacity%3D%22.55%22%2F%3E%20%3C%2Fg%3E%20%3C%2Fsvg%3E"><title>arrsubsync</title>
<style>__CSS__</style></head><body>
<div class="top"><div class="top-in">
  <div class="logo"><svg class="mark" viewBox="0 0 64 64" width="26" height="26" aria-hidden="true"><defs><linearGradient id="lg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#4f9cf9"/><stop offset="1" stop-color="#7c5cff"/></linearGradient></defs><rect width="64" height="64" rx="15" fill="url(#lg)"/><g fill="#fff" opacity=".95"><rect x="13" y="22" width="4" height="10" rx="2"/><rect x="21" y="16" width="4" height="22" rx="2"/><rect x="29" y="20" width="4" height="14" rx="2"/><rect x="37" y="13" width="4" height="28" rx="2"/><rect x="45" y="19" width="4" height="16" rx="2"/></g><g fill="#fff"><rect x="13" y="45" width="28" height="5" rx="2.5"/><rect x="45" y="45" width="6" height="5" rx="2.5" opacity=".55"/></g></svg> arrsubsync</div>
  <span class="pill" id="health">checking…</span>
  <span class="pill" id="bazarrPill">Bazarr</span>
  <span class="pill" id="autoPill">auto-replace</span>
  <div class="spacer"></div>
  <button class="btn small" onclick="job('scan')">Scan</button>
  <button class="btn small" onclick="job('check')">Check</button>
  <button class="btn small primary" onclick="job('full')">Sync everything</button>
  <button class="btn small danger hidden" id="cancel" onclick="cancelJob()">Stop</button>
  <form method="post" action="/logout"><button class="btn small">Sign out</button></form>
</div></div>

<main>
  <div class="grid">
    <div class="card stat good"><h3>In sync</h3><div class="n" id="sInSync">–</div>
      <div class="sub" id="sInSyncSub">verified against the audio</div></div>
    <div class="card stat"><h3>Corrected</h3><div class="n" id="sFixed">–</div>
      <div class="sub" id="sFixedSub">total drift removed</div></div>
    <div class="card stat bad"><h3>Needs a new subtitle</h3><div class="n" id="sBad">–</div>
      <div class="sub">alass cannot align these</div></div>
    <div class="card stat"><h3>Tracked</h3><div class="n" id="sTotal">–</div>
      <div class="sub" id="sTotalSub">subtitles in the library</div></div>
  </div>

  <div class="card section hidden" id="jobCard">
    <h2><span id="jobKind">job</span> <span class="muted" id="jobCurrent"></span></h2>
    <div class="bar"><i id="jobBar" style="width:0%"></i></div>
    <div class="row muted" style="margin-top:10px; font-size:13px">
      <span id="jobCount">0 / 0</span><span id="jobEta"></span><span id="jobTotals"></span>
    </div>
  </div>

  <div class="tabs">
    <div class="tab active" data-tab="problems" onclick="tab('problems')">Needs attention</div>
    <div class="tab" data-tab="all" onclick="tab('all')">All subtitles</div>
    <div class="tab" data-tab="activity" onclick="tab('activity')">Activity</div>
    <div class="tab" data-tab="settings" onclick="tab('settings')">Settings</div>
  </div>

  <div class="card section" id="tab-problems" style="border-radius:0 var(--radius) var(--radius) var(--radius)">
    <div class="row" style="margin-bottom:12px">
      <button class="btn small" onclick="act('fix')">Re-sync selected</button>
      <button class="btn small primary" onclick="act('heal')">Replace selected via Bazarr</button>
      <button class="btn small" onclick="act('ignore')">Ignore selected</button>
      <div class="spacer"></div>
      <button class="btn small" onclick="job('heal')">Replace all broken</button>
    </div>
    <table><thead><tr>
      <th style="width:28px"><input type="checkbox" onclick="selectAll(this)"></th>
      <th>Subtitle</th><th style="width:110px">Status</th><th style="width:110px">Shift</th><th>What alass said</th>
    </tr></thead><tbody id="rows"><tr><td colspan="5" class="muted">Loading…</td></tr></tbody></table>
  </div>

  <div class="card section hidden" id="tab-all" style="border-radius:0 var(--radius) var(--radius) var(--radius)">
    <div class="row" style="margin-bottom:12px">
      <input type="text" id="search" placeholder="Filter by path…" style="max-width:320px" oninput="loadRows()">
      <select id="statusFilter" style="max-width:180px" onchange="loadRows()">
        <option value="all">Every status</option><option value="ok">In sync</option>
        <option value="fixed">Corrected</option><option value="off">Out of sync</option>
        <option value="reverted">Reverted</option><option value="failed">Failed</option>
        <option value="unknown">Not checked</option>
      </select>
    </div>
    <table><thead><tr><th>Subtitle</th><th style="width:110px">Status</th><th style="width:110px">Shift</th><th style="width:120px">Checked</th></tr></thead>
    <tbody id="allRows"></tbody></table>
  </div>

  <div class="card section hidden" id="tab-activity" style="border-radius:0 var(--radius) var(--radius) var(--radius)">
    <h2>Corrections over the last 14 days</h2>
    <div class="chart" id="chart"></div>
    <h2 style="margin-top:22px">Recent activity</h2>
    <div class="feed" id="feed"></div>
  </div>

  <div class="card section hidden" id="tab-settings" style="border-radius:0 var(--radius) var(--radius) var(--radius)">
    <h2>Automation</h2>
    <div class="settings">
      <div class="field"><label class="switch"><input type="checkbox" id="set_auto_replace"> Replace subtitles that cannot be fixed</label>
        <div class="hint">When alass cannot align a subtitle, blacklist it in Bazarr and fetch another, then sync that. Repeats until it sticks.</div></div>
      <div class="field"><label class="switch"><input type="checkbox" id="set_schedule_enabled"> Run a full sweep on a schedule</label>
        <div class="hint">Indexes the library, corrects what drifted, then heals what it cannot fix.</div></div>
      <div class="field"><label>Hours between sweeps</label><input type="number" id="set_schedule_hours" min="0.25" step="0.25"></div>
      <div class="field"><label>Replacement attempts per subtitle</label><input type="number" id="set_max_replace_attempts" min="1" max="10">
        <div class="hint">How many different subtitles to try before giving up.</div></div>
    </div>

    <h2 style="margin-top:24px">Bazarr</h2>
    <div class="settings">
      <div class="field"><label>Bazarr URL</label><input type="text" id="set_bazarr_url" placeholder="http://bazarr:6767"></div>
      <div class="field"><label>API key</label><input type="password" id="set_bazarr_api_key" placeholder="Settings → General in Bazarr"></div>
      <div class="field" style="align-self:end"><button class="btn" onclick="testBazarr()">Test connection</button>
        <div class="hint" id="bazarrTest"></div></div>
    </div>

    <h2 style="margin-top:24px">Alignment</h2>
    <div class="settings">
      <div class="field"><label class="switch"><input type="checkbox" id="set_verify_after"> Verify every sync and revert if it does not hold</label>
        <div class="hint">A correct sync is a fixed point: re-aligning the result must find nothing to do. Leave this on.</div></div>
      <div class="field"><label>Verify threshold (seconds)</label><input type="number" id="set_verify_threshold" step="0.5" min="0.1"></div>
      <div class="field"><label>Timeout per subtitle (seconds)</label><input type="number" id="set_alass_timeout" min="10"></div>
      <div class="field"><label>Concurrent alass processes</label><input type="number" id="set_max_concurrency" min="1" max="16">
        <div class="hint">alass is CPU-bound. Raise it only if this box is not also transcoding.</div></div>
      <div class="field"><label>Split penalty</label><input type="text" id="set_alass_split_penalty" placeholder="alass default: 7">
        <div class="hint">Higher keeps the timeline in one piece; 5–20 is the useful range.</div></div>
      <div class="field"><label class="switch"><input type="checkbox" id="set_alass_no_split"> Offset only (no splits)</label>
        <div class="hint">Much faster, and cannot scatter cues, but will not absorb drift.</div></div>
    </div>

    <h2 style="margin-top:24px">Library</h2>
    <div class="settings">
      <div class="field"><label>Skip subtitles tagged</label><input type="text" id="set_skip_tags" placeholder="forced"></div>
      <div class="field"><label>Only these languages</label><input type="text" id="set_languages" placeholder="blank = all, e.g. en,nl"></div>
      <div class="field"><label class="switch"><input type="checkbox" id="set_backup_enabled"> Back up every subtitle before changing it</label></div>
      <div class="field"><label>Backup folder</label><input type="text" id="set_backup_dir"></div>
    </div>

    <div class="row" style="margin-top:20px"><button class="btn primary" onclick="saveSettings()">Save settings</button>
      <span class="muted" id="savedNote"></span></div>
  </div>
</main>
<div class="toast" id="toast"></div>
<script>__JS__</script></body></html>"""

DASHBOARD_JS = """
let current = 'problems', timer = null, lastJobRunning = false;

const $ = id => document.getElementById(id);
const fmt = n => (n ?? 0).toLocaleString();

function toast(msg) {
  const t = $('toast'); t.textContent = msg; t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2600);
}

function duration(s) {
  if (s == null) return '';
  if (s < 60) return Math.round(s) + 's';
  if (s < 3600) return Math.floor(s / 60) + 'm ' + Math.round(s % 60) + 's';
  return (s / 3600).toFixed(1) + 'h';
}

function tab(name) {
  current = name;
  if (location.hash.slice(1) !== name) history.replaceState(null, '', '#' + name);
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  ['problems', 'all', 'activity', 'settings'].forEach(t =>
    $('tab-' + t).classList.toggle('hidden', t !== name));
  if (name === 'all' || name === 'problems') loadRows();
  if (name === 'settings') loadSettings();
}

async function refresh() {
  let s;
  try { s = await (await fetch('/api/status')).json(); }
  catch { return; }
  if (s.detail === 'not authenticated') { location.reload(); return; }

  const c = s.counts, st = s.stats;
  $('sInSync').textContent = fmt(c.in_sync);
  $('sInSyncSub').textContent = c.total ? Math.round(c.in_sync / c.total * 100) + '% of the library' : 'nothing indexed yet';
  $('sFixed').textContent = fmt(st.corrected);
  $('sFixedSub').textContent = st.seconds_corrected ? duration(st.seconds_corrected) + ' of drift removed' : 'no corrections yet';
  $('sBad').textContent = fmt(c.needs_replacement);
  $('sTotal').textContent = fmt(c.total);
  $('sTotalSub').textContent = st.replacements ? fmt(st.replacements) + ' replacements fetched' : 'subtitles in the library';

  const h = s.health;
  $('health').textContent = h.alass && h.ffmpeg ? 'alass ready' : 'alass missing';
  $('health').className = 'pill ' + (h.alass && h.ffmpeg ? 'on' : 'off');
  $('bazarrPill').textContent = s.bazarr ? 'Bazarr connected' : 'Bazarr not set up';
  $('bazarrPill').className = 'pill ' + (s.bazarr ? 'on' : 'off');
  $('autoPill').textContent = s.auto_replace ? 'auto-replace on' : 'auto-replace off';
  $('autoPill').className = 'pill ' + (s.auto_replace ? 'on' : 'off');

  const j = s.job;
  if (j && j.running) {
    $('jobCard').classList.remove('hidden'); $('cancel').classList.remove('hidden');
    $('jobKind').textContent = j.kind;
    $('jobCurrent').textContent = j.current || '';
    $('jobBar').style.width = (j.total ? j.done / j.total * 100 : 0) + '%';
    $('jobCount').textContent = fmt(j.done) + ' / ' + fmt(j.total);
    $('jobEta').textContent = j.eta_seconds ? '· about ' + duration(j.eta_seconds) + ' left' : '';
    $('jobTotals').textContent = '· ' + Object.entries(j.totals || {}).map(([k, v]) => v + ' ' + k).join(', ');
    lastJobRunning = true;
  } else {
    $('jobCard').classList.add('hidden'); $('cancel').classList.add('hidden');
    if (lastJobRunning) { lastJobRunning = false; toast('Job finished'); loadRows(); }
  }

  const max = Math.max(1, ...s.history.map(d => d.count));
  $('chart').innerHTML = s.history.map(d =>
    `<div style="height:${Math.round(d.count / max * 100)}%" data-n="${d.count}"></div>`).join('');

  $('feed').innerHTML = s.events.map(e => `
    <div class="lvl-${e.level}">
      <span class="when">${new Date(e.ts * 1000).toLocaleTimeString()}</span>
      <span class="what"><b>${e.kind}</b> ${escapeHtml(e.message)}
        ${e.path ? `<div class="path">${escapeHtml(e.path.split('/').pop())}</div>` : ''}</span>
    </div>`).join('') || '<div class="muted">Nothing yet.</div>';

  clearTimeout(timer);
  timer = setTimeout(refresh, j && j.running ? 2000 : 8000);
}

function escapeHtml(s) {
  return (s || '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

async function loadRows() {
  const all = current === 'all';
  const status = all ? $('statusFilter').value : 'problem';
  const search = all ? $('search').value : '';
  const r = await (await fetch(`/api/subtitles?status=${status}&search=${encodeURIComponent(search)}&limit=300`)).json();
  const body = all ? $('allRows') : $('rows');
  if (!r.items.length) {
    body.innerHTML = `<tr><td colspan="5" class="muted">${all ? 'No subtitles match.' : 'Nothing needs attention. '}</td></tr>`;
    return;
  }
  body.innerHTML = r.items.map(it => all ? `
    <tr><td>${escapeHtml(it.name)}<div class="path">${escapeHtml(it.path)}</div></td>
      <td><span class="tag ${it.status}">${it.status}</span></td>
      <td class="mono">${it.shift_text || ''}</td>
      <td class="muted">${it.checked_at ? new Date(it.checked_at * 1000).toLocaleDateString() : '–'}</td></tr>` : `
    <tr><td><input type="checkbox" class="sel" value="${escapeHtml(it.path)}"></td>
      <td>${escapeHtml(it.name)}<div class="path">${escapeHtml(it.path)}</div></td>
      <td><span class="tag ${it.status}">${it.status}</span></td>
      <td class="mono">${it.shift_text || ''}</td>
      <td class="mono muted">${escapeHtml(it.verify_summary || it.summary || it.note || '')}</td></tr>`).join('');
}

function selectAll(box) { document.querySelectorAll('.sel').forEach(c => c.checked = box.checked); }
function selected() { return [...document.querySelectorAll('.sel:checked')].map(c => c.value); }

async function job(kind, paths) {
  const r = await fetch('/api/jobs/' + kind, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ paths: paths || null })
  });
  if (r.ok) { toast(kind + ' started'); refresh(); }
  else toast((await r.json()).detail || 'Could not start');
}

async function act(what) {
  const paths = selected();
  if (!paths.length) return toast('Select something first');
  if (what === 'ignore') {
    await fetch('/api/subtitles/ignore', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paths })
    });
    toast('Ignored ' + paths.length); loadRows();
  } else job(what, paths);
}

async function cancelJob() { await fetch('/api/jobs/cancel', { method: 'POST' }); toast('Stopping…'); }

const FIELDS = ['auto_replace','schedule_enabled','schedule_hours','max_replace_attempts','bazarr_url',
  'bazarr_api_key','verify_after','verify_threshold','alass_timeout','max_concurrency',
  'alass_split_penalty','alass_no_split','skip_tags','languages','backup_enabled','backup_dir'];

async function loadSettings() {
  const s = await (await fetch('/api/settings')).json();
  FIELDS.forEach(f => {
    const el = $('set_' + f); if (!el) return;
    if (el.type === 'checkbox') el.checked = !!s[f]; else el.value = s[f] ?? '';
  });
}

async function saveSettings() {
  const payload = {};
  FIELDS.forEach(f => {
    const el = $('set_' + f); if (!el) return;
    if (el.type === 'checkbox') payload[f] = el.checked;
    else if (el.type === 'number') payload[f] = el.value === '' ? null : parseFloat(el.value);
    else payload[f] = el.value;
  });
  if (payload.bazarr_api_key === '********') delete payload.bazarr_api_key;
  Object.keys(payload).forEach(k => payload[k] === null && delete payload[k]);
  await fetch('/api/settings', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
  });
  $('savedNote').textContent = 'Saved ' + new Date().toLocaleTimeString();
  toast('Settings saved'); refresh();
}

async function testBazarr() {
  await saveSettings();
  const r = await (await fetch('/api/bazarr/test', { method: 'POST' })).json();
  $('bazarrTest').textContent = r.ok ? ('Connected to Bazarr ' + (r.bazarr_version || '')) : ('Failed: ' + r.error);
  $('bazarrTest').style.color = r.ok ? 'var(--ok)' : 'var(--bad)';
}

tab(['problems','all','activity','settings'].includes(location.hash.slice(1))
      ? location.hash.slice(1) : 'problems');
refresh();
"""

DASHBOARD = DASHBOARD.replace("__CSS__", BASE_CSS + DASHBOARD_CSS).replace("__JS__", DASHBOARD_JS)
