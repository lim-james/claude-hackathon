const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {EventEmitter}=require('node:events');
const {validateRequest}=require('../source-code/OpenSlides/desktop/anthropic.cjs');

test('an old window request cannot remove a reopened window request with the same ID',async()=>{
 const app=new EventEmitter(),windows=[],handlers=new Map(),requests=[];
 let ready;
 app.getPath=()=>'/fixture';app.setPath=()=>{};app.setName=()=>{};
 app.whenReady=()=>({then:fn=>{ready=fn;}});
 class BrowserWindow extends EventEmitter{
  constructor(){super();windows.push(this);this.webContents=new EventEmitter();this.webContents.setWindowOpenHandler=()=>{};this.webContents.mainFrame={url:'deckforge://app/'};this.destroyed=false;}
  isDestroyed(){return this.destroyed;}
  async loadURL(){}
  static getAllWindows(){return windows.filter(w=>!w.destroyed);}
 }
 const electron={app,BrowserWindow,protocol:{registerSchemesAsPrivileged(){},handle(){}},ipcMain:{handle:(name,fn)=>handlers.set(name,fn)},safeStorage:{isEncryptionAvailable:()=>false},session:{defaultSession:{setPermissionRequestHandler(){},setPermissionCheckHandler(){}}}};
 const callAnthropic=(key,body,version,signal)=>{
  assert.equal(key,'test-key');validateRequest(body,version);
  return new Promise((resolve,reject)=>{requests.push({signal,resolve,reject});});
 };
 const filename=path.join(__dirname,'../source-code/OpenSlides/desktop/main.cjs');
 const context=vm.createContext({process:{env:{ANTHROPIC_API_KEY:'test-key'},argv:[],platform:'darwin'},URL,Map,AbortController,setTimeout,clearTimeout});
 const wrapper=vm.runInContext('(function(require,__dirname){'+fs.readFileSync(filename,'utf8')+'\n})',context);
 wrapper(id=>id==='electron'?electron:id==='node:fs'?{mkdirSync(){}}:id==='./anthropic.cjs'?{callAnthropic}:require(id),path.dirname(filename));
 await ready();
 const event=win=>({sender:win.webContents,senderFrame:win.webContents.mainFrame});
 const body={model:'claude-opus-5-5',max_tokens:1024,system:'Propose a design',messages:[{role:'user',content:'A leaf'}]};
 const request=win=>handlers.get('ai:request')(event(win),'design',body,'2026-10-21');
 const oldWindow=windows[0],oldRequest=request(oldWindow);
 oldWindow.destroyed=true;oldWindow.emit('closed');
 assert.equal(requests[0].signal.aborted,true);
 app.emit('activate');
 const newWindow=windows[1],newRequest=request(newWindow);
 try{
  requests[0].reject(new Error('Delayed abort completion'));
  await oldRequest;
  handlers.get('ai:cancel')(event(newWindow),'design');
  assert.equal(requests[1].signal.aborted,true,'cancelling must still reach the new request after the old request finishes');
 }finally{
  requests[1].resolve({ok:true});
  await newRequest;
 }
});
