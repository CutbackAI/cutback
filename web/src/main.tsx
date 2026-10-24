import React, {useCallback, useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import type {components} from './api.generated';
import {createSDK} from './client.generated';
import './style.css';

type Inventory = components['schemas']['Inventory'];
type RecordData = Record<string, any>;
type RequestAPI = (path: string, options?: RequestInit) => Promise<any>;
const views = ['Overview','Inventory','Costs','Opportunities','Experiments','Changes','Integrations','Security'];
const money = (value: unknown) => value === null || value === undefined ? 'Unknown' :
  new Intl.NumberFormat('en-US', {style:'currency', currency:'USD', maximumFractionDigits:6}).format(Number(value));
const count = (value: unknown) => value === null || value === undefined ? 'Unknown' : Number(value).toLocaleString();
function Empty({children}: {children: React.ReactNode}) {return <p className="empty">{children}</p>}
function JSONView({data}: {data: unknown}) {return <pre tabIndex={0}>{JSON.stringify(data, null, 2)}</pre>}
function Download({data, name}: {data: unknown, name: string}) {
  const save = () => {const blob = new Blob([JSON.stringify(data,null,2)], {type:'application/json'});
    const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href=url;
    a.download=name; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000)};
  return <button onClick={save}>Export JSON</button>;
}

