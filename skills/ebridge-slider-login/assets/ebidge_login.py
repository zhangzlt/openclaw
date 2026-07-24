"""
神州商桥 (t-opservice.e-bridge.com.cn) 滑块验证码自动登录脚本
================================================================

功能: 自动完成滑块拼图验证码 + 表单登录
依赖: pip install opencv-python-headless numpy requests

使用方法:
    # 命令行
    python ebridge_login.py --username zhangzlt --password "Zzl.20041006"

    # 作为模块
    from ebridge_login import EbridgeLogin
    client = EbridgeLogin()
    result = client.login("zhangzlt", "Zzl.20041006")
    print(result)

流程:
    1. GET  /dcNewOauthLogin/mall/login          → 获取 lsessionid cookie
    2. POST /dcNewOauthLogin/checkImage/getImages → 获取验证码图片
    3. OpenCV 边缘NCC匹配                        → 计算 moveX
    4. POST /dcNewOauthLogin/checkImage/checkImageMatch → 验证滑块
    5. POST /dcNewOauthLogin/login?              → 表单登录
"""

import sys
import base64
import argparse
import warnings

# Windows 控制台 UTF-8 输出
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

import requests
import numpy as np
import cv2

# 禁用 SSL 警告(测试环境证书不受信任)
warnings.filterwarnings("ignore", message="Unverified HTTPS request")


# ============================================================
# 配置
# ============================================================

BASE_URL = "https://t-opservice.e-bridge.com.cn/dcNewOauthLogin"

# 通用请求头
COMMON_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Referer": f"{BASE_URL}/mall/login",
}

# 求解算法参数
CANNY_LOW = 100       # Canny 边缘检测低阈值
CANNY_HIGH = 200      # Canny 边缘检测高阈值
Y_JITTER = 20         # Y方向搜索范围 (locationY ± Y_JITTER)
X_MIN_SEARCH = 35     # X最小搜索位置(排除拼图块初始区域)
X_RIGHT_MARGIN = 10   # X右边距排除
MAX_RETRIES = 3       # 验证失败时最大重试次数


# ============================================================
# 核心求解函数
# ============================================================

def solve_slider(src_b64: str, mark_b64: str, location_y: int) -> dict:
    """
    用 OpenCV 边缘 NCC 匹配计算滑块需要移动的像素距离。

    参数:
        src_b64:    背景图 base64 字符串 (PNG, 308x150, BGR)
        mark_b64:   拼图块 base64 字符串 (PNG, 50x50, BGRA 带alpha)
        location_y: 服务器返回的缺口Y坐标

    返回:
        {
            "move_x": int,      # 需要提交的 moveX 值
            "gap_x": int,       # 缺口在背景图中的X坐标
            "min_x": int,       # 拼图块有效内容左边距
            "score": float,     # 匹配置信度 (0~1)
            "success": bool,    # 是否成功计算
            "error": str        # 错误信息(成功时为空)
        }
    """
    result = {"move_x": 0, "gap_x": 0, "min_x": 0, "score": 0.0, "success": False, "error": ""}

    try:
        # 1. 解码图片
        bg_bytes = base64.b64decode(src_b64)
        mark_bytes = base64.b64decode(mark_b64)

        bg_arr = np.frombuffer(bg_bytes, dtype=np.uint8)
        mark_arr = np.frombuffer(mark_bytes, dtype=np.uint8)

        bg = cv2.imdecode(bg_arr, cv2.IMREAD_COLOR)        # BGR, (150, 308, 3)
        mark = cv2.imdecode(mark_arr, cv2.IMREAD_UNCHANGED) # BGRA, (50, 50, 4)

        if bg is None:
            result["error"] = "背景图解码失败"
            return result
        if mark is None:
            result["error"] = "拼图块解码失败"
            return result

        # 2. 提取 alpha 通道, 找有效区域边界
        if mark.shape[2] == 4:
            alpha = mark[:, :, 3]
            mark_bgr = mark[:, :, :3]
        else:
            # 如果没有 alpha 通道, 用全白 mask
            alpha = np.ones(mark.shape[:2], dtype=np.uint8) * 255
            mark_bgr = mark

        # 找每列有非透明像素的列
        col_has_content = alpha.max(axis=0) > 0
        cols = np.where(col_has_content)[0]
        if len(cols) == 0:
            result["error"] = "拼图块 alpha 通道全透明, 无法定位"
            return result

        min_x = int(cols[0])   # 有效内容最左列
        max_x = int(cols[-1]) + 1

        # 找每行有非透明像素的行
        row_has_content = alpha.max(axis=1) > 0
        rows = np.where(row_has_content)[0]
        if len(rows) == 0:
            result["error"] = "拼图块 alpha 通道全透明(行方向)"
            return result

        min_y_mark = int(rows[0])
        max_y_mark = int(rows[-1]) + 1

        # 3. 裁剪拼图块到有效区域
        mark_crop = mark_bgr[min_y_mark:max_y_mark, min_x:max_x]

        # 4. Canny 边缘检测
        bg_gray = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY)
        mark_gray = cv2.cvtColor(mark_crop, cv2.COLOR_BGR2GRAY)

        bg_edges = cv2.Canny(bg_gray, CANNY_LOW, CANNY_HIGH)
        mark_edges = cv2.Canny(mark_gray, CANNY_LOW, CANNY_HIGH)

        # 5. 确定搜索范围
        h_bg, w_bg = bg_edges.shape[:2]
        h_mk, w_mk = mark_edges.shape[:2]

        # Y 约束: locationY ± Y_JITTER
        y_start = max(0, location_y - Y_JITTER)
        y_end = min(h_bg - h_mk, location_y + Y_JITTER)
        if y_end <= y_start:
            y_start = 0
            y_end = h_bg - h_mk

        # 提取 ROI
        roi = bg_edges[y_start:y_end + h_mk, :]

        if roi.shape[0] < h_mk or roi.shape[1] < w_mk:
            result["error"] = f"ROI 尺寸不足: roi={roi.shape}, mark={mark_edges.shape}"
            return result

        # 6. 模板匹配
        match_result = cv2.matchTemplate(roi, mark_edges, cv2.TM_CCOEFF_NORMED)

        # 7. X 范围约束: 排除左侧初始位置和右边缘
        min_search = X_MIN_SEARCH
        max_search = match_result.shape[1] - 1
        if w_mk + X_RIGHT_MARGIN < match_result.shape[1]:
            max_search = match_result.shape[1] - X_RIGHT_MARGIN

        if max_search > min_search:
            # 将范围外的值设为 -1 (TM_CCOEFF_NORMED 最小值为 -1)
            match_result[0, :min_search] = -1
            match_result[0, max_search:] = -1

        # 8. 取最佳匹配位置
        _, max_val, _, max_loc = cv2.minMaxLoc(match_result)

        gap_x = max_loc[0]          # 缺口在背景图中的X (mark_crop 对齐位置)
        move_x = gap_x - min_x      # 实际需要提交的 moveX

        result["move_x"] = move_x
        result["gap_x"] = gap_x
        result["min_x"] = min_x
        result["score"] = float(max_val)
        result["success"] = True

    except Exception as e:
        result["error"] = f"求解异常: {type(e).__name__}: {str(e)}"

    return result


