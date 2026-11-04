const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {EventEmitter}=require('node:events');

test('concurrent OpenPDF launch requests start only one process',async()=>{
 const handlers=new Map(),children=[];
 let home,releaseAccess;
 const access=new Promise(resolve=>{releaseAccess=resolve;});
 class BrowserWindow extends EventEmitter{
  constructor(){super();home=this;this.webContents=new EventEmitter();this.webContents.setWindowOpenHandler=()=>{};this.webContents.mainFrame={url:'opensuite://home/'};}
  async loadURL(){}
 }
 const electron={app:{isPackaged:false,getPath:()=>'/fixture',on(){}},BrowserWindow,protocol:{handle(){}},ipcMain:{handle:(name,fn)=>handlers.set(name,fn)}};
 const spawn=()=>{
  const child=new EventEmitter();child.exitCode=null;child.killed=false;children.push(child);
  queueMicrotask(()=>child.emit('spawn'));return child;
 };
 const filename=path.join(__dirname,'../source-code/main/desktop/suite.cjs');
 const context=vm.createContext({process:{argv:[]},URL,Promise});
 const module={exports:{}};
 const wrapper=vm.runInContext('(function(require,module,__dirname){'+fs.readFileSync(filename,'utf8')+'\n})',context);
 wrapper(id=>id==='electron'?electron:id==='node:fs/promises'?{access:()=>access}:id==='node:child_process'?{spawn}:id==='./rules.cjs'?require('../source-code/main/desktop/rules.cjs'):require(id),module,path.dirname(filename));
 await module.exports.startSuite({openSlides(){},getSlidesWindow(){}});
 const event={sender:home.webContents,senderFrame:home.webContents.mainFrame};
 const launch=()=>handlers.get('suite:launch')(event,'openpdf');
 const first=launch(),second=launch();
 releaseAccess();
 const results=await Promise.all([first,second]);
 assert.equal(children.length,1,'concurrent requests must share the running OpenPDF process');
 assert.deepEqual(results.map(r=>r.message),['OpenPDF opened in its own window.','OpenPDF is already open. Switch to its window.']);
});
