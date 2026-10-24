import {mkdirSync,copyFileSync,existsSync} from 'node:fs';
import {resolve} from 'node:path';
const target=resolve('../src/cutback/dashboard/licenses');
mkdirSync(target,{recursive:true});
for(const name of ['react','react-dom','scheduler','vite']) {
  const base=resolve('node_modules',name,'LICENSE');
  copyFileSync(existsSync(base)?base:base+'.md',resolve(target,name+'.txt'));
}
