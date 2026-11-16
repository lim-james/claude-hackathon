const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

// A step that runs tests or a GUI check: a hang there must fail that step in
// minutes instead of holding the job until the job limit cancels the run.
const RUNS_CHECKS=/npm test|unittest|build-linux\.sh|--self-test|--smoke-test|-smoke\.mjs/;

function steps(){
 const text=fs.readFileSync(path.join(__dirname,'..','.github','workflows','windows.yml'),'utf8');
 const found=[];
 for(const line of text.split(/\r?\n/)){
  if(/^ {6}- /.test(line))found.push([line]);
  else if(/^ {0,5}\S/.test(line))found.push(null);
  else if(found.at(-1))found.at(-1).push(line);
 }
 return found.filter(Boolean).map(lines=>lines.join('\n'));
}

test('every CI step that runs tests or a GUI check has a time limit',()=>{
 const checks=steps().filter(step=>RUNS_CHECKS.test(step));
 assert.ok(checks.length>=9,'expected the test and GUI check steps, found '+checks.length);
 const unlimited=checks.filter(step=>!/^ {8}timeout-minutes: \d+$/m.test(step)).map(step=>step.split('\n')[0].trim());
 assert.deepEqual(unlimited,[],'steps without timeout-minutes');
});