# ============================================================
# 登录客户端类
# ============================================================

class EbridgeLogin:
    """神州商桥自动登录客户端"""

    def __init__(self, base_url: str = BASE_URL, verify_ssl: bool = False):
        """
        初始化客户端。

        参数:
            base_url:    登录系统基础URL
            verify_ssl:  是否验证SSL证书(测试环境建议False)
        """
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.verify = verify_ssl
        self.session.headers.update(COMMON_HEADERS)

    def _get_captcha(self) -> dict:
        """
        获取验证码图片。

        返回:
            {"srcImage": str, "markImage": str, "locationY": int}
            失败时抛出异常
        """
        url = f"{self.base_url}/checkImage/getImages"
        resp = self.session.post(url, data={})
        resp.raise_for_status()
        data = resp.json()

        if not data.get("success"):
            raise RuntimeError(f"getImages 返回失败: {data}")

        return {
            "srcImage": data["data"]["srcImage"],
            "markImage": data["data"]["markImage"],
            "locationY": data["data"]["locationY"],
        }

    def _verify_slider(self, move_x: int) -> bool:
        """
        提交滑块验证。

        参数:
            move_x: 计算出的移动像素值

        返回:
            True=验证通过, False=验证失败
        """
        url = f"{self.base_url}/checkImage/checkImageMatch"
        resp = self.session.post(url, data={"moveX": move_x})
        resp.raise_for_status()
        return resp.json().get("success", False)

    def _submit_login(self, username: str, password: str) -> dict:
        """
        提交登录表单。

        参数:
            username: 登录名
            password: 密码(明文)

        返回:
            {"success": bool, "status_code": int, "redirect_url": str}
        """
        url = f"{self.base_url}/login?"
        resp = self.session.post(
            url,
            data={
                "username": username,
                "password": password,
                "clientId": "",
                "mainVersion": "-1",
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            allow_redirects=False,
        )

        location = resp.headers.get("Location", "")
        success = (resp.status_code == 302) and ("error" not in location)

        return {
            "success": success,
            "status_code": resp.status_code,
            "redirect_url": location,
        }

    def login(self, username: str, password: str, verbose: bool = True) -> dict:
        """
        完整登录流程: 获取session → 获取验证码 → 求解 → 验证 → 登录

        参数:
            username: 登录名
            password: 密码(明文)
            verbose:  是否打印过程信息

        返回:
            {
                "success": bool,       # 登录是否成功
                "move_x": int,         # 使用的 moveX 值
                "score": float,        # 匹配置信度
                "redirect_url": str,   # 登录成功后的重定向URL
                "attempts": int,       # 滑块验证尝试次数
                "error": str           # 错误信息(成功时为空)
            }
        """
        result = {
            "success": False,
            "move_x": 0,
            "score": 0.0,
            "redirect_url": "",
            "attempts": 0,
            "error": "",
        }

        try:
            # Step 1: 访问登录页获取 session cookie
            if verbose:
                print("[1] 访问登录页获取 session...")
            resp = self.session.get(
                f"{self.base_url}/mall/login",
                headers={"Accept": "text/html"},
            )
            resp.raise_for_status()
            if verbose:
                cookies = dict(self.session.cookies)
                print(f"    Status: {resp.status_code}, Cookies: {list(cookies.keys())}")

            # Step 2~4: 获取验证码 → 求解 → 验证 (带重试)
            for attempt in range(1, MAX_RETRIES + 1):
                result["attempts"] = attempt

                # Step 2: 获取验证码
                if verbose:
                    print(f"\n[2] 获取验证码图片 (第{attempt}次)...")
                captcha = self._get_captcha()
                if verbose:
                    print(f"    srcImage: {len(captcha['srcImage'])} chars, "
                          f"markImage: {len(captcha['markImage'])} chars, "
                          f"locationY: {captcha['locationY']}")

                # Step 3: OpenCV 求解
                if verbose:
                    print("[3] OpenCV 求解缺口位置...")
                solve_result = solve_slider(
                    captcha["srcImage"],
                    captcha["markImage"],
                    captcha["locationY"],
                )

                if not solve_result["success"]:
                    if verbose:
                        print(f"    求解失败: {solve_result['error']}")
                    continue

                move_x = solve_result["move_x"]
                result["move_x"] = move_x
                result["score"] = solve_result["score"]

                if verbose:
                    print(f"    gap_x={solve_result['gap_x']}, "
                          f"min_x={solve_result['min_x']}, "
                          f"move_x={move_x}, "
                          f"score={solve_result['score']:.4f}")

                # Step 4: 验证滑块
                if verbose:
                    print(f"[4] 验证滑块 (moveX={move_x})...")
                verified = self._verify_slider(move_x)

                if verbose:
                    print(f"    结果: {'通过' if verified else '失败'}")

                if verified:
                    break
            else:
                result["error"] = f"滑块验证 {MAX_RETRIES} 次均失败"
                return result

            # Step 5: 登录
            if verbose:
                print(f"\n[5] 提交登录 (username={username})...")
            login_result = self._submit_login(username, password)

            result["success"] = login_result["success"]
            result["redirect_url"] = login_result["redirect_url"]

            if verbose:
                if login_result["success"]:
                    print(f"    登录成功! 重定向到: {login_result['redirect_url']}")
                else:
                    print(f"    登录失败 (账号或密码错误)")
                    print(f"    Status: {login_result['status_code']}, "
                          f"Location: {login_result['redirect_url']}")

        except Exception as e:
            result["error"] = f"{type(e).__name__}: {str(e)}"
            if verbose:
                print(f"\n[ERROR] {result['error']}")

        return result


# ============================================================
# 命令行入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="神州商桥滑块验证码自动登录",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python ebridge_login.py --username <账号> --password "<密码>"
  python ebridge_login.py -u <账号> -p "<密码>" --quiet
        """,
    )
    parser.add_argument("-u", "--username", required=True, help="登录用户名(必须由用户提供)")
    parser.add_argument("-p", "--password", required=True, help="登录密码(必须由用户提供)")
    parser.add_argument("-q", "--quiet", action="store_true", help="静默模式(不打印过程)")
    parser.add_argument("--base-url", default=BASE_URL, help="登录系统基础URL")

    args = parser.parse_args()

    client = EbridgeLogin(base_url=args.base_url)
    result = client.login(args.username, args.password, verbose=not args.quiet)

    # 输出最终结果
    print("\n" + "=" * 50)
    if result["success"]:
        print(f"登录成功!")
        print(f"  moveX: {result['move_x']}")
        print(f"  置信度: {result['score']:.4f}")
        print(f"  尝试次数: {result['attempts']}")
        print(f"  重定向: {result['redirect_url']}")
    else:
        print(f"登录失败!")
        print(f"  原因: {result['error'] or '账号或密码错误'}")
        print(f"  尝试次数: {result['attempts']}")
    print("=" * 50)

    # 返回码: 0=成功, 1=失败
    sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
