/* README 演示模式截图脚本：进演示 → 亮/暗主界面 → 媒体详情弹窗 → 概览 → 切回正常 */
import { chromium } from 'playwright-core';
import fs from 'node:fs';

const OUT = 'C:/Users/Administrator/WorkBuddy/2026-10-11-07-55-11/tg-leave-web/docs/screenshots';
fs.mkdirSync(OUT, { recursive: true });
const shot = (page, name) => page.screenshot({ path: `${OUT}/${name}.png` });

const browser = await chromium.launch({ channel: 'msedge', headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1680, height: 1050 }, deviceScaleFactor: 2 });
  page.setDefaultTimeout(90000);
  await page.goto('http://127.0.0.1:8787', { waitUntil: 'domcontentloaded' });

  // 等真实登录态恢复 + 表格渲染
  await page.waitForSelector('table tbody tr', { timeout: 90000 });
  await page.waitForTimeout(3000);

  // 切到演示模式（500 条模拟数据，零请求）
  await page.getByRole('button', { name: '演示', exact: true }).click();
  await page.waitForSelector('table tbody tr', { timeout: 90000 });
  await page.waitForTimeout(3500);   // 数字滚动动画 / 头像占位淡入
  await shot(page, 'demo-main-light');

  // 暗色主题
  await page.getByTitle('切换到暗色模式').click();
  await page.waitForTimeout(900);
  await shot(page, 'demo-main-dark');

  // 媒体分布详情弹窗（点第一行媒体格）
  await page.locator('button[title*="媒体共"]').first().click();
  await page.waitForTimeout(900);
  await shot(page, 'demo-media-detail');
  await page.keyboard.press('Escape');
  await page.waitForTimeout(600);

  // 概览展开（图表看板），失败不阻断
  try {
    await page.getByRole('button', { name: /概览/ }).first().click();
    await page.waitForTimeout(1500);
    await shot(page, 'demo-overview');
  } catch (e) { console.log('overview skip:', e.message.split('\n')[0]); }

  // 切回正常模式（恢复用户真实会话）
  await page.getByRole('button', { name: '正常', exact: true }).click();
  await page.waitForTimeout(4000);
  console.log('done, back to real mode');
} finally {
  await browser.close();
}
