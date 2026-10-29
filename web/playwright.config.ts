import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'tests',outputDir:process.env.CUTBACK_UI_OUTPUT_DIR || 'test-results',workers:1,timeout:30000,use:{
  baseURL:process.env.CUTBACK_UI_URL || 'http://127.0.0.1:8080',
  viewport:{width:1365,height:900},trace:'off',screenshot:'only-on-failure',
  video:process.env.CUTBACK_DEMO_VIDEO==='true'?{mode:'on',size:{width:1365,height:900}}:'off',
  launchOptions:{slowMo:process.env.CUTBACK_DEMO_VIDEO==='true'?200:0}}});
