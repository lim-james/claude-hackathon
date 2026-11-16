// Publishes v<version> with every platform's files at once, or publishes nothing.
// CI runs it from the `release` job, after both platform builds have passed:
//   node scripts/publish-release.mjs <folder holding the built files>
// The release is filled as a draft (no tag, not public) and only then published.
import {execFileSync} from 'node:child_process';
import {readFileSync,readdirSync} from 'node:fs';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';

// One entry per file a complete release holds.
export const REQUIRED=[
 ['OpenSuite Windows installer',/^OpenSuite-Setup-.+\.exe$/],
 ['OpenSuite Windows portable',/^OpenSuite-Portable-.+\.exe$/],
 ['OpenSlides Windows installer',/^OpenSlides-Setup-.+\.exe$/],
 ['OpenSlides Windows portable',/^OpenSlides-Portable-.+\.exe$/],
 ['OpenPDF.exe',/^OpenPDF\.exe$/],
 ['SHA256SUMS.txt',/^SHA256SUMS\.txt$/],
 ['OpenSuite AppImage',/^OpenSuite-.+\.AppImage$/],
 ['OpenSuite .deb',/^OpenSuite-.+\.deb$/],
 ['OpenSlides AppImage',/^OpenSlides-.+\.AppImage$/],
 ['OpenPDF-linux-x86_64',/^OpenPDF-linux-x86_64$/],
 ['SHA256SUMS-linux.txt',/^SHA256SUMS-linux\.txt$/],
];
export const missingFrom=names=>REQUIRED.filter(([,pattern])=>!names.some(name=>pattern.test(name))).map(([label])=>label);

// gh(args) runs the GitHub CLI and returns its stdout; tests pass a fake.
export function publishRelease({gh,repo,sha,version,files,notesFile,log=console.log}){
 if(!version)throw new Error('No version in package.json');
 const tag='v'+version;
 const releases=()=>{
  const all=[];
  for(let page=1;;page++){
   const batch=JSON.parse(gh(['api',`repos/${repo}/releases?per_page=100&page=${page}`]));
   all.push(...batch);
   if(batch.length<100)return all.filter(release=>release.tag_name===tag);
  }
 };
 const names=release=>release.assets.map(asset=>asset.name);
 const deleteDrafts=()=>{for(const release of releases())if(release.draft)gh(['api','-X','DELETE',`repos/${repo}/releases/${release.id}`]);};

 const published=releases().find(release=>!release.draft);
 if(published){
  const missing=missingFrom(names(published));
  // Files built from this commit must not be mixed into a release that targets an earlier one.
  if(missing.length)throw new Error(`Release ${tag} exists but lacks: ${missing.join(', ')}. Delete that release and its tag, or bump 'version' in package.json, then rerun.`);
  log(`::notice::Release ${tag} already exists; skipping publish. Bump 'version' in package.json to cut a new release.`);
  return 'exists';
 }
 const missing=missingFrom(files.map(file=>file.split(/[\\/]/).pop()));
 if(missing.length)throw new Error(`Not publishing ${tag}: the build did not produce ${missing.join(', ')}`);

 deleteDrafts(); // left behind by a cancelled run
 let draft;
 try{
  gh(['release','create',tag,...files,'--repo',repo,'--draft','--target',sha,'--title','OpenSuite '+tag,'--notes-file',notesFile]);
  draft=releases().find(release=>release.draft);
  if(!draft)throw new Error(`Draft release ${tag} not found after creating it`);
  const lost=missingFrom(names(draft));
  if(lost.length||draft.assets.length!==files.length)throw new Error(`Draft release ${tag} holds ${draft.assets.length} of ${files.length} files`+(lost.length?`; missing: ${lost.join(', ')}`:''));
 }catch(error){
  try{deleteDrafts();}catch{}
  throw error;
 }
 // Publishing creates the tag and makes the release public, with every file already attached.
 gh(['api','-X','PATCH',`repos/${repo}/releases/${draft.id}`,'-F','draft=false','-f','make_latest=true']);
 log(`Published ${tag} with ${files.length} files`);
 return 'published';
}

if(import.meta.url===pathToFileURL(process.argv[1]??'').href){
 const folder=process.argv[2];
 const {GITHUB_REPOSITORY:repo,GITHUB_SHA:sha}=process.env;
 if(!folder||!repo||!sha){console.error('usage: GITHUB_REPOSITORY=owner/name GITHUB_SHA=<commit> node scripts/publish-release.mjs <folder>');process.exit(2);}
 try{
  publishRelease({
   gh:args=>execFileSync('gh',args,{encoding:'utf8',stdio:['ignore','pipe','inherit'],maxBuffer:64*1024*1024}),
   repo,sha,
   version:JSON.parse(readFileSync('package.json','utf8')).version,
   files:readdirSync(folder).sort().map(name=>join(folder,name)),
   notesFile:'source-code/OpenPDF/RELEASE-NOTES.md',
  });
 }catch(error){
  console.log('::error::'+error.message);
  process.exit(1);
 }
}
