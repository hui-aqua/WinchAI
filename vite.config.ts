import { fileURLToPath, URL } from 'node:url';
import { rmSync } from 'node:fs';
import path from 'node:path';
import { defineConfig } from 'vitest/config';

let outputDirectory = '';
let productionBuild = false;

export default defineConfig({
  base: './',
  plugins: [{
    name: 'keep-local-aquasim-model-private',
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        if (request.url?.split('?')[0] === '/Next_Otervika_NLS_AI.amodel') {
          response.statusCode = 404;
          response.end('Not found');
          return;
        }
        next();
      });
    },
    configResolved(config) {
      productionBuild = config.command === 'build';
      outputDirectory = path.resolve(config.root, config.build.outDir);
    },
    closeBundle() {
      if (productionBuild) {
        rmSync(path.join(outputDirectory, 'Next_Otervika_NLS_AI.amodel'), { force: true });
      }
    }
  }],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  },
  build: {
    chunkSizeWarningLimit: 800,
    rollupOptions: {
      output: {
        manualChunks(id: string) {
          if (id.includes('node_modules/three')) {
            return 'three';
          }
        }
      }
    }
  },
  test: {
    environment: 'happy-dom'
  }
});
