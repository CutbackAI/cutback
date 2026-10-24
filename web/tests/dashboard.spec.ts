import {test,expect} from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('authenticated API parity, navigation, accessibility and credential lifetime',async({page})=>{
  const demoPause=async()=>{if(process.env.CUTBACK_DEMO_VIDEO==='true')await page.waitForTimeout(1800);};
  const failures:string[]=[];
  page.on('pageerror',error=>failures.push(error.message));
  await page.goto('/dashboard/?project='+encodeURIComponent(process.env.CUTBACK_UI_PROJECT || ''));
  if(process.env.CUTBACK_DEMO_VIDEO==='true'){
    await page.evaluate(()=>{
      const banner=document.createElement('div');
      banner.textContent='CUTBACK DEMO — SYNTHETIC DATA — NO LIVE PROVIDER REQUESTS';
      banner.setAttribute('role','region');
      banner.setAttribute('aria-label','Synthetic demo disclosure');
      banner.style.cssText='padding:12px;background:#222;color:white;text-align:center;font:14px sans-serif';
      document.body.prepend(banner);
    });
  }
  await expect(page.getByRole('heading',{name:/Understand your AI spend/})).toBeVisible();
  await page.getByLabel('Control API key').fill(process.env.CUTBACK_UI_KEY!);
  await page.getByRole('button',{name:'Connect to this collector'}).click();
  await expect(page.getByRole('heading',{name:'Overview',exact:true})).toBeVisible();
  await expect(page.getByText('2',{exact:true})).toBeVisible();
  const a11y=await new AxeBuilder({page}).analyze();
  expect(a11y.violations).toEqual([]);
  await page.screenshot({path:'../artifacts/dashboard-overview.png',fullPage:true});
  await demoPause();
  await page.getByRole('button',{name:'Inventory',exact:true}).click();
  await expect(page.getByRole('cell',{name:/main.py:3/})).toBeVisible();
  await demoPause();
  await page.getByRole('button',{name:'Costs',exact:true}).click();
  await expect(page.getByRole('table',{name:'Observed usage by model'})).toBeVisible();
  await expect(page.getByRole('img',{name:/Known modeled cost subtotals/})).toBeVisible();
  await demoPause();
  await page.getByRole('button',{name:'Opportunities',exact:true}).click();
  await expect(page.getByRole('heading',{name:'resolve pricing'})).toBeVisible();
  await demoPause();
  await page.getByRole('button',{name:'Experiments',exact:true}).click();
  await expect(page.getByText('No experiment runs.')).toBeVisible();
  await demoPause();
  await page.getByRole('button',{name:'Changes',exact:true}).click();
  await expect(page.getByText('No recommendations. Complete an approved experiment first.')).toBeVisible();
  await demoPause();
  await page.getByRole('button',{name:'Integrations',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Instrumentation health'})).toBeVisible();
  await demoPause();
  await page.getByRole('button',{name:'Security',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Project keys'})).toBeVisible();
  await demoPause();
  expect(await page.evaluate(()=>Object.keys(localStorage))).toEqual([]);
  expect(page.url()).not.toContain(process.env.CUTBACK_UI_KEY!);
  await page.setViewportSize({width:390,height:844});
  await expect(page.getByRole('heading',{name:'Security',exact:true})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await demoPause();
  await page.getByRole('button',{name:'Disconnect'}).click();
  await expect(page.getByLabel('Control API key')).toHaveValue('');
  expect(failures).toEqual([]);
});
