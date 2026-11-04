import test from 'node:test';
import assert from 'node:assert/strict';
import {applyTokens} from '../src/model/resolve.ts';
test('title token is inserted literally, including dollar sequences',()=>{
 assert.equal(applyTokens('<p>{{title}}</p>',{n:1,total:2,title:'Save $& earn $$'}),'<p>Save $& earn $$</p>');
});
