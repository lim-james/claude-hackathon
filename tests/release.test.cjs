const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');

const WINDOWS=['OpenPDF.exe','OpenSlides-Portable-0.5.0-x64.exe','OpenSlides-Setup-0.5.0-x64.exe','OpenSuite-Portable-0.5.0-x64.exe','OpenSuite-Setup-0.5.0-x64.exe','SHA256SUMS.txt'];
const LINUX=['OpenPDF-linux-x86_64','OpenSlides-0.5.0-x86_64.AppImage','OpenSuite-0.5.0-amd64.deb','OpenSuite-0.5.0-x86_64.AppImage','SHA256SUMS-linux.txt'];
const assets=names=>names.map(name=>({name}));

// A stand-in for the GitHub CLI that keeps the repository's releases in memory.
function fakeGitHub(releases=[],{loseOnUpload}={}){
 const state={releases,writes:[],nextId:100};
 state.gh=args=>{
  const [first,second]=args;
  if(first==='api'&&second.includes('/releases?'))return JSON.stringify(second.endsWith('page=1')?state.releases:[]);
  state.writes.push(args.join(' '));
  if(first==='api'&&second==='-X'){
   const id=Number(args[3].split('/').pop()),release=state.releases.find(r=>r.id===id);
   assert.ok(release,'unknown release '+id);
   if(args[2]==='DELETE')state.releases=state.releases.filter(r=>r!==release);
   else if(args[2]==='PATCH'){
    assert.deepEqual(args.slice(4),['-F','draft=false','-f','make_latest=true']);
    release.draft=false;release.assetsWhenPublished=release.assets.length;
   }else assert.fail('unexpected '+args.join(' '));
   return '';
  }
  if(first==='release'&&second==='create'){
   const flags=args.indexOf('--repo'),files=args.slice(3,flags);
   assert.ok(args.includes('--draft'),'a release must be created as a draft');
   state.releases.push({id:state.nextId++,tag_name:args[2],draft:true,target_commitish:args[args.indexOf('--target')+1],
    assets:assets(files.map(file=>file.split('/').pop()).filter(name=>name!==loseOnUpload))});
   return '';
  }
  assert.fail('unexpected gh '+args.join(' '));
 };
 return state;
}
async function run(github,names,version='0.5.0'){
 const {publishRelease}=await import('../scripts/publish-release.mjs');
 const notices=[];
 const result=publishRelease({gh:github.gh,repo:'owner/name',sha:'abc123',version,files:names.map(name=>'release-files/'+name),notesFile:'notes.md',log:line=>notices.push(line)});
 return {result,notices};
}

test('both builds present: one release, published only once every file is attached',async()=>{
 const github=fakeGitHub([{id:1,tag_name:'v0.4.0',draft:false,assets:assets([...WINDOWS,...LINUX])}]);
 const {result}=await run(github,[...WINDOWS,...LINUX]);
 assert.equal(result,'published');
 const release=github.releases.find(r=>r.tag_name==='v0.5.0');
 assert.equal(release.draft,false);
 assert.equal(release.target_commitish,'abc123');
 assert.equal(release.assetsWhenPublished,11);
 assert.deepEqual(release.assets.map(a=>a.name).sort(),[...WINDOWS,...LINUX].sort());
});

test('Linux files absent: nothing is created',async()=>{
 const github=fakeGitHub();
 await assert.rejects(run(github,WINDOWS),/did not produce OpenSuite AppImage, OpenSuite \.deb, OpenSlides AppImage, OpenPDF-linux-x86_64, SHA256SUMS-linux\.txt/);
 assert.deepEqual(github.writes,[]);
 assert.deepEqual(github.releases,[]);
});

test('Windows files absent: nothing is created',async()=>{
 const github=fakeGitHub();
 await assert.rejects(run(github,LINUX),/did not produce OpenSuite Windows installer, .*SHA256SUMS\.txt$/);
 assert.deepEqual(github.writes,[]);
});

test('a complete release for this version is left alone, with a notice',async()=>{
 const github=fakeGitHub([{id:1,tag_name:'v0.5.0',draft:false,assets:assets([...WINDOWS,...LINUX])}]);
 const {result,notices}=await run(github,[...WINDOWS,...LINUX]);
 assert.equal(result,'exists');
 assert.match(notices[0],/^::notice::Release v0\.5\.0 already exists; skipping publish/);
 assert.deepEqual(github.writes,[]);
});

test('a Windows-only release for this version fails the job instead of passing as done',async()=>{
 const github=fakeGitHub([{id:1,tag_name:'v0.5.0',draft:false,assets:assets(WINDOWS)}]);
 await assert.rejects(run(github,[...WINDOWS,...LINUX]),/Release v0\.5\.0 exists but lacks: OpenSuite AppImage/);
 assert.deepEqual(github.writes,[]);
});

test('a draft left by a cancelled run is replaced',async()=>{
 const github=fakeGitHub([{id:7,tag_name:'v0.5.0',draft:true,assets:assets(WINDOWS)}]);
 const {result}=await run(github,[...WINDOWS,...LINUX]);
 assert.equal(result,'published');
 assert.equal(github.releases.length,1);
 assert.notEqual(github.releases[0].id,7);
 assert.equal(github.releases[0].assetsWhenPublished,11);
});

test('an upload that loses a file removes the draft and publishes nothing',async()=>{
 const github=fakeGitHub([],{loseOnUpload:'SHA256SUMS-linux.txt'});
 await assert.rejects(run(github,[...WINDOWS,...LINUX]),/holds 10 of 11 files; missing: SHA256SUMS-linux\.txt/);
 assert.deepEqual(github.releases,[]);
 assert.ok(!github.writes.some(write=>write.includes('PATCH')));
});

test('the workflow publishes from one job that waits for both platform builds',()=>{
 const text=readFileSync(join(__dirname,'..','.github','workflows','windows.yml'),'utf8').replace(/\r\n/g,'\n');
 const jobs={};
 let name;
 for(const line of text.slice(text.indexOf('\njobs:\n')+7).split('\n')){
  const header=line.match(/^  ([\w-]+):\s*$/);
  if(header){name=header[1];jobs[name]='';}
  else if(name)jobs[name]+=line+'\n';
 }
 const needs=job=>(jobs[job].match(/^    needs: (.+)$/m)?.[1]??'').replace(/[\[\]\s]/g,'').split(',');
 const publishers=Object.keys(jobs).filter(job=>/gh release|publish-release|\/releases/.test(jobs[job]));
 assert.deepEqual(publishers,['release']);
 assert.deepEqual(needs('release').sort(),['linux','windows']);
 assert.match(jobs.release,/^    if: github\.ref == 'refs\/heads\/master'$/m);
 assert.ok(needs('homepage').includes('release'),'the homepage links to release files, so it waits for the release');
});
