#!/usr/bin/env python3
"""用 agent-browser 建立可长期复用的飞书登录 profile。

脚本只负责打开可视浏览器并等待人工完成密码、验证码或扫码验证，不在源码、
命令行或日志中保存密码。成功后关闭浏览器，Chrome profile 会自动持久化。
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_URL = "https://aily.feishu.cn/agents/agent_4jn4cnjeurc3r"
DEFAULT_PROFILE = ROOT / ".auth" / "feishu-browser-profile"


def run_agent_browser(session: str, profile: Path, *args: str, check: bool = True) -> str:
    env = os.environ.copy()
    env["AGENT_BROWSER_PROFILE"] = str(profile)
    env["AGENT_BROWSER_HEADED"] = "true"
    completed = subprocess.run(
        ["agent-browser", "--session", session, *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=40,
    )
    if check and completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return completed.stdout.strip()


def login(target_url: str, profile: Path, wait_seconds: int) -> bool:
    profile.mkdir(parents=True, exist_ok=True)
    session = f"feishu-login-{int(time.time())}"
    print(f"正在打开飞书登录窗口，持久 profile：{profile}")
    run_agent_browser(session, profile, "open", target_url)
    print("请在浏览器中完成手机号、密码、验证码或扫码验证。")
    print("脚本不会读取或记录你的密码。")

    deadline = time.time() + wait_seconds
    success = False
    while time.time() < deadline:
        time.sleep(2)
        current_url = run_agent_browser(session, profile, "get", "url", check=False)
        body = run_agent_browser(session, profile, "get", "text", "body", check=False)
        if "aily.feishu.cn/agents/" in current_url and "登录" not in body and len(body) > 20:
            success = True
            screenshot = profile.parent / "feishu-login-success.png"
            run_agent_browser(session, profile, "screenshot", str(screenshot), check=False)
            print(f"登录成功，验证截图：{screenshot}")
            break

    run_agent_browser(session, profile, "close", check=False)
    if not success:
        print(f"等待 {wait_seconds} 秒后仍未确认登录成功，请重新运行脚本。")
    return success


def main() -> int:
    parser = argparse.ArgumentParser(description="建立飞书 agent-browser 持久登录态")
    parser.add_argument("--url", default=DEFAULT_URL, help="用于验证登录的 Aily 智能体 URL")
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE, help="持久 Chrome profile 目录")
    parser.add_argument("--wait", type=int, default=300, help="等待人工完成验证的秒数")
    args = parser.parse_args()
    return 0 if login(args.url, args.profile.expanduser().resolve(), args.wait) else 1


if __name__ == "__main__":
    sys.exit(main())
