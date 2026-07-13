const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

const DIR = path.join(__dirname, 'screenshots');
fs.mkdirSync(DIR, { recursive: true });

async function ss(page, name) {
  try {
    const fp = path.join(DIR, `${name}.png`);
    await page.screenshot({ path: fp, fullPage: true, timeout: 15000 });
    console.log(`📸 ${fp}`);
    return fp;
  } catch (e) { console.log(`⚠️ 截图失败: ${e.message.split('\\n')[0]}`); return null; }
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const browser = await chromium.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox']
  });

  try {
    // ===== 用独立 context 登录 agent.digitalchina.com =====
    console.log('🚀 登录 agent.digitalchina.com ...');
    const marketCtx = await browser.newContext({
      viewport: { width: 1440, height: 900 },
      locale: 'zh-CN'
    });
    const marketPage = await marketCtx.newPage();
    
    await marketPage.goto('https://agent.digitalchina.com/market', { 
      waitUntil: 'networkidle', timeout: 30000 
    });
    await sleep(2000);

    if (await marketPage.locator('input[placeholder="请输入itcode"]').count() > 0) {
      await marketPage.locator('input[placeholder="请输入itcode"]').fill('zhangzlt');
      await marketPage.locator('input[type="password"]').fill('Zzl.20041006');
      await marketPage.locator('button[type="submit"]').click();
      await sleep(4000);
      console.log('  ✅ 已登录');
    } else {
      console.log('  ✅ 已有登录态');
    }
    await ss(marketPage, '01-market');

    // ===== 用独立 context 打开飞书登录页 =====
    console.log('🎯 打开飞书登录页...');
    const feishuCtx = await browser.newContext({
      viewport: { width: 1440, height: 900 },
      locale: 'zh-CN'
    });
    const feishuPage = await feishuCtx.newPage();
    
    const feishuUrl = 'https://accounts.feishu.cn/accounts/page/login?app_id=149&no_trap=1&query_scope=all&redirect_uri=https%3A%2F%2Faily.feishu.cn%2Fai%2Fagents%2Fagent_4jn4cnjeurc3r';
    
    // 用 commit 最快加载，避免超时
    await feishuPage.goto(feishuUrl, { 
      waitUntil: 'commit', 
      timeout: 15000 
    }).catch(e => console.log(`  goto: ${e.message.split('\\n')[0]}`));
    
    // 等待 DOM 加载
    await feishuPage.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => {});
    await sleep(5000);
    
    console.log(`  URL: ${feishuPage.url()}`);
    console.log(`  标题: ${await feishuPage.title().catch(() => '?')}`);
    
    await ss(feishuPage, '02-feishu-login-qrcode');

    // ===== 等待扫码 =====
    console.log('🔍 等待扫码（180秒），请尽快扫码！');
    
    const start = Date.now();
    let loggedIn = false;

    while (Date.now() - start < 180000) {
      try {
        const url = feishuPage.url();
        
        if (url.includes('aily.feishu.cn/ai/agents/') && !url.includes('login') && !url.includes('accounts')) {
          console.log(`\n✅✅✅ 登录成功！已进入智能体`);
          console.log(`  URL: ${url}`);
          loggedIn = true;
          break;
        }

        if (/(?:oauth|authorize|consent|grant)/i.test(url)) {
          console.log('  ⚠️ 授权页面');
          await ss(feishuPage, `auth-confirm`);
          for (const t of ['授权', '同意', '确认', '允许', '继续', 'Accept']) {
            try {
              const btn = feishuPage.locator(`button:has-text("${t}")`).first();
              if (await btn.count() > 0 && await btn.isVisible({ timeout: 500 }).catch(() => false)) {
                await btn.click();
                console.log(`  ✅ ${t}`);
                await sleep(3000);
                break;
              }
            } catch (_) {}
          }
        }

        const elapsed = Math.floor((Date.now() - start) / 1000);
        if (elapsed % 15 === 0 || elapsed < 10) {
          console.log(`  [${elapsed}s] ${url.substring(0, 85)}`);
        }
      } catch (e) {
        console.log(`  ⚠️ ${e.message.split('\\n')[0]}`);
      }
      await sleep(3000);
    }

    // ===== 保存状态 =====
    if (loggedIn) {
      console.log('\n🎉🎉🎉 成功进入智能体！');
      await sleep(3000);
      await ss(feishuPage, '03-agent-success');

      const state = await feishuCtx.storageState();
      fs.writeFileSync(path.join(__dirname, 'auth_state.json'), JSON.stringify(state, null, 2));
      const cookies = await feishuCtx.cookies();
      fs.writeFileSync(path.join(__dirname, 'cookies.json'), JSON.stringify(cookies, null, 2));
      console.log('  💾 登录态已保存: auth_state.json + cookies.json');
    } else {
      console.log('\n⏱️ 超时');
      try { await ss(feishuPage, 'timeout'); } catch (_) {}
    }

    console.log('🌐 运行中...');
    await new Promise(() => {});

  } catch (e) {
    console.error('❌', e.message);
  }
})();
