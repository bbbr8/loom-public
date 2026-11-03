const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/app.js', 'utf8');
function context(fetch) {
 const controls = [{disabled:false}, {disabled:false}];
 const ctx = vm.createContext({fetch, AbortController, setTimeout, clearTimeout, document:{querySelectorAll:()=>controls}, status:()=>{}});
 vm.runInContext('let requestBusy = false;\n' + source.slice(source.indexOf('function setRequestBusy'), source.indexOf('function openRecord')), ctx);
 vm.runInContext(source.slice(source.indexOf('async function fetchJson'), source.indexOf('function showJob')), ctx);
 return {ctx, controls};
}
test('busy state disables and restores every request control', () => {
 const {ctx,controls}=context();
 vm.runInContext('setRequestBusy(true)',ctx);
 assert.ok(controls.every(c=>c.disabled));
 vm.runInContext('setRequestBusy(false)',ctx);
 assert.ok(controls.every(c=>!c.disabled));
});
test('overlapping request exits before touching request or job state', async () => {
 const {ctx}=context();
 vm.runInContext(source.slice(source.indexOf('async function executeRequest'), source.indexOf('$("workbench").addEventListener')),ctx);
 vm.runInContext('setRequestBusy(true)',ctx);
 await vm.runInContext('executeRequest({operation:"graph_explain"})',ctx);
});
test('non-JSON upstream failure has actionable error', async () => {
 const {ctx}=context(async()=>({status:502,json:async()=>{throw new SyntaxError('bad JSON')}}));
 await assert.rejects(vm.runInContext('fetchJson("/v1/jobs", "key")',ctx), /HTTP 502.*retained job/);
});
test('null response is handled without property access crash', async () => {
 const {ctx}=context(async()=>({status:200,ok:true,json:async()=>null}));
 await assert.rejects(vm.runInContext('fetchJson("/v1/jobs", "key")',ctx), /could not complete/);
});
test('successful response remains intact', async () => {
 const data={ok:true,result:{hits:[{id:'source-1'}]}};
 const {ctx}=context(async()=>({status:200,ok:true,json:async()=>data}));
 assert.equal(await vm.runInContext('fetchJson("/v1/jobs", "key")',ctx),data);
});
