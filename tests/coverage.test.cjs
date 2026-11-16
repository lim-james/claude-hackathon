const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {spawnSync}=require('node:child_process');
const {pathToFileURL}=require('node:url');
const scriptFile=path.join(__dirname,'..','scripts','coverage.mjs');
const load=()=>import('../scripts/coverage.mjs');

function fixture(t,files){
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'coverage-test-'));
 t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
 return Object.entries(files).map(([name,text])=>{
  const file=path.join(dir,name);
  fs.writeFileSync(file,text);
  return file;
 });
}

test('a source file no test loaded counts every non-blank line as uncovered',async t=>{
 const {summarise}=await load();
 const [loaded,unloaded]=fixture(t,{'loaded.js':'a\nb\nc\nd\n','unloaded.js':'one\n\n  \ntwo\nthree\r\nfour\n'});
 const area=summarise('Area',[loaded,unloaded],new Map([[loaded,[3,4]]]));
 assert.deepEqual(area,{name:'Area',covered:3,lines:8,percent:37.5,files:2,loaded:1});
});

test('an area whose files were all skipped by the tests is 0%, not missing',async t=>{
 const {summarise}=await load();
 const files=fixture(t,{'a.js':'x\ny\n','b.js':'z\n'});
 const area=summarise('Area',files,new Map());
 assert.equal(area.percent,0);
 assert.equal(area.lines,3);
 assert.equal(area.loaded,0);
});

test('lcov and coverage.py reports resolve to the same absolute file keys',async()=>{
 const {parseLcov,parseCoveragePy}=await load();
 const base=path.resolve('some','dir');
 const lcov=parseLcov('TN:\nSF:src/a.ts\nFNF:1\nLF:10\nLH:4\nend_of_record\nSF:src/b.ts\nLF:2\nLH:2\nend_of_record\n',base);
 assert.deepEqual([...lcov],[[path.join(base,'src','a.ts'),[4,10]],[path.join(base,'src','b.ts'),[2,2]]]);
 const py=parseCoveragePy({files:{'core.py':{summary:{covered_lines:7,num_statements:9}}}},base);
 assert.deepEqual([...py],[[path.join(base,'core.py'),[7,9]]]);
});

test('reports from two test runs merge, keeping the run that covered more of a file',async()=>{
 const {mostCovered}=await load();
 const merged=mostCovered(new Map([['a',[1,9]],['b',[5,9]]]),new Map([['a',[4,9]],['b',[2,9]],['c',[0,3]]]));
 assert.deepEqual([...merged],[['a',[4,9]],['b',[5,9]],['c',[0,3]]]);
});

test('an area fails when its covered lines fall or its untested lines rise, whatever the percentage',async()=>{
 const {belowFloor}=await load();
 const floor={covered:146,lines:8654};
 const check=(covered,lines)=>belowFloor([{name:'A',covered,lines}],{A:floor});
 assert.deepEqual(check(146,8654),[]);
 assert.deepEqual(check(150,8658),[]);
 assert.deepEqual(check(146,8600),[]);
 assert.deepEqual(check(146,8655),['A: 8509 lines are run by no test, above the floor of 8508']);
 assert.deepEqual(check(145,8653),['A: 145 lines are covered, below the floor of 146']);
 assert.deepEqual(check(145,8654),['A: 145 lines are covered, below the floor of 146','A: 8509 lines are run by no test, above the floor of 8508']);
 // A higher percentage than the floor's, with untested lines added: the cases a ratio let through.
 assert.deepEqual(belowFloor([{name:'A',covered:1067,lines:4665}],{A:{covered:1066,lines:4661}}),['A: 3598 lines are run by no test, above the floor of 3595']);
 assert.deepEqual(check(147,8713),['A: 8566 lines are run by no test, above the floor of 8508']);
 assert.deepEqual(check(1460,17308),['A: 15848 lines are run by no test, above the floor of 8508']);
});

