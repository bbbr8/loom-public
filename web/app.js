"use strict";
const $ = id => document.getElementById(id);
let examples = [], lastResult = null, lastRequest = null, jobsAvailable = false, retainedJob = null;
let studioReturn = null;
let requestBusy = false;
function setRequestBusy(value) {
  requestBusy = value;
  document.querySelectorAll('[data-studio], .record-open, #run, #next-page, #resume-job, #studio-back, #example').forEach(button => { button.disabled = value; });
}
function openRecord(id) {
  const graph = lastRequest?.graph || $("studio-graph").value.trim();
  if (lastResult && !lastResult.node) studioReturn = { result:lastResult, request:lastRequest };
  executeRequest({operation:"graph_inspect_record", graph, query:id});
}
function recordButton(id, label = "Open record and sources") {
  const b = element("button", label, "secondary record-open"); b.type = "button"; b.addEventListener("click", () => openRecord(id)); return b;
}
function sourceCard(card) {
  const article = element("article", "", "hit evidence-card");
  article.append(element("h3", card.title || card.label || card.id));
  article.append(element("p", card.source_tier || card.status || "Source extract", "badge"));
  article.append(element("blockquote", card.excerpt ?? card.text ?? ""));
  article.append(element("small", card.locator || card.id));
  const headers=card.headers || card.data?.headers;
  if(headers) { const selected=Object.entries(headers).filter(([k])=>["from","to","cc","bcc","date","subject","message-id","sent-date","x-reconstruction-note"].includes(k.toLowerCase())); article.append(table(["Outer message field","Recorded value"],selected.map(([k,v])=>[k,Array.isArray(v)?v.join("; "):String(v)]))); }
  if(card.reconstruction_note || card.data?.reconstruction_note) article.append(element("p",card.reconstruction_note || card.data.reconstruction_note,"panel-note"));
  if(card.speech_scope || card.data?.speech_scope) article.append(element("p",card.speech_scope || card.data.speech_scope,"panel-note"));
  if (card.open_dimensions?.length) article.append(element("p", "Open context: " + card.open_dimensions.join(", "), "panel-note"));
  const normalized=card.data?.normalization;
  if(normalized?.fields?.length) {
    const d=document.createElement("details");d.append(element("summary","Compare original and normalized fields"));
    const scroll=element("div","","normalization-scroll"); scroll.append(table(["Field / role","Kind / unit","Original","Normalized","State"],normalized.fields.map(f=>[
      f.name+(f.role?" · "+f.role:""),f.value.kind+(f.value.unit?" · "+f.value.unit:f.value.currency?" · "+f.value.currency:""),typeof f.value.raw==="string"?f.value.raw:JSON.stringify(f.value.raw),
      f.value.canonical===null?"Open":typeof f.value.canonical==="object"?JSON.stringify(f.value.canonical):f.value.canonical,f.value.state]))); d.append(scroll);
    d.append(element("p","The Original column retains the source values. Profile choices and source dependencies remain in the structured result.","panel-note"));article.append(d);
    if(card.data?.source_node) article.append(recordButton(card.data.source_node,"Open original record"));
  }
  if (card.clocks && Object.keys(card.clocks).length) article.append(table(["Clock", "Recorded value"],Object.entries(card.clocks).map(([k,v])=>[k,JSON.stringify(v)])));
  article.append(recordButton(card.id)); return article;
}
function graphView(result) {
  const center = result.node, neighbors = (result.neighbors || []).slice(0,8), all = [center,...neighbors];
  const svg = document.createElementNS("http://www.w3.org/2000/svg","svg"); svg.setAttribute("viewBox","0 0 720 360"); svg.setAttribute("class","evidence-graph"); svg.setAttribute("role","img"); svg.setAttribute("aria-label","Selected record and source-linked relationships. Full details follow below.");
  const positions = new Map([[center.id,[360,180]]]);
  neighbors.forEach((n,i)=>{const a=2*Math.PI*i/Math.max(1,neighbors.length);positions.set(n.id,[360+240*Math.cos(a),180+125*Math.sin(a)]);});
  (result.edges || []).forEach(e=>{const a=positions.get(e.subject),b=positions.get(e.object);if(!a||!b)return;const line=document.createElementNS(svg.namespaceURI,"line");["x1","y1","x2","y2"].forEach((k,i)=>line.setAttribute(k,[...a,...b][i]));line.setAttribute("class",e.status==="candidate"?"candidate-edge":"reviewed-edge");svg.append(line);});
  all.forEach(n=>{const [x,y]=positions.get(n.id),g=document.createElementNS(svg.namespaceURI,"g"),circle=document.createElementNS(svg.namespaceURI,"circle"),label=document.createElementNS(svg.namespaceURI,"text");circle.setAttribute("cx",x);circle.setAttribute("cy",y);circle.setAttribute("r",n===center?18:10);label.setAttribute("x",x);label.setAttribute("y",y+32);label.setAttribute("text-anchor","middle");label.textContent=n.label.slice(0,25);g.append(circle,label);svg.append(g);});
  $("result").append(svg);
}
function element(tag, text, className) { const e = document.createElement(tag); e.textContent = text; if (className) e.className = className; return e; }
function status(text, error = false) { $("status").textContent = text; $("status").className = error ? "error" : ""; }
function stat(label, value) { const e = element("div", "", "stat"); e.append(element("strong", String(value)), element("span", label)); $("summary").append(e); }
function table(headers, rows) { const t = document.createElement("table"), h = document.createElement("tr"); headers.forEach(x => h.append(element("th", x))); const head = document.createElement("thead"); head.append(h); t.append(head); const body = document.createElement("tbody"); rows.forEach(row => { const tr = document.createElement("tr"); row.forEach(v => tr.append(element("td", String(v)))); body.append(tr); }); t.append(body); return t; }
function render(result) {
  $("summary").replaceChildren(); $("result").replaceChildren();
  const searches = result.searches || [], hits = result.hits || searches.flatMap(x => x.hits || []);
  const bundles = result.bundles || result.planning?.bundles;
  $("studio-back").hidden = !(result.node && studioReturn);
  if (result.sentences && result.timeline) {
    stat("Mapped statements",result.sentences.length);stat("Source references",result.source_ids.length);stat("Stale annotations",result.stale_annotations.length);
    $("result").append(element("h2",result.episode));
    result.sentences.forEach((s,i)=>{
      const a=element("article","","hit evidence-card");a.append(element("p",s.text,"episode-sentence"),element("small","["+(i+1)+"] · Named source annotation"));
      const details=document.createElement("details");details.append(element("summary","Read exact supporting text"));
      s.quotes.forEach(q=>{details.append(element("blockquote",q.quote),element("small",q.locator || q.node_id),recordButton(q.node_id));});
      if(s.finding) details.append(element("p","Retained finding: "+s.finding.status));
      a.append(details,recordButton(s.annotation_id,"Inspect annotation and dependencies"));$("result").append(a);
    });
    $("result").append(element("h3","Dates and recorded knowledge"));
    $("result").append(table(["Clock","Date or interval","Actor","Record"],result.timeline.map(t=>[t.clock,t.at?.join(" → ") || "Date open",t.actor || "",t.id])));
    $("result").append(table(["Actor","Recorded state","Version","Scope"],result.knowledge.map(k=>[k.actor,k.state,k.version || "Open",k.scope || "Open"])));
    if(result.comparisons.length) $("result").append(table(["Comparison","Open coordinates","Differing coordinates"],result.comparisons.map(c=>[c.state,c.open_axes?.join(", ") || "",c.differing_axes?.join(", ") || ""])));
    if(result.lifecycle.lanes.length) result.lifecycle.lanes.forEach(l=>{
      $("result").append(element("h3","Money stages · "+l.coordinates.obligation),table(["Stage","Exact value","Unit","Instrument"],l.observations.map(o=>[o.stage,o.value,o.unit,o.instrument])),element("p","Stages awaiting mapping or records: "+l.open_stages.join(", "),"panel-note"));
    });
    $("result").append(element("p","Source-linked draft. Independent reader evaluation remains open. Mapping gaps lead to existing-store and native-context checks.","panel-note"));
  } else if (result.cards) {
    stat("Selected records", result.cards.length); stat("Source origins", result.source_origin_count); stat("Open context fields", result.open_bridges.length);
    $("result").append(element("p", result.orientation));
    result.cards.forEach(card => $("result").append(sourceCard(card)));
    $("result").append(element("p", result.reader_note, "panel-note"));
  } else if (result.node) {
    stat("Record state", result.state); stat("Versions", result.versions.length); graphView(result);
    $("result").append(sourceCard(result.node));
    const sourceTitle=element("h3","Sources and related records"); $("result").append(sourceTitle);
    [...result.sources,...result.neighbors].filter((n,i,a)=>a.findIndex(x=>x.id===n.id)===i).forEach(n=>$("result").append(recordButton(n.id,n.label)));
    $("result").append(table(["Relationship","State","Review"],result.edges.map(e=>[e.predicate,e.status,e.data?.review_state || "Supplied annotation"])));
    $("result").append(table(["Version","Commit","Active"],result.versions.map(v=>[v.revision,v.commit,v.active])));
    if(result.node.stale_reasons?.length) $("result").append(element("p","Stale reasons: "+result.node.stale_reasons.join(", ")));
  } else if (result.actions) {
    stat("Proposed record tests",result.actions.length);
    result.actions.forEach(a=>{const card=element("article","","hit");card.append(element("h3",a.question),element("p",a.record_to_seek),element("p","Would support: "+a.confirming_result),element("p","Would weaken: "+a.weakening_result),recordButton(a.node_id));$("result").append(card);});
  } else if (hits.length) {
    stat("Source hits", hits.length);
    if (result.graph_revision !== undefined) stat("Graph revision", result.graph_revision);
    hits.slice(0, 12).forEach(h => { const card = element("article", "", "hit"); card.append(element("h3", h.title || h.label || h.id), element("p", typeof h.snippet === "string" ? h.snippet : h.snippet?.text || h.text || ""), element("small", h.locator || h.id)); if(lastRequest?.graph)card.append(recordButton(h.id)); $("result").append(card); });
  } else if (bundles) {
    stat("Record bundles", bundles.length);
    if (bundles.length) { stat("Best gain", bundles[0].guaranteed_gain); $("result").append(table(["Records", "Cost", "Guaranteed gain", "Synergy"], bundles.slice(0, 12).map(b => [b.records.join(" + "), b.cost, b.guaranteed_gain, b.synergy]))); }
    $("result").append(element("p", "Gain is the reduction in the selected model metric’s range, in that metric’s unit.", "panel-note"));
  } else if (result.nodes) {
    stat("Nodes", result.pagination?.total_nodes ?? result.nodes.length); stat("Relationships", result.pagination?.total_edges ?? (result.edges || []).length); stat("Revision", result.revision);
    if (result.pagination) $("result").append(element("p", `Showing ${result.pagination.returned} records, starting at ${result.pagination.offset + 1}.`, "panel-note"));
    $("result").append(table(["Record", "Type", "State"], result.nodes.slice(0, 30).map(n => [n.label || n.id, n.type, n.effective_status || n.status])));
  } else if (result.metrics) {
    stat("Feasible states", result.feasible_states ?? "—");
    $("result").append(table(["Metric", "Minimum", "Maximum", "Unit"], Object.entries(result.metrics).map(([id, m]) => [id, m.min, m.max, m.unit])));
  } else {
    const pre = element("pre", JSON.stringify(result, null, 2)); $("result").append(pre);
  }
  const evolution = result.evolution || result.intake?.evolution || (result.evolution_complete !== undefined ? result : null);
  if (evolution?.evolution_complete === false) $("result").prepend(element("p", `Graph growth has more work: ${evolution.remaining?.nodes ?? "unknown"} nodes and ${evolution.remaining?.edges ?? "unknown"} links remain. Run graph_evolve with the returned continuation to continue.`, "panel-note"));
  $("next-page").hidden = !result.pagination?.next_cursor;
  $("download").textContent = result.pagination?.next_cursor || result.pagination?.offset ? "Export this page" : "Export JSON";
  $("raw").textContent = JSON.stringify(result, null, 2); $("raw-details").hidden = false;
}
function loadExample() { const ex = examples[Number($("example").value)]; $("request-title").textContent = ex.title; $("description").textContent = ex.description; $("request").value = JSON.stringify(ex.request, null, 2); status("Example loaded. All supplied example records are synthetic."); }
$("example").addEventListener("change", loadExample);
document.querySelectorAll("[data-studio]").forEach(b=>b.addEventListener("click",()=>{
  const graph=$("studio-graph").value.trim(), query=$("studio-query").value.trim();
  if(!/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(graph)||!query){status("Enter a project graph and a question.",true);return;}
  studioReturn=null; executeRequest({operation:b.dataset.studio,graph,query});
}));
$("studio-back").addEventListener("click",()=>{if(studioReturn){lastRequest=studioReturn.request;lastResult=studioReturn.result;render(lastResult);}});
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
async function fetchJson(url, key, options = {}) {
  const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 20000);
  try {
    const response = await fetch(url, { ...options, headers: { "Content-Type": "application/json", "Authorization": "Bearer " + key, ...(options.headers || {}) }, signal: controller.signal });
    let data;
    try { data = await response.json(); }
    catch { throw new Error(`The service returned an unreadable response (HTTP ${response.status}). Check the retained job before retrying.`); }
    if (!response.ok || !data || !data.ok) { const messages = {401: "Access key was rejected.", 409: "This job key already belongs to a different request.", 422: "Request could not be evaluated. Inspect its schema and coverage; use pagination for large results.", 429: "Loom’s queue or request limit is full. Try again shortly.", 408: "Analysis reached its resource budget. Submit a queued job for expensive work."}; throw new Error(messages[response.status] || data?.error || "The service could not complete this request."); }
    return data;
  } finally { clearTimeout(timeout); }
}
function showJob(job) {
  retainedJob = { id: job.id, status_url: job.status_url, terminal:["failed","interrupted","succeeded"].includes(job.state) };
  $("job-controls").hidden = false; $("job-note").textContent = `Job ${job.id.slice(0, 12)} · ${job.state}`;
}
async function pollJob(job, key) {
  showJob(job);
  for (let n = 0; n < 180; n++) {
    if (job.state === "succeeded") { $("job-controls").hidden = true; retainedJob = null; return job.result; }
    if (["failed", "interrupted"].includes(job.state)) throw new Error(`Job ${job.state}: ${job.error || "inspect the retained status before resubmitting"}.`);
    status(job.state === "queued" ? "Queued · your analysis is retained and waiting for a worker." : "Running · you can check this retained job again without repeating the request.");
    await wait(1000);
    job = (await fetchJson(job.status_url, key)).job; showJob(job);
  }
  throw new Error("This job is still retained. Use Check retained job to resume watching it.");
}
async function executeRequest(request) {
  if (requestBusy) { status("An analysis is already running. Wait for it to finish.", true); return; }
  if (retainedJob && !retainedJob.terminal) { status("A request identity is already retained. Check that job before submitting another request.", true); return; }
  const key = $("key").value.trim();
  if (key.length < 32) { status("Enter the access key configured for this Loom service.", true); $("key").focus(); return; }
  setRequestBusy(true); $("download").disabled = true; status("Submitting analysis…");
  lastRequest = structuredClone(request);
  try {
    let result;
    if (jobsAvailable) {
      const id = crypto.randomUUID();
      // Keep this identity in page memory before sending. A transport interruption
      // must not cause a graph mutation to be submitted under a different identity.
      retainedJob = { submission: structuredClone(request), idempotency: id };
      const data = await fetchJson("/v1/jobs", key, { method:"POST", headers:{"Idempotency-Key": id}, body:JSON.stringify(request) });
      result = await pollJob(data.job, key);
    } else result = (await fetchJson("/v1/analyze", key, {method:"POST", body:JSON.stringify(request)})).result;
    lastResult = result; render(result); $("download").disabled = false;
    status("Returned · " + (result.state || result.status || "complete") + ". Inspect coverage and assumptions in the result.");
  } catch (error) {
    if (retainedJob) { $("job-controls").hidden = false; $("job-note").textContent = "Request identity retained in this page. Check it before submitting again."; }
    status(error.name === "AbortError" ? "Connection timed out. Check the retained job before repeating a mutation." : error.message, true);
  } finally { setRequestBusy(false); }
}
$("workbench").addEventListener("submit", async event => {
  event.preventDefault(); let request;
  try { request = JSON.parse($("request").value); } catch { status("Request JSON is incomplete or invalid. Check the editor and try again.", true); return; }
  await executeRequest(request);
});
$("next-page").addEventListener("click", () => { if (lastResult?.pagination?.next_cursor && lastRequest) executeRequest({...lastRequest, options:{...(lastRequest.options || {}), cursor:lastResult.pagination.next_cursor}}); });
$("resume-job").addEventListener("click", async () => {
  if (!retainedJob || requestBusy) return;
  setRequestBusy(true);
  try {
    const key = $("key").value.trim();
    let job;
    if (retainedJob.submission) job = (await fetchJson("/v1/jobs", key, {method:"POST", headers:{"Idempotency-Key":retainedJob.idempotency}, body:JSON.stringify(retainedJob.submission)})).job;
    else job = (await fetchJson(retainedJob.status_url, key)).job;
    lastResult = await pollJob(job, key); render(lastResult); $("download").disabled = false; status("Retained job completed.");
  } catch(error) { status(error.message, true); }
  finally { setRequestBusy(false); }
});
$("download").addEventListener("click", () => { if (!lastResult) return; const url = URL.createObjectURL(new Blob([JSON.stringify(lastResult, null, 2)], {type:"application/json"})); const a = document.createElement("a"); a.href = url; a.download = "loom-result.json"; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); });
Promise.all([fetch("/examples.json").then(r => r.json()), fetch("/health").then(r => r.json())]).then(([items, health]) => {
  examples = items; items.forEach((ex, i) => { const o = element("option", ex.title); o.value = String(i); $("example").append(o); }); loadExample();
  $("health").textContent = "Engine ready"; $("connection").classList.add("ready"); $("storage").textContent = health.graph_storage_configured ? "Graph storage: " + health.storage_mode : "Graph storage is disabled on this service.";
  jobsAvailable = health.jobs_configured === true;
}).catch(() => { $("health").textContent = "Service unavailable"; status("Could not load the workbench. Reload after checking the service.", true); });
