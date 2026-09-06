import { defineConfig } from 'vite';
import { mockApi } from './mock/plugin';

// No framework plugin: esbuild compiles the JSX straight to Preact's
// jsx-runtime, which keeps the dependency list to preact alone.
export default defineConfig({
  base: './',
  plugins: [mockApi()],
  esbuild: { jsx: 'automatic', jsxImportSource: 'preact' },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    target: 'es2022',
    cssCodeSplit: false,
    rollupOptions: {
      output: {
        entryFileNames: 'a/[name]-[hash].js',
        chunkFileNames: 'a/[name]-[hash].js',
        assetFileNames: 'a/[name]-[hash][extname]',
      },
    },
  },
});
