import { defineConfig } from 'tsup';

export default defineConfig({
  entry: [
    'src/index.ts',
    'src/core.ts',
    'src/next.ts',
    'src/express.ts',
    'src/middleware.ts',
    'src/langchain.ts',
    'src/openclaw.ts',
  ],
  format: ['esm', 'cjs'],
  dts: true,
  sourcemap: true,
  clean: true,
  minify: true,
  splitting: true,
  treeshake: true,
  target: 'node18',
  outDir: 'dist',
  shims: true,
  cjsInterop: true,
});
