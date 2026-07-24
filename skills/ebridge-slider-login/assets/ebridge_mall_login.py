# -*- coding: utf-8 -*-
"""
神州商桥商城完整登录脚本 (dcOauthLogin SSO 链)
================================================================

功能: 登录并获得【商城已认证会话】(首页显示"你好,公司名",
      可调 /homePage/ajaxGetUserInfo 等已认证接口)

与 ebidge_login.py 的区别:
    ebidge_login.py     → 走 /dcNewOauthLogin/login?, 仅验证会员系统登录成功,
                          拿到的 JSESSIONID 不能认证商城
    ebridge_mall_login.py → 走 /dcOauthLogin SSO 链, 获得真正的商城会话 (本脚本)

依赖: pip install opencv-python-headless numpy requests

使用方法:
    # 命令行
    python ebridge_mall_login.py --username <账号> --password "<密码>"

    # 作为模块
    from ebridge_mall_login import EbridgeMallLogin
    client = EbridgeMallLogin()
    result = client.login("<账号>", "<密码>")
    print(result["user_info"])   # companyName / user 等

流程:
    A. GET  /login                        → 302 authorize(存SavedRequest)
                                            → 302 /dcOauthLogin/mall/login
    B. POST /dcOauthLogin/checkImage/getImages
    C. OpenCV 边缘NCC匹配                  → moveX (复用 ebidge_login.solve_slider)
    D. POST /dcOauthLogin/checkImage/checkImageMatch
    E. POST /dcOauthLogin/login?          → 302 authorize(发code)
                                            → 302 /client/callback?code=xxx
                                            → 200 商城首页
    F. POST /homePage/ajaxGetUserInfo     → 已认证用户信息
"""

import sys
import argparse
import warnings

# Windows 控制台 UTF-8 输出
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

import requests

# 同目录导入滑块求解器 (文件名 typo: ebidge_login)
from ebidge_login import solve_slider, COMMON_HEADERS, MAX_RETRIES

warnings.filterwarnings("ignore", message="Unverified HTTPS request")


# ============================================================
# 配置
# ============================================================

HOST = "https://t-opservice.e-bridge.com.cn"
OAUTH_BASE = HOST + "/dcOauthLogin"


# ============================================================
# 商城登录客户端
# ============================================================

