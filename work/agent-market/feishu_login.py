#!/usr/bin/env python3
"""用 agent-browser 建立可长期复用的飞书登录 profile。

脚本只负责打开可视浏览器并等待人工完成密码、验证码或扫码验证，不在源码、
命令行或日志中保存密码。成功后关闭浏览器，Chrome profile 会自动持久化。
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_URL = "https://aily.feishu.cn/agents/agent_4jn4cnjeurc3r"
DEFAULT_PROFILE = ROOT / ".auth" / "feishu-browser-profile"


def find_agent_browser() -> str:
    """定位 agent-browser；Windows 优先绕过 npm .cmd，直接调用原生程序。"""
    configured = os.environ.get("AGENT_BROWSER_BIN", "").strip()
    candidates: list[str] = []

    def add_candidate(candidate: str | None) -> None:
        if not candidate:
            return
        path = Path(candidate)
        if os.name == "nt" and path.suffix.lower() == ".cmd":
            native_dir = path.parent / "node_modules" / "agent-browser" / "bin"
            candidates.extend(
                str(native)
                for native in sorted(native_dir.glob("agent-browser-win32-*.exe"))
            )
        candidates.append(candidate)

    if configured:
        add_candidate(shutil.which(configured) or configured)
    if os.name == "nt":
        add_candidate(shutil.which("agent-browser.exe"))
        add_candidate(shutil.which("agent-browser.cmd"))
    add_candidate(shutil.which("agent-browser"))

    for candidate in candidates:
        if Path(candidate).is_file():
            return str(Path(candidate).resolve())

    raise FileNotFoundError(
        "未找到 agent-browser。请先执行 npm install -g agent-browser，"
        "或通过 AGENT_BROWSER_BIN 指定 agent-browser.cmd 的完整路径。"
    )


def run_agent_browser(
    session: str,
    profile: Path,
    *args: str,
    check: bool = True,
    timeout_seconds: int = 40,
) -> str:
    env = os.environ.copy()
    env["AGENT_BROWSER_PROFILE"] = str(profile)
    env["AGENT_BROWSER_HEADED"] = "true"
    command = [find_agent_browser(), "--session", session, *args]
    # Windows 下 agent-browser 会启动常驻 daemon。若使用 PIPE，daemon 会继承管道，
    # 导致 subprocess 超时后仍无法返回；临时文件可避免这个句柄继承死锁。
    with tempfile.TemporaryFile(mode="w+b") as output:
        try:
            completed = subprocess.run(
                command,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            action = " ".join(args[:2]) or "unknown"
            raise RuntimeError(
                f"agent-browser 命令超时（{timeout_seconds} 秒）：{action}"
            ) from exc
        output.seek(0)
        result = output.read().decode("utf-8", errors="replace").strip()

    if check and completed.returncode != 0:
        raise RuntimeError(result or f"agent-browser 退出码：{completed.returncode}")
    return result


def login(target_url: str, profile: Path, wait_seconds: int) -> bool:
    profile.mkdir(parents=True, exist_ok=True)
    session = f"feishu-login-{int(time.time())}"
    print(f"正在打开飞书登录窗口，持久 profile：{profile}")
    success = False
    try:
        run_agent_browser(session, profile, "open", target_url)
        print("请在浏览器中完成手机号、密码、验证码或扫码验证。")
        print("脚本不会读取或记录你的密码。")

        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            time.sleep(2)
            current_url = run_agent_browser(session, profile, "get", "url", check=False)
            if "aily.feishu.cn/agents/" not in current_url:
                continue
            body = run_agent_browser(session, profile, "get", "text", "body", check=False)
            if "登录" not in body and len(body) > 20:
                success = True
                screenshot = profile.parent / "feishu-login-success.png"
                run_agent_browser(session, profile, "screenshot", str(screenshot), check=False)
                print(f"登录成功，验证截图：{screenshot}")
                break
    finally:
        try:
            run_agent_browser(
                session, profile, "close", check=False, timeout_seconds=10
            )
        except RuntimeError:
            pass

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
