"""Escaped, self-contained static ForgeDeck dashboard. No external resources."""
from __future__ import annotations
import html
import json
from .storage import home,now,stats
from .usage import latest_usage


def esc(value):return html.escape(str(value),quote=True)


def build(con):
    data=stats(con)
    kinds=[(r[0],r[1]) for r in con.execute('SELECT visibility,count(*) FROM repositories GROUP BY visibility')]
    finding_rows=[dict(r) for r in con.execute('SELECT * FROM findings ORDER BY CASE severity WHEN "review" THEN 0 ELSE 1 END,repo,path,rule LIMIT 500')]
    jobs=[dict(r) for r in con.execute('SELECT * FROM jobs ORDER BY id DESC LIMIT 200')]
    latest=con.execute('SELECT * FROM scans ORDER BY id DESC LIMIT 1').fetchone()
    last='No GitHub inventory yet' if not latest else f'GitHub scan: {latest["repos_scanned"]} repos · {latest["workflows_scanned"]} workflows · {latest["errors"]} errors · '+('partial' if latest['partial'] else 'complete')
    stats_html=''.join('<div class="stat"><strong>'+str(v)+'</strong><span>'+esc(k.capitalize())+'</span></div>' for k,v in data.items())
    finding_cards=''.join('''<article class="card finding" data-search="{search}"><div class="meta">{repo} · {path}</div><h3>{rule}</h3><div class="tag">{severity} · {confidence} confidence</div><p>{summary}</p><p class="recommend"><b>Review:</b> {recommend}</p></article>'''.format(**{
      'search':esc(' '.join([r['repo'],r['path'],r['rule'],r['summary']]).lower()),
      'repo':esc(r['repo']),'path':esc(r['path']),'rule':esc(r['rule'].replace('_',' ')),
      'severity':esc(r['severity']),'confidence':esc(r['confidence']),
      'summary':esc(r['summary']),'recommend':esc(r['recommendation'])}) for r in finding_rows)
    job_cards=''.join('<article class="job"><div><b>#'+str(r['id'])+' '+esc(r['kind'])+'</b><span>'+esc(r['status'])+'</span></div><small>'+esc(r['target'])+'</small><p>'+esc(r['outcome'] or 'Awaiting worker')+'</p></article>' for r in jobs)
    usage=latest_usage(con)
    if usage:
        scan=usage['scan']; text_note=('PARTIAL' if scan['partial'] else 'SAMPLED')
        note=f"{text_note} · {scan['source']} · since {scan['window_since']} · {scan['repositories_seen']} repositories · {scan['runs_seen']} runs · {scan['jobs_seen']} jobs · {scan['errors']} errors"
        review_rows=''.join('<tr><td>'+esc(item['repo'])+'</td><td>'+esc(item['workflow_name'])+'</td><td>'+str(item['runs'])+'</td><td>'+str(item['jobs'])+'</td><td>'+str(item['observed_job_wall_minutes'])+'</td><td>'+str(item['workflow_review_findings'])+'</td></tr>' for item in usage['workflows'][:15])
        usage_html='<section class="usage"><div class="titleRow"><h2 class="sectionTitle">Historical Actions observations</h2><span class="label">'+esc(text_note)+'</span></div><div class="bar">'+esc(note)+'</div><div class="tableWrap"><table><thead><tr><th>Repository</th><th>Workflow</th><th>Runs</th><th>Jobs</th><th>Observed job wall-min</th><th>Review flags</th></tr></thead><tbody>'+ (review_rows or '<tr><td colspan="6">No sampled job history</td></tr>')+'</tbody></table></div><p class="caution">Observed job wall-time is <strong>not billed GitHub Actions minutes</strong>. Coverage may be incomplete; no estimated cost savings are claimed.</p></section>'
    else:
        usage_html='<section class="usage"><h2 class="sectionTitle">Historical Actions observations</h2><div class="empty">No historical run scan yet. Use scan-usage for read-only data collection. No minutes can be estimated from workflow files alone.</div></section>'
    page='''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AeroForge · ForgeDeck</title>
<style>
:root{color-scheme:dark;font-family:Inter,ui-sans-serif,system-ui,sans-serif;background:#090f1b;color:#eaf7fe}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 85% 0%,#123048 0,transparent 36%),#090f1b}.container{max-width:1300px;padding:36px 25px 64px;margin:auto}.eyebrow{text-transform:uppercase;letter-spacing:.3em;font-size:11px;color:#67d9e7;font-weight:800}header{display:flex;justify-content:space-between;align-items:end;gap:25px;flex-wrap:wrap;border-bottom:1px solid #24505f;padding-bottom:25px}h1{font-size:clamp(44px,7vw,83px);letter-spacing:-.075em;line-height:1;margin:10px 0 5px}h1 em{color:#43d7e7;font-style:normal}.sub{max-width:650px;color:#a5b8ca;line-height:1.5}.label{border:1px solid #2a6d7a;border-radius:50px;padding:8px 13px;font-size:12px;color:#8edee6}.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin:26px 0}.stat{background:#10212d;border:1px solid #254456;border-radius:16px;padding:20px}.stat strong{font-size:38px;display:block;line-height:1.3}.stat span{color:#84a9b6;text-transform:uppercase;letter-spacing:.15em;font-size:10px}.bar{padding:15px 20px;border-left:3px solid #50cddf;background:#10222c;border-radius:4px;color:#bdd7e0;line-height:1.5}.columns{display:grid;grid-template-columns:minmax(0,2fr) minmax(290px,1fr);gap:25px;margin:35px 0}.sectionTitle{font-size:19px;letter-spacing:-.03em;margin:0 0 12px}.search{display:block;width:100%;border:1px solid #2a4b61;border-radius:11px;background:#101c2c;color:#eaf7fe;padding:13px;margin:10px 0 14px}.cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.card,.job{background:#0e1d2a;border:1px solid #254052;border-radius:14px;padding:18px}.meta{color:#80c9d8;font-size:11px;overflow-wrap:anywhere}.card h3{font-size:15px;margin:10px 0}.tag{display:inline-block;padding:4px 9px;border-radius:25px;background:#244158;color:#acdce7;font-size:11px}.card p{font-size:13px;color:#b3c9d6;line-height:1.55}.card .recommend{color:#bfcbd5;border-top:1px solid #284355;padding-top:12px}.job{margin-bottom:10px;padding:14px}.job>div{display:flex;justify-content:space-between;gap:10px}.job span{color:#71e4b2;font-size:11px}.job small{display:block;margin-top:8px;color:#8da9bc;overflow-wrap:anywhere}.job p{color:#a7b9c9;font-size:12px;margin-bottom:0}.empty{padding:25px;border:1px dashed #376176;border-radius:14px;color:#92b5c8}.foot{font-size:12px;color:#7799aa;border-top:1px solid #234356;padding-top:22px}.usage{margin:26px 0}.tableWrap{overflow-x:auto}table{width:100%;border-collapse:collapse;margin-top:12px;min-width:700px}td,th{text-align:left;padding:12px;border-bottom:1px solid #254052;font-size:12px}th{font-size:10px;text-transform:uppercase;color:#80c9d8;letter-spacing:.09em}td{color:#c2d6e1;overflow-wrap:anywhere}.caution{font-size:12px;color:#aabccc}.titleRow{display:flex;align-items:center;justify-content:space-between;gap:12px}
@media(max-width:950px){.columns{grid-template-columns:1fr}.stats{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:600px){.cards{grid-template-columns:1fr}.container{padding:24px 15px}.stat strong{font-size:27px}}
</style></head><body><div class="container"><header><div><div class="eyebrow">AEROVISTA / NEXUS TECHWORKS / DEVELOPMENT OPERATIONS</div><h1>Aero<em>Forge.</em></h1><div class="sub">Build. Verify. Deliver. Evidence-first CI intelligence with zero automatic GitHub-hosted Actions jobs.</div></div><div class="label">FORGEDECK · LOCAL SNAPSHOT</div></header>
<section class="stats">__STATS__</section><div class="bar">__SCANNOTE__<br>__VISIBILITY__</div>
__USAGE__
<div class="columns"><section><div class="titleRow"><h2 class="sectionTitle">Workflow opportunities</h2><span class="label">Review required</span></div><input class="search" id="search" placeholder="Filter by repository, rule, or path" aria-label="Filter findings"><div class="cards" id="findings">__FINDINGS__</div></section><aside><h2 class="sectionTitle">Local verification queue</h2>__JOBS__<div class="bar">Static checks only: Python AST, JSON parsing, workflow analysis and inventory. Untrusted repository code is never executed.</div></aside></div>
<div class="foot">Generated __DATE__. Audit suggestions are advisory, not completed GitHub Actions changes. No paid runners or production deployments were started by this dashboard.</div></div><script>const q=document.getElementById('search');q.addEventListener('input',()=>{const s=q.value.toLowerCase();for(const c of document.querySelectorAll('.finding'))c.hidden=!c.dataset.search.includes(s)});</script></body></html>'''
    vis=', '.join(str(v)+' '+k for k,v in kinds) or 'No repositories indexed'
    page=page.replace('__USAGE__',usage_html)
    page=page.replace('__STATS__',stats_html).replace('__SCANNOTE__',esc(last)).replace('__VISIBILITY__',esc(vis))
    page=page.replace('__FINDINGS__',finding_cards or '<div class="empty">No findings yet. Import a fixture or scan GitHub to populate this view.</div>')
    page=page.replace('__JOBS__',job_cards or '<div class="empty">No local checks queued.</div>').replace('__DATE__',esc(now()))
    output=home()/'dashboard.html'
    output.write_text(page,encoding='utf-8')
    return output