class EbridgeMallLogin:
    """神州商桥商城登录客户端 (dcOauthLogin SSO 链)"""

    def __init__(self, verify_ssl: bool = False):
        self.session = requests.Session()
        self.session.verify = verify_ssl
        self.session.headers.update(COMMON_HEADERS)
        self.session.headers["Referer"] = OAUTH_BASE + "/mall/login"

    def _trigger_oauth_chain(self) -> str:
        """
        Step A: GET /login 触发 OAuth 链。

        未登录时 authorize 会把 OAuth 请求(SavedRequest)存进
        dcOauthLogin 会话, 并重定向到 dcOauthLogin 自己的登录页。
        这一步同时拿到 JSESSIONID(Path=/) 和 lsessionid(Path=/dcOauthLogin)。

        返回: 落地页 URL (正常应为 .../dcOauthLogin/mall/login)
        """
        resp = self.session.get(
            HOST + "/login",
            headers={"Accept": "text/html"},
            allow_redirects=True,
        )
        resp.raise_for_status()
        return resp.url

    def _solve_captcha(self) -> bool:
        """
        Step B~D: 获取验证码 → OpenCV求解 → 验证滑块 (带重试)。

        接口在 /dcOauthLogin/checkImage/* 下, 机制与 dcNewOauthLogin 完全相同
        (图片 308x150 / 50x50, 显示1:1, moveX 即像素值)。
        """
        for _ in range(MAX_RETRIES):
            resp = self.session.post(OAUTH_BASE + "/checkImage/getImages", data={})
            resp.raise_for_status()
            d = resp.json()
            if not d.get("success"):
                continue
            data = d["data"]

            sr = solve_slider(data["srcImage"], data["markImage"], data["locationY"])
            if not sr["success"]:
                continue

            resp = self.session.post(
                OAUTH_BASE + "/checkImage/checkImageMatch",
                data={"moveX": sr["move_x"]},
            )
            resp.raise_for_status()
            if resp.json().get("success", False):
                return True
        return False

    def _submit_login(self, username: str, password: str) -> dict:
        """
        Step E: 提交登录表单并跟随重定向完成 SSO。

        重定向链: /dcOauthLogin/login → /login → authorize(发code)
                  → /client/callback?code=xxx&state=xxx → 商城首页
        """
        resp = self.session.post(
            OAUTH_BASE + "/login?",
            data={
                "username": username,
                "password": password,   # 明文传输
                "clientId": "",
                "mainVersion": "-1",
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            allow_redirects=True,
        )
        return {
            "success": "error" not in resp.url.lower(),
            "final_url": resp.url,
            "redirects": [h.status_code for h in resp.history],
        }

    def get_user_info(self) -> dict:
        """
        Step F: 调商城已认证接口, 获取当前登录用户信息。

        返回 ajaxGetUserInfo 的 JSON:
            data.companyName          → 首页"你好,"后面显示的公司名
            data.user.userName        → 登录名
            data.user.userRealName    → 真实姓名
            data.user.primaryAccount  → 是否主账号(子账号首页显示 公司名_用户名)

        若返回 {"success": false, "error": "获取用户信息失败"}
        说明商城会话未建立(不是账号密码问题)。
        """
        resp = self.session.post(
            HOST + "/homePage/ajaxGetUserInfo",
            data={},
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Referer": HOST + "/",
            },
        )
        resp.raise_for_status()
        return resp.json()

    def login(self, username: str, password: str, verbose: bool = True) -> dict:
        """
        完整登录流程。

        返回:
            {
                "success": bool,      # 是否获得商城已认证会话
                "final_url": str,     # 登录后的落地页
                "user_info": dict,    # ajaxGetUserInfo 返回(成功时)
                "error": str,
            }
        """
        result = {"success": False, "final_url": "", "user_info": None, "error": ""}

        try:
            if verbose:
                print("[A] GET /login 触发 OAuth 链...")
            landing = self._trigger_oauth_chain()
            if verbose:
                print(f"    落地: {landing}")
            if "/dcOauthLogin/mall/login" not in landing:
                result["error"] = f"未落到 dcOauthLogin 登录页: {landing}"
                return result

            if verbose:
                print("[B~D] 滑块验证码求解...")
            if not self._solve_captcha():
                result["error"] = f"滑块验证 {MAX_RETRIES} 次均失败"
                return result
            if verbose:
                print("    滑块验证通过")

            if verbose:
                print(f"[E] POST /dcOauthLogin/login? (username={username})...")
            lr = self._submit_login(username, password)
            result["final_url"] = lr["final_url"]
            if verbose:
                print(f"    落地: {lr['final_url']}  重定向链: {lr['redirects']}")
            if not lr["success"]:
                result["error"] = "账号或密码错误(重定向链中含 error)"
                return result

            if verbose:
                print("[F] POST /homePage/ajaxGetUserInfo ...")
            ui = self.get_user_info()
            result["user_info"] = ui
            if ui.get("success"):
                result["success"] = True
                if verbose:
                    d = ui["data"]
                    print(f"    你好,{d.get('companyName')}  "
                          f"(登录名: {d.get('user', {}).get('userName')}, "
                          f"主账号: {d.get('user', {}).get('primaryAccount')})")
            else:
                result["error"] = f"商城会话未建立: {ui.get('error')}"

        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}"
            if verbose:
                print(f"[ERROR] {result['error']}")

        return result


# ============================================================
# 命令行入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="神州商桥商城登录 (dcOauthLogin SSO 链)",
    )
    parser.add_argument("-u", "--username", required=True, help="登录用户名(必须由用户提供)")
    parser.add_argument("-p", "--password", required=True, help="登录密码(必须由用户提供)")
    parser.add_argument("-q", "--quiet", action="store_true", help="静默模式")
    args = parser.parse_args()

    client = EbridgeMallLogin()
    result = client.login(args.username, args.password, verbose=not args.quiet)

    print("\n" + "=" * 50)
    if result["success"]:
        print("商城登录成功!")
        print(f"  落地页: {result['final_url']}")
        d = result["user_info"]["data"]
        print(f"  公司名: {d.get('companyName')}")
        print(f"  登录名: {d.get('user', {}).get('userName')}")
        print(f"  姓名:   {d.get('user', {}).get('userRealName')}")
    else:
        print(f"登录失败: {result['error']}")
    print("=" * 50)

    sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
