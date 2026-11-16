import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const {grantsPermission}=createRequire(import.meta.url)('../desktop/permissions.cjs');
const show={isMainFrame:true,requestingUrl:'deckforge://app/#/present/abc?s=0'};
test('grants full screen to an app page in one of the app\'s own windows',()=>{
 assert.equal(grantsPermission('fullscreen',true,show),true);
 assert.equal(grantsPermission('fullscreen',true,{isMainFrame:true,requestingUrl:'deckforge://app/#/audience/abc'}),true);
});
test('denies full screen to anything else',()=>{
 assert.equal(grantsPermission('fullscreen',false,show),false);// a window the app did not create, such as the suite launcher
 assert.equal(grantsPermission('fullscreen',true,{...show,isMainFrame:false}),false);
 assert.equal(grantsPermission('fullscreen',true,undefined),false);
 for(const requestingUrl of ['opensuite://home/','https://app/','deckforge://evil/','deckforge://app.evil/','file:///app','about:blank','',undefined])assert.equal(grantsPermission('fullscreen',true,{isMainFrame:true,requestingUrl}),false,String(requestingUrl));
});
test('denies every other permission, even to the app\'s own pages',()=>{
 for(const permission of ['media','display-capture','geolocation','notifications','midi','midiSysex','pointerLock','keyboardLock','clipboard-read','clipboard-sanitized-write','openExternal','window-management','automatic-fullscreen','storage-access','top-level-storage-access','speaker-selection','idle-detection','mediaKeySystem','hid','serial','usb','fileSystem','unknown','Fullscreen','',undefined])assert.equal(grantsPermission(permission,true,show),false,String(permission));
});