test('a floor whose area was not measured fails, except the OpenPDF floor for the other Tesseract state',async()=>{
 const {belowFloor}=await load();
 const one={covered:1,lines:2};
 const area=name=>({name,covered:1,lines:2});
 assert.deepEqual(belowFloor([area('A')],{A:one,Gone:one}),['Gone: has a floor but was not measured']);
 assert.deepEqual(belowFloor([area('OpenPDF')],{OpenPDF:one,Gone:one}),['Gone: has a floor but was not measured']);
 const floors={OpenPDF:one,'OpenPDF without Tesseract':one,A:one};
 assert.deepEqual(belowFloor([area('OpenPDF'),area('A')],floors),[]);
 assert.deepEqual(belowFloor([area('OpenPDF without Tesseract'),area('A')],floors),[]);
 assert.deepEqual(belowFloor([area('A')],floors),['OpenPDF: has a floor but was not measured','OpenPDF without Tesseract: has a floor but was not measured']);
});

test('an area without a floor fails instead of passing unchecked',async()=>{
 const {belowFloor}=await load();
 assert.deepEqual(belowFloor([{name:'New',covered:9,lines:10}],{}),['New: no floor in coverage-floor.json']);
});

test('a floor that is not two whole line counts fails, whatever was measured',async()=>{
 const {belowFloor}=await load();
 const area=[{name:'A',covered:10,lines:10}];
 for(const floor of [null,'high',50,22.9,[],{},{covered:5},{lines:10},{covered:'5',lines:10},{covered:1.5,lines:10},{covered:-1,lines:10},{covered:0,lines:0},{covered:11,lines:10}]){
  const failures=belowFloor(area,{A:floor});
  assert.equal(failures.length,1,`floor ${JSON.stringify(floor)} passed`);
  assert.match(failures[0],/^A: the floor must be/);
 }
 for(const floors of [undefined,null,'x',[{covered:1,lines:2}]]){
  assert.deepEqual(belowFloor(area,floors),['coverage-floor.json has no "floors" object'],`floors ${JSON.stringify(floors)} passed`);
 }
});

test('OpenPDF is held to the full floor with Tesseract, its own floor without, and CI must have it',async()=>{
 const {pdfAreaName}=await load();
 assert.equal(pdfAreaName(true,true),'OpenPDF');
 assert.equal(pdfAreaName(true,false),'OpenPDF');
 assert.equal(pdfAreaName(false,false),'OpenPDF without Tesseract');
 assert.throws(()=>pdfAreaName(false,true),/Tesseract is not on PATH/);
});

test('either CI or GITHUB_ACTIONS marks a CI run',async()=>{
 const {inCi}=await load();
 assert.equal(inCi({CI:'true'}),true);
 assert.equal(inCi({CI:'',GITHUB_ACTIONS:'true'}),true);
 assert.equal(inCi({GITHUB_ACTIONS:'true'}),true);
 assert.equal(inCi({CI:'',GITHUB_ACTIONS:''}),false);
 assert.equal(inCi({}),false);
});

// A PATH holding only an empty file named like Tesseract stands in for an install,
// and an empty PATH for a machine without it, so these run the same anywhere.
function pathWith(t,tesseract){
 const name=process.platform==='win32'?'tesseract.exe':'tesseract';
 const [file]=fixture(t,tesseract?{[name]:''}:{'empty':''});
 return path.dirname(file);
}

test('the OpenPDF floor follows Tesseract on PATH, and CI without it is refused',async t=>{
 const {pdfArea}=await load();
 const withIt=pathWith(t,true),without=pathWith(t,false);
 assert.equal(pdfArea({PATH:withIt,CI:'true'}),'OpenPDF');
 assert.equal(pdfArea({PATH:withIt}),'OpenPDF');
 assert.equal(pdfArea({PATH:without}),'OpenPDF without Tesseract');
 assert.throws(()=>pdfArea({PATH:without,CI:'true'}),/Tesseract is not on PATH/);
 assert.throws(()=>pdfArea({PATH:without,CI:'',GITHUB_ACTIONS:'true'}),/Tesseract is not on PATH/);
});

