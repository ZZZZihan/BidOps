// Keep MIT notices in the static artifact; bundling removes source comments.
import { readFile, writeFile } from 'node:fs/promises';

const sources = [
  ['shadcn/ui', '../src/components/ui/LICENSES/shadcn-ui.txt'],
  ['Prompt Kit', '../src/components/ui/LICENSES/prompt-kit.txt'],
  ['Glow Card Grid', '../src/components/ui/LICENSES/chanhdai.txt'],
  ['React', '../node_modules/react/LICENSE'],
  ['React DOM', '../node_modules/react-dom/LICENSE'],
];
const notices = await Promise.all(sources.map(async ([name, path]) => `${name}\n${await readFile(new URL(path, import.meta.url), 'utf8')}`));
await writeFile(new URL('../dist/THIRD-PARTY-LICENSES.txt', import.meta.url), notices.join('\n\n'));
console.info('Third-party MIT notices included in dist/THIRD-PARTY-LICENSES.txt');
