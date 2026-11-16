'use strict';
// Browser permissions the desktop shell grants. There is one: full screen, for the slideshow, and
// only when the top frame of an app page asks from a window this app created (the main window and
// the slides window it opens). Everything else, from every window, is denied.
const appPage=url=>{try{const u=new URL(url);return u.protocol==='deckforge:'&&u.host==='app';}catch{return false;}};
const grantsPermission=(permission,ownWindow,details)=>permission==='fullscreen'&&ownWindow===true&&!!details&&details.isMainFrame===true&&appPage(details.requestingUrl);
module.exports={grantsPermission};
