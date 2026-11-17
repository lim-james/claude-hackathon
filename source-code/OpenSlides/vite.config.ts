import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { VitePWA } from 'vite-plugin-pwa';

// BASE_PATH lets the same build serve from a GitHub Pages project path
// (e.g. /open-suite/) or from the root of any static host.
const desktop = process.env.DECKFORGE_DESKTOP === '1';
const base = desktop ? './' : process.env.BASE_PATH ?? '/';

export default defineConfig({
  base,
  plugins: [
    react(),
    VitePWA({
      disable: desktop,
      registerType: 'autoUpdate',
      includeAssets: ['favicon.svg'],
      manifest: {
        name: 'OpenSlides',
        short_name: 'OpenSlides',
        description: 'An open-source slide deck editor that works offline and exports real PowerPoint files.',
        theme_color: '#2f4a3f',
        background_color: '#2f4a3f',
        display: 'standalone',
        start_url: '.',
        icons: [
          { src: 'icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: 'icon-512.png', sizes: '512x512', type: 'image/png' },
          { src: 'icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      workbox: {
        // The DejaVu and Latin Modern subsets ship only as .woff (src/styles/fonts); the other .woff files duplicate cached .woff2.
        globPatterns: ['**/*.{js,css,html,svg,png,woff2}', 'assets/{dejavu,latin-modern}-*.woff'],
        maximumFileSizeToCacheInBytes: 8 * 1024 * 1024,
        navigateFallback: 'index.html',
      },
    }),
  ],
  build: {
    chunkSizeWarningLimit: 2500,
  },
  // Exports embed fonts by resolving their stylesheet URLs through a <base> element, which the desktop
  // CSP (base-uri 'none') blocks; root-absolute font URLs resolve the same either way. The desktop app
  // always serves from the root of deckforge://app/.
  experimental: desktop
    ? { renderBuiltUrl: (filename, { hostType }) => (hostType === 'css' && /\.woff2?$/.test(filename) ? '/' + filename : { relative: true }) }
    : undefined,
});
