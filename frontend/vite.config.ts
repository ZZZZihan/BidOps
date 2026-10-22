import { defineConfig, loadEnv } from 'vite';

export default defineConfig(({mode}) => {
  const target = loadEnv(mode, '.', 'BIDOPS_').BIDOPS_API_PROXY_TARGET || 'http://127.0.0.1:8000';
  return {
  server: { host: '127.0.0.1', proxy: { '/api': {
    target, rewrite: (path: string) => path.replace(/^\/api/, ''),
  } } },
  preview: { host: '127.0.0.1', proxy: { '/api': {
    target, rewrite: (path: string) => path.replace(/^\/api/, ''),
  } } },
};
});
