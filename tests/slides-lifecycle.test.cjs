const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {EventEmitter}=require('node:events');

test('macOS activation reopens the editor while an audience window remains',async()=>{
 const app=new EventEmitter(),windows=[];
 let ready;
 app.getPath=()=>'/fixture';app.setPath=()=>{};app.setName=()=>{};
 app.whenReady=()=>({then:fn=>{ready=fn;}});
 class BrowserWindow extends EventEmitter{
  constructor(){super();windows.push(this);this.webContents=new EventEmitter();this.webContents.setWindowOpenHandler=()=>{};}
  isDestroyed(){return false;}
  async loadURL(url){this.url=url;}
  static getAllWindows(){return windows;}
 }
 const electron={app,BrowserWindow,protocol:{registerSchemesAsPrivileged(){},handle(){}},ipcMain:{handle(){}},safeStorage:{isEncryptionAvailable:()=>false},session:{defaultSession:{setPermissionRequestHandler(){},setPermissionCheckHandler(){}}}};
 const filename=path.join(__dirname,'../source-code/OpenSlides/desktop/main.cjs');
 const context=vm.createContext({process:{env:{},argv:[],platform:'darwin'},URL,Map});
 const wrapper=vm.runInContext('(function(require,__dirname){'+fs.readFileSync(filename,'utf8')+'\n})',context);
 wrapper(id=>id==='electron'?electron:id==='node:fs'?{mkdirSync(){}}:id==='./anthropic.cjs'?{callAnthropic(){throw new Error('Unexpected API call');}}:require(id),path.dirname(filename));
 await ready();
 assert.equal(windows.length,1);
 const editor=windows.shift();
 windows.push({audience:true});
 editor.emit('closed');
 app.emit('activate');
 assert.equal(windows.length,2,'the remaining audience window must not prevent reopening the editor');
 assert.equal(windows[1].url,'deckforge://app/');
});
