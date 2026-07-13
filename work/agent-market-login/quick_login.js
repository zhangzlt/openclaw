const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

const DIR = path.join(__dirname, 'screenshots');
fs.mkdirSync(DIR, { recursive: true });

async function ss(page, name, retries = 3) {
  for (let i = 0; i < retries; i++) {
    try {
      const fp = path.join(DIR, `${name}.png`);
      await page.screenshot({ path: fp, fullPage: true, timeout: 15000 });
      console.log(`📸 ${fp}`);
      return fp;
    } catch (e) {
      console.log(`  ⚠️ 截图${i+1}/${retries}: ${e.message.split('\\n')[0]}`);
      if (i < retries - 1) await new Promise(r => setTimeout(r, 3000));
    }
  }
  return null;
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const browser = await chromium.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox']
  });

  try {
    // ===== Step 1: 飞书登录（先做，因为之前测试成功过） =====
    console.log('🎯 打开飞书登录页...');
    const feishuCtx = await browser.newContext({
      viewport: { width: 1440, height: 900 },
      locale: 'zh-CN'
    });
    const feishuPage = await feishuCtx.newPage();
    
    const feishuUrl = 'https://accounts.feishu.cn/accounts/page/login?app_id=149&no_trap=1&query_scope=all&redirect_uri=https%3A%2F%2Faily.feishu.cn%2Fai%2Fagents%2Fagent_4jn4cnjeurc3r';
    
    // 用 networkidle（会超时但页面可用）
    await feishuPage.goto(feishuUrl, { 
      waitUntil: 'networkidle', 
      timeout: 30000 
    }).catch(e => console.log(`  goto: ${e.message.split('\\n')[0]}`));
    
    console.log(`  goto 完成`);
    await sleep(5000);
    console.log(`  URL: ${feishuPage.url()}`);
    console.log(`  标题: ${await feishuPage.title().catch(() => '?')}`);
    console.log(`  HTML长度: ${(await feishuPage.content().catch(() => '')).length}`);
    
    await ss(feishuPage, '02-feishu-login');

    // ===== Step 2: 等扫码 =====
    console.log('\n🔍 等待扫码（180秒）！');
    const start = Date.now();
    let loggedIn = false;

    while (Date.now() - start < 180000) {
      try {
        const url = feishuPage.url();
        
        if (url.includes('aily.feishu.cn/ai/agents/') && !url.includes('login') && !url.includes('accounts')) {
          console.log(`\n✅✅✅ 登录成功！${url}`);
          loggedIn = true;
          break;
        }

        if (/(?:oauth|authorize|consent|grant)/i.test(url)) {
          console.log('  ⚠️ 授权页');
          await ss(feishuPage, `auth`);
          for (const t of ['授权','同意','确认','允许','继续','Accept']) {
            try {
              const b = feishuPage.locator(`button:has-text("${t}")`).first();
              if (await b.count()>0 && await b.isVisible({timeout:500}).catch(()=>false)) {
                await b.click(); console.log(`  ✅ ${t}`); await sleep(3000); break;
              }
            } catch(_){}
          }
        }

        const s = Math.floor((Date.now()-start)/1000);
        if (s%15===0||s<10) console.log(`  [${s}s] ${url.substring(0,85)}`);
      } catch(e) {
        console.log(`  ⚠️ ${e.message.split('\\n')[0]}`);
      }
      await sleep(3000);
    }

    if (loggedIn) {
      console.log('\n🎉 成功进入智能体！');
      await sleep(3000);
      await ss(feishuPage, '03-agent-success');
      const state = await feishuCtx.storageState();
      fs.writeFileSync(path.join(__dirname, 'auth_state.json'), JSON.stringify(state, null, 2));
      fs.writeFileSync(path.join(__dirname, 'cookies.json'), JSON.stringify(await feishuCtx.cookies(), null, 2));
      console.log('  💾 状态已保存');
    } else {
      console.log('\n⏱️ 超时');
      try { await ss(feishuPage, 'timeout'); } catch(_){}
    }

    console.log('🌐 运行中...');
    await new Promise(() => {});

  } catch(e) {
    console.error('❌', e.message);
  }
})();
