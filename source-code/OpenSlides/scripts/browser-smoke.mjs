import {createRequire} from 'node:module';
import {resolve} from 'node:path';
import assert from 'node:assert/strict';
const {chromium}=createRequire(resolve(process.env.PLAYWRIGHT_PACKAGE_JSON||'.qa/package.json'))('playwright');
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1500,height:960}});const errors=[];
page.on('pageerror',e=>errors.push(e.message));
try{
 await page.goto('http://127.0.0.1:5173');
 await page.getByText('OpenSlides',{exact:true}).waitFor();assert.equal(await page.title(),'OpenSlides');
 await page.getByRole('button',{name:'New blank deck',exact:true}).click();
 const rail=page.getByRole('navigation',{name:'Creation tools'});await rail.waitFor();
 await page.getByRole('button',{name:'Add Heart',exact:true}).click();
 assert.equal(await page.evaluate(async()=>{const {useEditor}=await import('/src/store/editor.ts');return useEditor.getState().deck.slides[0].elements.filter(e=>e.name==='Heart').length;}),1);
 await rail.getByRole('button',{name:'Text',exact:true}).click();
 await page.getByLabel('Search fonts').fill('DejaVu');
 assert.equal(await page.locator('.studio-fonts button').count(),3);
 await rail.getByRole('button',{name:'Themes',exact:true}).click();
 assert.equal(await page.locator('.studio-theme').count(),20);
 await page.locator('.studio-theme').nth(9).click();
 await page.getByRole('button',{name:'Apply to deck',exact:true}).click();
 await rail.getByRole('button',{name:'Agents',exact:true}).click();
 await page.getByRole('button',{name:'New agent',exact:true}).click();
 await page.getByLabel('Agent name').fill('Hackathon helper');
 await page.getByLabel('Agent instructions').fill('Create simple blue graphics for our presentation.');
 await page.getByRole('button',{name:'Save agent',exact:true}).click();
 assert.equal(await page.getByText('Hackathon helper',{exact:true}).count(),1);
 await rail.getByRole('button',{name:'Ask Claudia',exact:true}).click();
 await page.getByRole('button',{name:'AI settings',exact:true}).click();
 await page.getByLabel('Anthropic API key').fill('mock-key-for-ui-test');
 await page.getByRole('button',{name:'Save settings',exact:true}).click();
 await page.route('https://api.anthropic.com/v1/messages',async route=>{
  const data=route.request().postDataJSON();assert.equal(data.model,'claude-opus-5-5');assert.equal(route.request().headers()['anthropic-version'],'2026-10-21');
  await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({model:data.model,stop_reason:'end_turn',usage:{input_tokens:100,output_tokens:60},content:[{type:'text',text:JSON.stringify({summary:'A violet circle',operations:[{tool:'element',name:'Violet circle',svg:'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle cx="50" cy="50" r="45" fill="#7054d7"/></svg>'}]})}]})});
 });
 await page.getByLabel('Describe your idea').fill('A violet circle');
 await page.getByRole('button',{name:'Generate with Claudia',exact:true}).click();
 await page.getByRole('button',{name:'Apply changes',exact:true}).waitFor();
 await page.getByRole('button',{name:'Save graphics for reuse',exact:true}).click();
 await page.getByRole('button',{name:'Apply changes',exact:true}).click();
 const applied=await page.evaluate(async()=>{
  const {useEditor,undo,redo}=await import('/src/store/editor.ts');
  const count=()=>useEditor.getState().deck.slides[0].elements.filter(e=>e.name==='Violet circle').length;
  const after=count();undo();const undone=count();redo();return {after,undone,redone:count(),storage:JSON.stringify({...localStorage})};
 });
 assert.deepEqual([applied.after,applied.undone,applied.redone],[1,0,1]);assert.ok(!applied.storage.includes('mock-key-for-ui-test'));
 const checks=await page.evaluate(async()=>{
  const {safeSvg}=await import('/src/studio/svg.ts');
  const bad=['<svg viewBox="0 0 100 100"><script>alert(1)</script></svg>','<svg viewBox="0 0 100 100" onload="alert(1)"/>','<svg viewBox="0 0 100 100"><image href="https://example.com/a"/></svg>','<svg viewBox="0 0 100 100"><rect style="fill:red"/></svg>','<!DOCTYPE svg><svg viewBox="0 0 100 100"/>'];
  const blocked=bad.map(svg=>{try{safeSvg(svg);return false;}catch{return true;}});
  const {useEditor,mutate}=await import('/src/store/editor.ts');const {preparePlan,applyPreparedPlan}=await import('/src/ai/apply.ts');
  const source=useEditor.getState().deck,next=preparePlan({summary:'Notes',operations:[{tool:'notes',text:'Example notes'}]},source,source.slides[0].id);
  mutate(d=>{d.title='Edited after proposal';});let stale=false;try{applyPreparedPlan(source,next);}catch{stale=true;}
  const {TEMPLATES,createFromTemplate}=await import('/src/model/templates.ts');
  return {blocked,stale,templates:TEMPLATES.map(t=>createFromTemplate(t.id).slides.length)};
 });
 assert.ok(checks.blocked.every(Boolean));assert.ok(checks.stale);assert.ok(checks.templates.every(n=>n>0));
 await rail.getByRole('button',{name:'Elements',exact:true}).click();
 await page.getByText('Your generated elements',{exact:true}).waitFor();
 await page.screenshot({path:'studio-smoke.png',fullPage:true});
 const exports=await page.evaluate(async()=>{
  const {useEditor}=await import('/src/store/editor.ts');const {buildPptx}=await import('/src/io/export.tsx');const {default:JSZip}=await import('/node_modules/.vite/deps/jszip.js');
  const data=await (await buildPptx(useEditor.getState().deck)).write({outputType:'arraybuffer'});const zip=await JSZip.loadAsync(data);
  const {htmlToText}=await import('/src/model/resolve.ts');window.__injected=false;htmlToText('<img src="bad" onerror="window.__injected=true"><p>Safe</p>');await new Promise(r=>setTimeout(r,100));
  return {bytes:data.byteLength,media:Object.keys(zip.files).filter(f=>f.startsWith('ppt/media/')).length,slides:Object.keys(zip.files).filter(f=>/^ppt\/slides\/slide[0-9]+\.xml$/.test(f)).length,injected:window.__injected};
 });assert.ok(exports.bytes>5000);assert.ok(exports.media>=2);assert.equal(exports.slides,1);assert.equal(exports.injected,false);
 await page.evaluate(async()=>{const {mutate,useEditor}=await import('/src/store/editor.ts');const {createFromTemplate}=await import('/src/model/templates.ts');const d=createFromTemplate('creative');mutate(deck=>{deck.title=d.title;deck.theme=d.theme;deck.layouts=d.layouts;deck.slides=d.slides;});useEditor.setState({slideId:d.slides[0].id,selection:[]});});
 await page.screenshot({path:'studio-smoke.png',fullPage:true});
 const history=await page.evaluate(async()=>{
  const storage=await import('/src/store/storage.ts');const {diffDecks}=await import('/src/model/diff.ts');
  const {useEditor,openDeck,mutate}=await import('/src/store/editor.ts');
  // Raw access to the store idb-keyval uses, to plant and inspect records.
  const idb=(mode,fn)=>new Promise((res,rej)=>{const open=indexedDB.open('keyval-store');open.onerror=()=>rej(open.error);open.onsuccess=()=>{const tx=open.result.transaction('keyval',mode);const r=fn(tx.objectStore('keyval'));tx.oncomplete=()=>{open.result.close();res(r.result);};tx.onerror=()=>rej(tx.error);};});
  const deck=useEditor.getState().deck,titleBefore=deck.title;
  const v=await storage.saveVersion(deck,'smoke snapshot');
  mutate(d=>{d.title='Changed for history test';});
  const list=await storage.listVersions(deck.id),mine=list.find(x=>x.id===v.id);
  const saved=await storage.loadVersionDeck(deck.id,v.id);
  const diff=diffDecks(saved,useEditor.getState().deck);
  openDeck({...saved,updatedAt:Date.now()});
  const restoredTitle=useEditor.getState().deck.title;
  await storage.deleteVersion(deck.id,v.id);
  const deleted=!(await storage.listVersions(deck.id)).some(x=>x.id===v.id)&&!(await storage.loadVersionDeck(deck.id,v.id));
  // A version saved by an earlier release keeps the whole deck inside its list record.
  const oldKey=`version:${deck.id}:old1`;
  await idb('readwrite',s=>s.put({id:'old1',deckId:deck.id,createdAt:1,label:'old release',deck:{...structuredClone(saved),title:'Old shape'}},oldKey));
  const oldBeforeList=(await storage.loadVersionDeck(deck.id,'old1'))?.title;
  const old=(await storage.listVersions(deck.id)).find(x=>x.id==='old1');
  const oldRecord=await idb('readonly',s=>s.get(oldKey));
  const oldAfterList=(await storage.loadVersionDeck(deck.id,'old1'))?.title;
  // Only the newest MAX_VERSIONS are kept: the old one is the oldest, so it goes.
  for(let i=0;i<storage.MAX_VERSIONS;i++)await storage.saveVersion(deck,`v${i}`);
  const kept=await storage.listVersions(deck.id);
  const oldDropped=!kept.some(x=>x.id==='old1')&&!(await storage.loadVersionDeck(deck.id,'old1'));
  for(const x of kept)await storage.deleteVersion(deck.id,x.id);
  const leftover=(await idb('readonly',s=>s.getAllKeys())).filter(k=>String(k).startsWith('version')).length;
  return {listed:!!mine,listedHoldsDeck:list.some(x=>'deck' in x),slideCount:mine?.slideCount,slides:deck.slides.length,titleChanged:!!diff.titleChange,restoredTitle,titleBefore,deleted,
   oldBeforeList,oldAfterList,oldLabel:old?.label,oldSlideCount:old?.slideCount,oldRecordHoldsDeck:!!oldRecord&&'deck' in oldRecord,kept:kept.length,max:storage.MAX_VERSIONS,oldDropped,leftover};
 });
 assert.ok(history.listed,'saved version is listed');
 assert.equal(history.listedHoldsDeck,false,'the version list carries no deck');
 assert.equal(history.slideCount,history.slides,'the list knows the slide count without the deck');
 assert.ok(history.titleChanged,'diff detected the edit after the snapshot');
 assert.equal(history.restoredTitle,history.titleBefore,'restore brought back the snapshot');
 assert.ok(history.deleted,'deleting a version removes it and its deck');
 assert.equal(history.oldBeforeList,'Old shape','a version from an earlier release loads before it is listed');
 assert.equal(history.oldLabel,'old release','a version from an earlier release is listed');
 assert.equal(history.oldSlideCount,history.slides,'and shows its slide count');
 assert.equal(history.oldRecordHoldsDeck,false,'listing moves its deck out of the list record');
 assert.equal(history.oldAfterList,'Old shape','and it still loads afterwards');
 assert.equal(history.kept,history.max,'only the newest versions are kept');
 assert.ok(history.oldDropped,'the oldest version is dropped with its deck');
 assert.equal(history.leftover,0,'deleting every version leaves no version records');
 await page.setViewportSize({width:1024,height:768});
 const exportButton=page.getByRole('button',{name:'Export',exact:false}).first();const presentButton=page.getByRole('button',{name:'Present',exact:true});
 for(const button of [exportButton,presentButton]){const b=await button.boundingBox();assert.ok(b&&b.x>=0&&b.x+b.width<=1024,'Primary action is outside laptop viewport');}
 assert.deepEqual(errors,[]);console.log('Browser smoke passed: galleries, themes, agents, mocked Claudia request, saved elements, atomic undo, stale proposal guard, SVG rejection, version history save/diff/restore, every template.');
}catch(error){await page.screenshot({path:'studio-smoke.png',fullPage:true});throw error;}finally{await browser.close();}