function App() {
  const [token, setToken] = useState('');
  const [connected, setConnected] = useState(false);
  const [projects, setProjects] = useState<RecordData[]>([]);
  const initial = new URLSearchParams(location.search);
  const [project, setProject] = useState(initial.get('project') || '');
  const [view, setView] = useState(views.includes(initial.get('view') || '') ? initial.get('view')! : 'Overview');
  const [period, setPeriod] = useState(initial.get('period') || '7d');
  const [groupBy, setGroupBy] = useState(initial.get('group') || 'model');
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<RecordData>({});
  const [error, setError] = useState(''); const [loading, setLoading] = useState(false);
  const [updated, setUpdated] = useState('');
  const [refresh, setRefresh] = useState(false);
  const requestVersion = useRef(0);
  const api: RequestAPI = useCallback(async(path, options={}) => {
    const response = await fetch('/v1'+path, {...options, headers:{Authorization:'Bearer '+token,
      ...(options.body ? {'Content-Type':'application/json'} : {}), ...options.headers}, cache:'no-store',
      redirect:'error', credentials:'omit'});
    const body = await response.json();
    if (!response.ok) throw new Error(body.code || 'REQUEST_FAILED');
    return body;
  },[token]);
  useEffect(()=> {const params = new URLSearchParams({view, period, group:groupBy});
    if(project) params.set('project', project); history.replaceState(null,'','?'+params);
    setOffset(0);},[view,project,period,groupBy]);
  const load = useCallback(async()=> {
    if(!connected || !project) return;
    const version = ++requestVersion.current; setLoading(true); setError('');
    const base = '/projects/'+encodeURIComponent(project);
    try {
      let result: RecordData = {};
      const from = new Date(Date.now()-Number(period.slice(0,-1))*(period.endsWith('h')?3600000:86400000)).toISOString();
      const usage = '/usage?start='+encodeURIComponent(from)+'&group_by='+encodeURIComponent(groupBy);
      if(view==='Overview') {const [report, health] = await Promise.all([api(base+usage),api(base+'/health')]); result={report,health};}
      if(view==='Inventory') result=await api(base+'/inventory');
      if(view==='Costs') result={report:await api(base+usage)};
      if(view==='Opportunities') result=await api(base+'/opportunities?start='+encodeURIComponent(from));
      if(view==='Experiments') {const [datasets,runs,plans] = await Promise.all([
        api(base+'/datasets?offset='+offset),api(base+'/experiments?offset='+offset),api(base+'/experiments/plans?offset='+offset)]);
        result={datasets,runs,plans};}
      if(view==='Changes') result={recommendations:await api(base+'/recommendations')};
      if(view==='Integrations') result={health:await api(base+'/health')};
      if(view==='Security') {const [policies,prices,audit,keys] = await Promise.all([
        api(base+'/capture-policies'),api(base+'/prices'),api(base+'/audit?offset='+offset),api(base+'/keys')]);
        result={policies,prices,audit,keys};}
      if(version===requestVersion.current) {setData(result);setUpdated(new Date().toLocaleTimeString());}
    } catch(e) {if(version===requestVersion.current) {setError((e as Error).message);setData({});}}
    finally {if(version===requestVersion.current) setLoading(false);}
  },[connected,project,view,period,groupBy,offset,api]);
  useEffect(()=> {setData({});load();},[load]);
  useEffect(()=> {if(!refresh) return; const timer=setInterval(load,30000); return()=>clearInterval(timer)},[refresh,load]);
  async function login(event: React.FormEvent) {event.preventDefault();setError('');
    try {const result=await createSDK(api).projects_v1_projects_get({limit:100});setProjects(result);setConnected(true);
      if(!result.some((p:RecordData)=>p.id===project)) setProject(result[0]?.id || '');}
    catch(e) {setError((e as Error).message)} }
  function logout() {requestVersion.current++;setConnected(false);setToken('');setData({});setProjects([]);setError('');}
  const base='/projects/'+encodeURIComponent(project);
  return <><a className="skip" href="#main">Skip to content</a>
    <header><a className="brand" href="/dashboard/">cutback<span> / evidence before change</span></a>
      {connected && <button onClick={logout}>Disconnect</button>}</header>
    {!connected ? <main id="main" className="login"><p className="eyebrow">CUSTOMER OWNED · OPEN SOURCE</p>
      <h1>Understand your AI spend.<br/>Keep the evidence.</h1>
      <p>Inspect observed usage, test alternatives, and review changes. Source scans alone cannot show historical spend.</p>
      <form onSubmit={login}><label htmlFor="token">Control API key</label>
        <input id="token" type="password" autoComplete="off" value={token} onChange={e=>setToken(e.target.value)} required/>
        <p className="hint">Held in memory for this session. Ingestion keys cannot read the dashboard.</p>
        <button type="submit">Connect to this collector</button></form>
      {error && <p role="alert">{error}</p>}</main> : <div className="shell">
      <aside><label htmlFor="project">Project</label><select id="project" value={project} onChange={e=>setProject(e.target.value)}>
        {projects.map(p=><option key={p.id} value={p.id}>{p.slug}</option>)}</select>
        <nav aria-label="Dashboard">{views.map(name=><button key={name} aria-current={view===name?'page':undefined}
          onClick={()=>setView(name)}>{name}</button>)}</nav><p className="hint">Metadata by default.<br/>No automatic deployments.</p></aside>
      <main id="main"><div className="page-title"><div><p className="eyebrow">PROJECT EVIDENCE</p><h1>{view}</h1></div>
        <button onClick={load} disabled={loading}>Refresh</button></div>
        <div className="toolbar"><label>Period<select value={period} onChange={e=>setPeriod(e.target.value)}>
          {['1h','7d','30d','395d'].map(p=><option key={p}>{p}</option>)}</select></label>
          {view==='Costs' && <label>Group by<select value={groupBy} onChange={e=>setGroupBy(e.target.value)}>
            {['model','provider','workflow','environment','day'].map(g=><option key={g}>{g}</option>)}</select></label>}
          <label className="checkbox"><input type="checkbox" checked={refresh} onChange={e=>setRefresh(e.target.checked)}/>Refresh every 30 seconds</label>
          <span role="status">{loading?'Loading…':updated?'Updated '+updated:''}</span></div>
        {error && <p role="alert" className="notice">{error}. Check the key’s role and project scope.</p>}
        {!project && <Empty>No projects. Create a project with the CLI or control API.</Empty>}
        {view==='Overview' && data.report && <Overview report={data.report} health={data.health}/>}
        {view==='Costs' && data.report && <Costs report={data.report}/>}
        {view==='Inventory' && ('inventory' in data) && <InventoryView inventory={data.inventory}/>}
        {view==='Opportunities' && data.opportunities && <Opportunities items={data.opportunities}/>}
        {view==='Experiments' && data.datasets && <Experiments data={data} api={api} base={base} reload={load}/>}
        {view==='Changes' && data.recommendations && <Changes items={data.recommendations} api={api} base={base} reload={load}/>}
        {view==='Integrations' && data.health && <Integrations health={data.health}/>}
        {view==='Security' && data.policies && <Security data={data} api={api} base={base} reload={load}/>}
        {['Experiments','Security'].includes(view) && <div className="pager"><button disabled={!offset} onClick={()=>setOffset(Math.max(0,offset-50))}>Previous page</button>
          <span>Offset {offset}</span><button disabled={view==='Experiments' ? Math.max(data.datasets?.length||0,data.runs?.length||0,data.plans?.length||0)<50 : (data.audit?.length||0)<50}
            onClick={()=>setOffset(offset+50)}>Next page</button></div>}
      </main></div>}
    <footer>Cutback · modeled prices are estimates, never verified provider invoices.</footer></>;
}

