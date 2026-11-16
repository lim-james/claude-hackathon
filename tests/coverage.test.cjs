const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
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

test('the floor check fails only below the floor minus the rounding tolerance',async()=>{
 const {belowFloor}=await load();
 const floors={A:50,B:50,C:50,D:50};
 const areas=[{name:'A',covered:500,lines:1000},{name:'B',covered:4996,lines:10000},{name:'C',covered:4994,lines:10000},{name:'D',covered:63,lines:100}];
 const failures=belowFloor(areas,floors,0.05);
 assert.equal(failures.length,1);
 assert.match(failures[0],/^C: 49\.94% is below the floor of 50%/);
});

test('an area without a floor fails instead of passing unchecked',async()=>{
 const {belowFloor}=await load();
 assert.deepEqual(belowFloor([{name:'New',covered:9,lines:10}],{},0.05),['New: no floor in coverage-floor.json']);
});