// The real script with its real measure(). PYTHON names a missing command, so a run
// that gets past the Tesseract check stops at the first test command instead of
// running the suites: which of the two messages comes back shows where it stopped.
function realRun(t,tesseract,ci){
 const env=Object.fromEntries(Object.entries(process.env).filter(([key])=>!/^(path|ci|github_actions)$/i.test(key)));
 return spawnSync(process.execPath,[scriptFile],{encoding:'utf8',env:{...env,...ci,PATH:pathWith(t,tesseract),PYTHON:path.join(__dirname,'no-such-python')}});
}

test('run directly in CI without Tesseract, the script exits 1 before it measures anything',t=>{
 for(const ci of [{CI:'true'},{GITHUB_ACTIONS:'true'},{CI:'',GITHUB_ACTIONS:'true'},{CI:'true',GITHUB_ACTIONS:'true'}]){
  const result=realRun(t,false,ci);
  assert.equal(result.status,1,`${JSON.stringify(ci)} exited ${result.status}`);
  assert.match(result.stderr,/^Tesseract is not on PATH\. CI must run the OCR test/,JSON.stringify(ci));
  assert.equal(result.stdout,'');
 }
});

test('run directly, the script goes on to the tests in CI with Tesseract and outside CI without it',t=>{
 for(const [tesseract,ci] of [[true,{CI:'true',GITHUB_ACTIONS:'true'}],[true,{}],[false,{}]]){
  const result=realRun(t,tesseract,ci);
  assert.equal(result.status,1);
  assert.match(result.stderr,/^OpenPDF tests failed \(.*ENOENT/,`tesseract ${tesseract}, ${JSON.stringify(ci)}`);
 }
});

test('a command counts as installed only when a PATH folder holds it',async t=>{
 const {onPath}=await load();
 const [tool]=fixture(t,{'sometool':'','othertool.EXE':''});
 const dir=path.dirname(tool);
 const env={PATH:[path.join(dir,'missing'),dir].join(path.delimiter)};
 assert.equal(onPath('sometool',env,'linux'),true);
 assert.equal(onPath('tesseract',env,'linux'),false);
 assert.equal(onPath('sometool',{PATH:''},'linux'),false);
 assert.equal(onPath('othertool',{...env,PATHEXT:'.COM;.EXE'},'win32'),true);
 assert.equal(onPath('othertool',env,'linux'),false);
});

test('a test command that exits non-zero or cannot start stops the measurement',async()=>{
 const {run}=await load();
 assert.doesNotThrow(()=>run('ok',process.execPath,['-e','process.exit(0)'],__dirname));
 assert.throws(()=>run('Some tests',process.execPath,['-e','console.log("2 failing");process.exit(3)'],__dirname),/Some tests failed \(exit 3\):\n2 failing/);
 assert.throws(()=>run('Some tests',path.join(__dirname,'no-such-command'),[],__dirname),/Some tests failed \(.*ENOENT/);
});

// The script as a process. `measured` stands in for the test run: 'throw' makes it fail.
function gate(t,floorText,measured,args=[]){
 const [floors]=fixture(t,{'floor.json':floorText});
 const script=`import(process.env.COVERAGE_SCRIPT).then(m=>m.cli([...JSON.parse(process.env.COVERAGE_ARGS)],{measure(){`
  +`const areas=JSON.parse(process.env.COVERAGE_MEASURED);if(areas==='throw')throw new Error('OpenPDF tests failed (exit 1)');return areas;}}))`;
 const result=spawnSync(process.execPath,['-e',script],{encoding:'utf8',env:{...process.env,
  COVERAGE_SCRIPT:pathToFileURL(scriptFile).href,COVERAGE_ARGS:JSON.stringify(['--floors',floors,...args]),COVERAGE_MEASURED:JSON.stringify(measured)}});
 return {...result,floors};
}
const measured=[{name:'A',covered:40,lines:100,percent:40,files:3,loaded:2}];

test('the script exits 0 when every area is at its floor',t=>{
 const result=gate(t,'{"floors":{"A":{"covered":40,"lines":100}}}',measured);
 assert.equal(result.status,0,result.stderr);
 assert.match(result.stdout,/A +40\.0%  40\/100 lines, 2\/3 files loaded by tests  \(floor 40\/100\)\nEvery area is at or better than its floor\./);
});

test('the script exits 1 when an area is worse than its floor, and says how to pass',t=>{
 const result=gate(t,'{"floors":{"A":{"covered":41,"lines":100}}}',measured);
 assert.equal(result.status,1);
 assert.match(result.stderr,/Coverage is worse than its floor:\n  A: 40 lines are covered, below the floor of 41\n  A: 60 lines are run by no test, above the floor of 59\nAdd tests .*\n.*coverage\.mjs --update` and commit scripts\/coverage-floor\.json/);
});

test('the script exits 1 when a floor has no measured area',t=>{
 const result=gate(t,'{"floors":{"A":{"covered":40,"lines":100},"Dropped":{"covered":7,"lines":111}}}',measured);
 assert.equal(result.status,1);
 assert.match(result.stderr,/Dropped: has a floor but was not measured/);
});

test('the script exits 1 on a floor file with old-style or damaged floors',t=>{
 for(const text of ['{"floors":{"A":50}}','{"tolerance":0.05,"floors":{"A":1.7}}','{"floors":{"A":null}}','{"floors":{"A":"high"}}','{"floors":{}}','{}','null','[]']){
  const result=gate(t,text,measured);
  assert.equal(result.status,1,`${text} exited ${result.status}`);
  assert.match(result.stderr,/is damaged|Coverage is worse than its floor/);
 }
});

test('the script exits 1 when the tests it measures fail',t=>{
 const result=gate(t,'{"floors":{"A":{"covered":0,"lines":100}}}','throw');
 assert.equal(result.status,1);
 assert.match(result.stderr,/OpenPDF tests failed \(exit 1\)/);
});

test('--update writes the measured counts, keeps other floors, and the result passes',t=>{
 const result=gate(t,'{"tolerance":0.05,"floors":{"A":22.9,"Other":{"covered":7,"lines":9}}}',measured,['--update']);
 assert.equal(result.status,0,result.stderr);
 assert.deepEqual(JSON.parse(fs.readFileSync(result.floors,'utf8')),{floors:{A:{covered:40,lines:100},Other:{covered:7,lines:9}}});
});

test('run directly, the script exits 1 on damaged floors before it measures anything',t=>{
 const [floors]=fixture(t,{'floor.json':'{"tolerance":0.05,"floors":{"OpenPDF":22.9,"OpenSlides":null}}'});
 const result=spawnSync(process.execPath,[scriptFile,'--floors',floors],{encoding:'utf8'});
 assert.equal(result.status,1,result.stdout);
 assert.match(result.stderr,/floor\.json is damaged:\n  OpenPDF: the floor must be .*got 22\.9\n  OpenSlides: the floor must be .*got null/);
 assert.equal(result.stdout,'');
});

test('run directly, the script exits 1 on a floor file it cannot read',t=>{
 const [broken]=fixture(t,{'floor.json':'{"floors": '});
 for(const floors of [broken,path.join(path.dirname(broken),'absent.json')]){
  const result=spawnSync(process.execPath,[scriptFile,'--floors',floors],{encoding:'utf8'});
  assert.equal(result.status,1,result.stdout);
  assert.notEqual(result.stderr.trim(),'');
 }
});