function Overview({report,health}:{report:RecordData,health:RecordData}) {
  const s=report.summary;
  return <><p>Observed period: <time>{report.period.from}</time> to <time>{report.period.to}</time>.</p>
    <div className="metrics"><article><h2>Modeled cost</h2><strong>{money(s.modeled_cost_usd)}</strong><p>Known subtotal {money(s.known_modeled_cost_subtotal_usd)}</p></article>
      <article><h2>Observed attempts</h2><strong>{count(s.observed_attempts)}</strong><p>{count(s.logical_operations)} linked operations</p></article>
      <article><h2>Unpriced attempts</h2><strong>{count(s.unpriced_requests)}</strong><p>Population coverage: unknown</p></article>
      <article><h2>Latency p95</h2><strong>{s.latency_p95_ms===null?'Unknown':Math.round(s.latency_p95_ms)+' ms'}</strong><p>Quality: not verified</p></article></div>
    {!s.observed_attempts && <Empty>No observations in this period. Scan locally, enable a supported adapter, and send authorized traffic to this collector.</Empty>}
    <h2>Evidence and gaps</h2><p className="notice">No savings are production verified here. Cost estimates cover recorded usage and supported rates. Lost or uninstrumented calls are not known.</p>
    <p>Pricing snapshots: {s.price_snapshot_ids?.join(', ') || 'None in this period'}</p>
    <Download data={report} name="cutback-usage.json"/><Integrations health={health}/></>;
}
function InventoryView({inventory}:{inventory:Inventory|null}) {
  if(!inventory) return <Empty>No scan uploaded. Run cutback scan, then cutback scans upload.</Empty>;
  return <><p>Static discovery · actual historical tokens, latency, and cost remain unknown.</p>
    <div className="table-scroll"><table><caption>Discovered candidate call sites</caption><thead><tr>{['Source','Provider / surface','Model','Confidence','Missing evidence'].map(v=><th key={v}>{v}</th>)}</tr></thead>
      <tbody>{inventory.call_sites.map((s:any)=><tr key={s.call_site_id}><td><code>{s.source_file}:{s.line}</code><br/>{s.source_symbol}</td>
        <td>{s.provider}<br/>{s.sdk_surface}</td><td>{s.requested_model || 'Runtime unresolved'}</td><td>{s.confidence}</td><td>{s.unresolved.join(', ') || 'Runtime observation needed'}</td></tr>)}</tbody></table></div>
    <h2>Scanner coverage and warnings</h2><JSONView data={{coverage:inventory.coverage,warnings:inventory.warnings}}/><Download data={inventory} name="cutback-inventory.json"/></>;
}
function Costs({report}:{report:RecordData}) {
  const max=Math.max(0,...report.groups.map((g:RecordData)=>Number(g.known_modeled_cost_subtotal_usd||0)));
  return <><p>{report.period.from} → {report.period.to}. Rates: {report.summary.price_snapshot_ids.join(', ') || 'No priced usage'}.</p>
    <p className="notice">{count(report.summary.unpriced_requests)} attempts have unknown cost. Known subtotal: {money(report.summary.known_modeled_cost_subtotal_usd)}. Invoice total: unknown.</p>
    {!report.groups.length?<Empty>No observations in this period.</Empty>:<><svg className="chart" viewBox={`0 0 800 ${report.groups.slice(0,10).length*40+25}`} role="img" aria-label="Known modeled cost subtotals by group; exact values in the table below">
      {report.groups.slice(0,10).map((g:RecordData,i:number)=><g key={g.key}><text x="0" y={i*40+22}>{g.key.slice(0,25)}</text>
        <rect x="240" y={i*40+8} width={max?Number(g.known_modeled_cost_subtotal_usd||0)/max*480:0} height="22"/></g>)}</svg>
      <div className="table-scroll"><table><caption>Observed usage by {report.group_by}</caption><thead><tr>{['Group','Attempts','Modeled cost USD','Known subtotal USD','Unpriced','Input tokens','Output tokens','p95 ms'].map(t=><th key={t}>{t}</th>)}</tr></thead>
        <tbody>{report.groups.map((g:RecordData)=><tr key={g.key}><th scope="row">{g.key}</th><td>{count(g.observed_attempts)}</td><td>{money(g.modeled_cost_usd)}</td><td>{money(g.known_modeled_cost_subtotal_usd)}</td><td>{count(g.unpriced_requests)}</td><td>{count(g.input_tokens_observed)}</td><td>{count(g.output_tokens_observed)}</td><td>{count(g.latency_p95_ms)}</td></tr>)}</tbody></table></div></>}
      {report.groups_truncated && <p>Top 100 groups shown; narrow the period for more detail.</p>}
      {report.retention?.partial_days_excluded?.length>0 && <p role="alert">Archived partial days are excluded; totals are incomplete.</p>}
      <Download data={report} name="cutback-costs.json"/></>;
}
function Opportunities({items}:{items:RecordData[]}) {
  return <>{!items.length?<Empty>No observed opportunities yet. Record supported traffic and check pricing coverage.</Empty>:items.map((item,i)=><article className="row" key={i}>
    <h2>{item.strategy.replaceAll('_',' ')}</h2><p>{item.target} · tier {item.evidence_tier} · quality {item.quality_status}</p><p>{item.reason.replaceAll('_',' ')}</p>
    <p>Expected savings: unknown. Next: {item.next_action.replaceAll('_',' ')}.</p><details><summary>View source evidence</summary><JSONView data={item.evidence}/></details></article>)}</>;
}
function Mutation({title,endpoint,api,reload,description}:{title:string,endpoint:string,api:RequestAPI,reload:()=>void,description:string}) {
  const [body,setBody]=useState('');const [result,setResult]=useState<any>(null); const [busy,setBusy]=useState(false);
  async function submit(e:React.FormEvent) {e.preventDefault();setBusy(true);try {
    const parsed=JSON.parse(body);setResult(await api(endpoint,{method:'POST',body:JSON.stringify(parsed)}));reload();
  }catch(e){setResult({code:(e as Error).message})}finally{setBusy(false)}}
  return <details><summary>{title}</summary><p>{description}</p><form onSubmit={submit}>
    <label>{title} JSON<textarea rows={6} value={body} onChange={e=>setBody(e.target.value)} required spellCheck={false}/></label>
    <button disabled={busy}>{busy?'Submitting…':title}</button></form>{result&&<JSONView data={result}/>}</details>;
}
function Experiments({data,api,base,reload}:{data:RecordData,api:RequestAPI,base:string,reload:()=>void}) {
  const [detail,setDetail]=useState<any>(null);const [notice,setNotice]=useState('');
  const [approval,setApproval]=useState('');
  async function act(fn:()=>Promise<any>){try{setNotice('');setDetail(await fn());reload()}catch(e){setNotice((e as Error).message)}}
  return <><p className="notice">Datasets require rights approval and encrypted storage. Plans make no provider calls. Each approved budget permits one queued run; execution is disabled by default.</p>
    <h2>Frozen datasets</h2>{!data.datasets.length?<Empty>No authorized datasets imported.</Empty>:data.datasets.map((d:RecordData)=><details key={d.id}><summary>{d.workflow} · {d.id}</summary><JSONView data={d}/></details>)}
    <Mutation title="Import authorized dataset" endpoint={base+'/datasets'} api={api} reload={reload} description="Requires an enabled capture policy. Inputs leave this browser and are encrypted in the configured customer store. Submit only approved data."/>
    <h2>Experiment plans</h2><label htmlFor="approval">Approval reference (required to approve a budget)</label><input id="approval" value={approval} onChange={e=>setApproval(e.target.value)}/>
    {!data.plans.length?<Empty>No frozen plans. Generate a plan with the CLI or submit the versioned API contract below.</Empty>:data.plans.map((p:RecordData)=><article className="row" key={p.id}>
      <h3>{p.specification.workflow} · {p.approval_status}</h3><p>Maximum budget {money(p.specification.budget.max_usd)} · {p.specification.budget.max_requests} requests · hash <code>{p.plan_hash}</code></p>
      <details><summary>Frozen specification</summary><JSONView data={p}/></details>
      {p.approval_status==='pending'&&<button disabled={!approval} onClick={()=>act(()=>api(base+`/experiments/plans/${p.id}/approve`,{method:'POST',body:JSON.stringify({plan_hash:p.plan_hash,approval_ref:approval})}))}>Approve this budget</button>}
      {p.approval_status==='approved'&&<button onClick={()=>act(()=>api(base+'/experiments',{method:'POST',body:JSON.stringify({plan_id:p.id})}))}>Queue approved run</button>}
    </article>)}
    <Mutation title="Create experiment plan" endpoint={base+'/experiments/plans'} api={api} reload={reload} description="Submit dataset_id and specification. Plans freeze models, evaluator, dataset hash, provider allowlist and budget; they do not execute."/>
    <h2>Runs</h2>{!data.runs.length?<Empty>No experiment runs.</Empty>:data.runs.map((r:RecordData)=><article className="row" key={r.id}>
      <h3>{r.status} · {r.id}</h3><p>Reserved {money(r.reserved_usd)} · known spend subtotal {money(r.spent_usd)} · {r.requests} requests</p>
      <button onClick={()=>act(()=>api(base+'/experiments/'+r.id))}>View reproducible evidence</button>
      {['QUEUED','RUNNING'].includes(r.status)&&<button onClick={()=>act(()=>api(base+`/experiments/${r.id}/cancel`,{method:'POST',body:'{}'}))}>Cancel further calls</button>}</article>)}
    {notice&&<p role="alert">{notice}</p>}{detail&&<><h2>Operation result</h2><JSONView data={detail}/><Download data={detail} name="cutback-experiment.json"/></>}</>;
}
function Changes({items,api,base,reload}:{items:RecordData[],api:RequestAPI,base:string,reload:()=>void}) {
  const [message,setMessage]=useState('');
  async function review(id:string,decision:string){try{await api(base+`/recommendations/${id}/review`,{method:'POST',body:JSON.stringify({decision,reason:'dashboard-review'})});reload()}catch(e){setMessage((e as Error).message)}}
  return <><p>Review evidence before generating a patch. Acceptance here approves staging exploration; production traffic requires a separate operator decision.</p>
    {!items.length?<Empty>No recommendations. Complete an approved experiment first.</Empty>:items.map(r=><article className="row" key={r.id}><h2>{r.evidence.candidate?.model || r.id}</h2>
      <p>{r.status} · {r.evidence.status} · tier {r.evidence.evidence_tier} · {r.evidence.sample_n} held-out samples</p><JSONView data={r.evidence}/>
      {r.status==='pending'&&<div className="actions"><button onClick={()=>review(r.id,'accepted_for_staging')}>Accept for staging review</button><button onClick={()=>review(r.id,'rejected')}>Reject</button></div>}
      <p>Generate a source-checked dry-run patch with <code>cutback recommendations patch</code>.</p><Download data={r} name={`recommendation-${r.id}.json`}/></article>)}{message&&<p role="alert">{message}</p>}</>;
}
function Integrations({health}:{health:RecordData}) {
  return <><h2>Instrumentation health</h2><p>Supported adapters: OpenAI 2.29.0 and Anthropic 2.3.0. Uninstrumented traffic and population coverage remain unknown.</p>
    {!health.instances?.length?<Empty>No SDK health reports received. Check endpoint, scoped key, SDK initialization, and flush behavior.</Empty>:<div className="table-scroll"><table><caption>Reported SDK instances (counters since process startup)</caption><thead><tr><th>Instance</th><th>Last report</th><th>Observed</th><th>Exported</th><th>Dropped</th><th>Queue</th></tr></thead><tbody>
      {health.instances.map((h:RecordData)=><tr key={h.instance_id}><td><code>{h.instance_id}</code></td><td>{h.updated_at}</td><td>{count(h.counters.observed)}</td><td>{count(h.counters.exported)}</td><td>{count(h.counters.dropped)}</td><td>{count(h.counters.queue_depth)}</td></tr>)}</tbody></table></div>}</>;
}
function Security({data,api,base,reload}:{data:RecordData,api:RequestAPI,base:string,reload:()=>void}) {
  const [message,setMessage]=useState('');
  async function revoke(path:string){try{await api(path,{method:'DELETE'});reload()}catch(e){setMessage((e as Error).message)}}
  return <><p className="notice">Admin scope required. Content capture is denied unless both server storage and an explicit scoped policy are enabled. This browser does not store provider keys.</p>
    <h2>Capture policies</h2>{!data.policies.length?<Empty>No policies. Payload capture is disabled.</Empty>:data.policies.map((p:RecordData)=><details key={p.id}><summary>{p.id} · {p.enabled?'Enabled':'Revoked'}</summary><JSONView data={p}/>{p.enabled&&<button onClick={()=>revoke(base+'/capture-policies/'+p.id)}>Revoke capture policy</button>}</details>)}
    <Mutation title="Create scoped capture policy" api={api} endpoint={base+'/capture-policies'} reload={reload} description="Requires data owner authorization, provenance, environment/workflow/vendor allowlists, expiry, retention and quota. Redaction cannot guarantee removal of all sensitive content."/>
    <h2>Project keys</h2>{!data.keys.length?<Empty>No scoped keys.</Empty>:data.keys.map((k:RecordData)=><article className="row" key={k.id}><p>{k.role} · {k.id} · expires {k.expires_at}</p><button disabled={!!k.revoked_at} onClick={()=>revoke(base+'/keys/'+k.id)}>Revoke key</button></article>)}
    <p>Issue a key with <code>cutback keys create --output protected-file.json</code>; tokens are shown only once and should enter a secret store.</p>
    <h2>Pricing and overrides</h2><details><summary>Recorded catalog and contract rates</summary><JSONView data={data.prices}/></details>
    <Mutation title="Add contract rate" api={api} endpoint={base+'/prices/overrides'} reload={reload} description="Rates are USD per million tokens, effective dated, and scoped to exact provider/model/region/tier. Historical valuations remain unchanged."/>
    <h2>Audit trail</h2>{!data.audit.length?<Empty>No audit records on this page.</Empty>:<JSONView data={data.audit}/>}
    <p>Retention defaults: raw events 30 days, daily summaries 395 days, approved payloads 7 days. Configure and run the customer-owned worker; consult the backup and deletion playbook before destructive operations.</p>
    {message&&<p role="alert">{message}</p>}</>;
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
