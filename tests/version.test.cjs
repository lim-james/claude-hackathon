const {test}=require('node:test');
const assert=require('node:assert/strict');
const root=require('../package.json');
test('every manifest carries the single suite version',()=>{
 const lock=require('../source-code/OpenSlides/package-lock.json');
 const versions={
  'OpenSlides/package.json':require('../source-code/OpenSlides/package.json').version,
  'OpenSlides/desktop/package.json':require('../source-code/OpenSlides/desktop/package.json').version,
  'OpenSlides/package-lock.json':lock.version,
  'OpenSlides/package-lock.json packages[""]':lock.packages[''].version,
  'OpenSlides/desktop/builder.cjs':require('../source-code/OpenSlides/desktop/builder.cjs').extraMetadata.version,
 };
 for(const [file,version] of Object.entries(versions))assert.equal(version,root.version,file+' is out of sync; run `npm run version:sync`');
});
test('the supported Node range is the same in every place that states it',()=>{
 const fs=require('node:fs'),path=require('node:path');
 const read=file=>fs.readFileSync(path.join(__dirname,'..',file),'utf8');
 const range=root.engines&&root.engines.node;
 assert.ok(range,'package.json has no engines.node');
 assert.equal(require('../source-code/OpenSlides/package.json').engines.node,range,'OpenSlides/package.json engines.node differs from the root');
 assert.equal(require('../source-code/OpenSlides/package-lock.json').packages[''].engines.node,range,'OpenSlides/package-lock.json engines.node differs from the root');
 // Each alternative is a caret range: "^22.18.0" allows 22.18.0 and later on the 22 line, "^24" allows all of 24.
 const lines=range.split('||').map(part=>{
  const m=/^\^(\d+)(?:\.(\d+))?(?:\.(\d+))?$/.exec(part.trim());
  assert.ok(m,'unexpected engines.node alternative: '+part);
  return {major:+m[1],minor:+(m[2]||0),patch:+(m[3]||0)};
 });
 const readme=read('README.md').split('\n').find(line=>line.includes('**Node.js**'));
 assert.ok(readme,'README.md has no Node.js prerequisite line');
 const listed=[...readme.matchAll(/v(\d+)(?:\.(\d+))?/g)].map(m=>({major:+m[1],minor:+(m[2]||0)}));
 assert.deepEqual(listed,lines.map(({major,minor})=>({major,minor})),'README.md Node.js prerequisite does not match engines.node '+range);
 const ci=[...read('.github/workflows/windows.yml').matchAll(/node-version:\s*['"]?(\d+)(?:\.(\d+))?(?:\.(\d+))?/g)];
 assert.ok(ci.length>0,'windows.yml sets no node-version');
 for(const m of ci){
  const line=lines.find(l=>l.major===+m[1]);
  assert.ok(line,'CI Node '+m[1]+' is outside engines.node '+range);
  // A bare major in CI means the newest release of that line, which is inside the range.
  if(m[2]!==undefined)assert.ok(+m[2]>line.minor||(+m[2]===line.minor&&+(m[3]||0)>=line.patch),'CI Node '+m[0]+' is below engines.node '+range);
 }
});
