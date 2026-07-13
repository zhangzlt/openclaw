const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

const SCREENSHOT_DIR = path.join(__dirname, 'screenshots');
fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });

async function screenshot(page, name) {
  const filepath = path.join(SCREENSHOT_DIR, `${name}.png`);
  await page.screenshot({ path: filepath, fullPage: true });
  console.log(`📸 截图已保存: ${filepath}`);
  return filepath;
}

async function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

(async () => {
  const browser = await chromium.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox']
  });

  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    locale: 'zh-CN'
  });

  const page = await context.newPage();

  try {
    // ============ Step 1: 导航到登录页面 ============
    console.log('🚀 Step 1: 导航到 https://agent.digitalchina.com/market ...');
    await page.goto('https://agent.digitalchina.com/market', {
      waitUntil: 'networkidle',
      timeout: 30000
    });
    await sleep(2000);
    await screenshot(page, '01-initial-page');

    // ============ Step 2: 填写登录表单 ============
    console.log('🔐 Step 2: 填写登录表单 ...');

    // 查找用户名输入框
    const usernameSelectors = [
      'input[name="username"]',
      'input[name="account"]',
      'input[type="text"][placeholder*="账号"]',
      'input[placeholder*="用户名"]',
      'input[placeholder*="手机"]',
      'input[placeholder*="邮箱"]',
      'input[name="mobile"]',
      'input[name="phone"]',
    ];

    let usernameInput = null;
    for (const sel of usernameSelectors) {
      const el = page.locator(sel);
      if (await el.count() > 0) {
        usernameInput = el;
        console.log(`  找到用户名输入框: ${sel}`);
        break;
      }
    }

    if (!usernameInput) {
      // 尝试用更通用的方式获取所有 input
      console.log('  尝试获取所有 input 元素...');
      const inputs = page.locator('input[type="text"], input:not([type])');
      const count = await inputs.count();
      console.log(`  找到 ${count} 个文本输入框`);
      for (let i = 0; i < count && i < 5; i++) {
        const placeholder = await inputs.nth(i).getAttribute('placeholder');
        const name = await inputs.nth(i).getAttribute('name');
        console.log(`  Input ${i}: name="${name}", placeholder="${placeholder}"`);
      }
      if (count > 0) {
        usernameInput = inputs.first();
        console.log('  使用第一个文本输入框作为用户名输入');
      }
    }

    if (usernameInput) {
      await usernameInput.click();
      await usernameInput.fill('zhangzlt');
      console.log('  ✅ 用户名已填写');
    } else {
      console.log('  ❌ 未找到用户名输入框');
      // 尝试看当前页面是否有登录入口
      const loginButtons = page.locator('button:has-text("登录"), a:has-text("登录"), button:has-text("Login"), a:has-text("Login")');
      const loginBtnCount = await loginButtons.count();
      console.log(`  找到 ${loginBtnCount} 个登录按钮`);
      if (loginBtnCount > 0) {
        await loginButtons.first().click();
        await sleep(3000);
        await screenshot(page, '01b-after-click-login');
      }
    }

    // 查找密码输入框
    const passwordSelectors = [
      'input[type="password"]',
      'input[name="password"]',
      'input[placeholder*="密码"]',
    ];

    let passwordInput = null;
    for (const sel of passwordSelectors) {
      const el = page.locator(sel);
      if (await el.count() > 0) {
        passwordInput = el;
        console.log(`  找到密码输入框: ${sel}`);
        break;
      }
    }

    if (passwordInput) {
      await passwordInput.click();
      await passwordInput.fill('Zzl.20041006');
      console.log('  ✅ 密码已填写');
    } else {
      console.log('  ❌ 未找到密码输入框');
    }

    await screenshot(page, '02-filled-form');

    // ============ Step 3: 提交登录 ============
    console.log('🔑 Step 3: 提交登录 ...');

    const submitSelectors = [
      'button[type="submit"]',
      'button:has-text("登录")',
      'button:has-text("登 录")',
      'button:has-text("Login")',
      'input[type="submit"]',
      '.login-btn',
      '#login-btn',
    ];

    let submitted = false;
    for (const sel of submitSelectors) {
      const el = page.locator(sel);
      if (await el.count() > 0) {
        await el.first().click();
        console.log(`  点击提交按钮: ${sel}`);
        submitted = true;
        break;
      }
    }

    if (!submitted && passwordInput) {
      console.log('  未找到提交按钮，尝试按 Enter');
      await passwordInput.press('Enter');
      submitted = true;
    }

    if (!submitted) {
      console.log('  ⚠️ 未找到提交方式');
    }

    // 等待登录完成
    await sleep(5000);
    await screenshot(page, '03-after-login');

    // ============ Step 4: 进入智能体市场 ============
    console.log('🏪 Step 4: 进入智能体市场 ...');

    // 获取当前页面标题和URL
    const currentUrl = page.url();
    const currentTitle = await page.title();
    console.log(`  当前页面: ${currentTitle} | ${currentUrl}`);

    // 尝试找智能体市场的入口
    const marketLinks = [
      'a:has-text("智能体市场")',
      'a:has-text("市场")',
      'a:has-text("Market")',
      'button:has-text("智能体市场")',
      'button:has-text("市场")',
      'span:has-text("智能体市场")',
      'li:has-text("智能体市场")',
      'div:has-text("智能体市场")',
      '[href*="market"]',
    ];

    let marketFound = false;
    for (const sel of marketLinks) {
      const el = page.locator(sel).first();
      if (await el.count() > 0 && await el.isVisible()) {
        console.log(`  找到市场入口: ${sel}`);
        await el.click();
        marketFound = true;
        await sleep(3000);
        break;
      }
    }

    if (!marketFound) {
      // 如果当前已经在市场页面，跳过导航
      if (currentUrl.includes('market')) {
        console.log('  当前已在市场页面，跳过导航');
        marketFound = true;
      } else {
        // 尝试直接导航
        console.log('  尝试直接导航到市场页面...');
        await page.goto('https://agent.digitalchina.com/market', {
          waitUntil: 'networkidle',
          timeout: 15000
        }).catch(() => {});
        await sleep(3000);
      }
    }

    await screenshot(page, '04-market-page');

    // ============ Step 5: 点击智能体的"打开"按钮 ============
    console.log('🎯 Step 5: 查找并点击智能体的"打开"按钮 ...');

    // 先截图看整个市场页面
    const marketUrl = page.url();
    console.log(`  市场页面 URL: ${marketUrl}`);

    // 查找"打开"按钮
    const openButtons = page.locator('button:has-text("打开"), a:has-text("打开"), span:has-text("打开")');
    const openCount = await openButtons.count();
    console.log(`  找到 ${openCount} 个"打开"按钮`);

    if (openCount > 0) {
      // 点击第一个可见的打开按钮
      for (let i = 0; i < openCount; i++) {
        const btn = openButtons.nth(i);
        if (await btn.isVisible()) {
          console.log(`  点击第 ${i + 1} 个打开按钮`);
          const btnText = await btn.textContent();
          console.log(`  按钮文本: "${btnText}"`);
          await btn.click();
          break;
        }
      }
    } else {
      // 尝试找其他可能的操作按钮
      console.log('  未找到"打开"按钮，查找其他可能的按钮...');
      const allButtons = page.locator('button');
      const allCount = await allButtons.count();
      console.log(`  页面共有 ${allCount} 个按钮`);
      
      for (let i = 0; i < Math.min(allCount, 20); i++) {
        const btn = allButtons.nth(i);
        if (await btn.isVisible()) {
          const text = await btn.textContent();
          console.log(`  按钮 ${i}: "${text?.trim()}"`);
        }
      }
    }

    // 等待跳转/弹窗
    await sleep(5000);
    await screenshot(page, '05-after-click-open');

    // ============ Step 6: 检测飞书登录 ============
    console.log('🔍 Step 6: 检测当前状态 ...');

    // 检查是否有新页面打开
    const pages = context.pages();
    console.log(`  当前打开的页面数: ${pages.length}`);
    for (let i = 0; i < pages.length; i++) {
      const p = pages[i];
      console.log(`  页面 ${i}: ${p.url()}`);
      await p.bringToFront();
      await sleep(1000);
      await p.screenshot({
        path: path.join(SCREENSHOT_DIR, `06-page-${i}.png`),
        fullPage: true
      });
      console.log(`  📸 页面 ${i} 截图已保存`);
    }

    // 检查当前页面URL是否包含飞书
    const finalUrl = page.url();
    console.log(`  最终页面 URL: ${finalUrl}`);

    if (finalUrl.includes('feishu') || finalUrl.includes('lark') || finalUrl.includes('passport')) {
      console.log('  🔔 检测到飞书登录页面！需要扫码登录');
      await screenshot(page, '06-feishu-login-qrcode');
    } else if (finalUrl.includes('agent') && !finalUrl.includes('market')) {
      console.log('  ✅ 已成功登录并进入智能体页面！');
      await screenshot(page, '06-agent-page-success');
    } else {
      console.log('  ⚠️ 当前状态未知，保存当前页面截图');
      await screenshot(page, '06-current-state');
    }

    console.log('\n✅ 脚本执行完毕');
    console.log(`📁 所有截图保存在: ${SCREENSHOT_DIR}`);

  } catch (error) {
    console.error('❌ 脚本出错:', error.message);
    await screenshot(page, 'error-state');
  } finally {
    // 不关闭浏览器，让你可以看到结果
    console.log('🌐 浏览器保持打开状态，可以手动操作');
    // await browser.close();
  }
})();
