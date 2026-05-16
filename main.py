"""
Douyin Emoji Professional - P8 Edition (ABogus)
================================================
纯协议驱动 | curl_cffi Chrome TLS 指纹 | 纯Python ABogus签名
Cookie 登录 | 并发下载 | WebP→GIF 自动转换

依赖:
  pip install curl_cffi pillow gmssl --break-system-packages
"""

import json
import zipfile
import io
import os
import time
import random
import string
import concurrent.futures
from urllib.parse import urlencode, quote
from PIL import Image, ImageSequence

# curl_cffi 完美模拟 Chrome TLS 指纹，通过抖音 WAF
from curl_cffi import requests

# 纯 Python 实现的 ABogus 签名器（核心技术壁垒，绕过字节 WAF 校验）
from abogus import ABogus

"""
项目架构设计说明:
1. 认证层 (DouyinAuthenticator): 多级回退机制，确保在各种运行环境下都能稳定获取 Session。
2. 引擎层 (DouyinEmojiEngine): 负责资源扫描、分片逻辑以及核心的图片格式转换。
3. 签名层 (ABogus): 负责计算所有 Web API 必需的 a_bogus 指纹，维持协议活性。
"""

# ==========================================
# --- 全局配置 ---
# ==========================================
API = {
    # ttwid 引导（无需登录即可获取）
    "TTWID":          "https://ttwid.bytedance.com/ttwid/union/register/",
    # 表情资源接口
    "STICKER":        "https://www.douyin.com/aweme/v1/web/im/resource/list/aggregation",
    # Session 持久化
    "SESSION_FILE":   "session.json",
}

DOWNLOAD = {
    "WORKERS":  15,
    "ZIP_NAME": "Douyin_Emoji_Pack.zip",
    "RETRIES":  3,
}

# 固定 User-Agent（须与 abogus.py 中 ua_code 对应的 UA 匹配）
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/90.0.4430.212 Safari/537.36"
)

HEADERS = {
    "User-Agent":      UA,
    "Referer":         "https://www.douyin.com/",
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# ==========================================
# --- 工具函数 ---
# ==========================================

def gen_random_str(length: int = 126) -> str:
    """生成随机字符串（msToken 伪造用）"""
    chars = string.ascii_letters + string.digits
    return "".join(random.choices(chars, k=length))


def gen_verify_fp() -> str:
    """
    生成 verifyFp / s_v_web_id
    算法移植自 Evil0ctal/Douyin_TikTok_Download_API VerifyFpManager
    """
    base_str = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    t = len(base_str)
    ms = int(round(time.time() * 1000))
    base36 = ""
    n = ms
    while n > 0:
        rem = n % 36
        base36 = (str(rem) if rem < 10 else chr(ord("a") + rem - 10)) + base36
        n = int(n / 36)

    o = [""] * 36
    o[8] = o[13] = o[18] = o[23] = "_"
    o[14] = "4"

    for i in range(36):
        if not o[i]:
            x = int(random.random() * t)
            if i == 19:
                x = 3 & x | 8
            o[i] = base_str[x]

    return "verify_" + base36 + "_" + "".join(o)


def build_endpoint_with_abogus(base_url: str, params: dict, method: str = "GET") -> str:
    """
    签名链路闭环：封装请求参数并计算 a_bogus。
    params: 原始查询参数字典
    method: HTTP 方法，ABogus 算法会根据方法名对参数进行不同的哈希处理
    """
    params_with_ms = dict(params)
    # msToken 在当前 Web 端策略中可留空，主要校验逻辑集中在 a_bogus 中
    params_with_ms["msToken"] = ""

    ab = ABogus()
    # 计算原始指纹
    a_bogus_raw = ab.get_value(params_with_ms, method)
    # 必须进行 URL 编码，否则特殊字符会导致服务端 403
    a_bogus = quote(a_bogus_raw, safe="")

    return f"{base_url}?{urlencode(params_with_ms)}&a_bogus={a_bogus}"


def get_ttwid(session) -> str:
    """
    从 bytedance ttwid 服务获取 ttwid cookie（无需登录）。
    这是抖音 API 请求的基础 token。
    """
    payload = (
        '{"region":"cn","aid":1128,"needFid":false,"service":"www.douyin.com",'
        '"migrate_info":{"ticket":"","source":"node"},'
        '"cbUrlProtocol":"https","union":true}'
    )
    try:
        resp = session.post(
            API["TTWID"],
            data=payload,
            headers={**HEADERS, "Content-Type": "application/json"},
            timeout=10,
        )
        ttwid = resp.cookies.get("ttwid", "")
        if ttwid:
            print(f"  ✅ ttwid 获取成功: {ttwid[:20]}...")
            return ttwid
    except Exception as e:
        print(f"  ⚠️  ttwid 获取失败（将继续）: {e}")
    return ""


# ==========================================
# --- 认证模块 ---
# ==========================================

class DouyinAuthenticator:
    """
    认证中台：负责 Session 生命周期管理。
    采用四层回退策略：环境变量 -> 本地缓存文件 -> 原始 Cookie 解析 -> 手动输入。
    """
    def __init__(self):
        # 使用 impersonate="chrome124" 模拟真实的浏览器 TLS 指纹，这是过 WAF 的第一道关卡
        self.session = requests.Session(impersonate="chrome124")
        self.session.headers.update(HEADERS)
        self._bootstrap_cookies()

    def _bootstrap_cookies(self):
        """预置必要的基础 Cookie（无需登录即可访问大部分接口）"""
        # 1. 获取 ttwid
        ttwid = get_ttwid(self.session)
        if ttwid:
            self.session.cookies.set("ttwid", ttwid, domain=".douyin.com")

        # 2. 生成 verifyFp / s_v_web_id
        verify_fp = gen_verify_fp()
        s_v_web_id = gen_verify_fp()
        self.session.cookies.set("verifyFp", verify_fp, domain=".douyin.com")
        self.session.cookies.set("s_v_web_id", s_v_web_id, domain=".douyin.com")

        # 3. 伪造 msToken（长度 128，抖音只校验长度格式）
        ms_token = gen_random_str(126) + "=="
        self.session.cookies.set("msToken", ms_token, domain=".douyin.com")

        print(f"  🍪 基础 Cookie 已预置 (verifyFp, s_v_web_id, msToken, ttwid)")

    def get_session(self):
        """
        认证优先级:
          1. 环境变量 DY_COOKIE  (CI/GitHub Actions 专用)
          2. 本地 session.json   (持久化 Session)
          3. 手动输入 Cookie      (交互模式)
        """
        # 1. CI 环境变量
        env_cookie = os.getenv("DY_COOKIE")
        if env_cookie:
            print("🔗 [CI] 检测到 DY_COOKIE 环境变量，注入中...")
            self._inject_raw_cookie(env_cookie)
            if self._check_valid():
                print("✅ CI Session 有效。")
                return self.session
            print("⚠️  DY_COOKIE 已失效，尝试其他方式...")

        # 2. 检查 cookie.txt (强鲁棒性增强: 解决终端粘贴截断问题)
        if os.path.exists("cookie.txt"):
            try:
                with open("cookie.txt", "r") as f:
                    raw_cookie = f.read().strip()
                if raw_cookie:
                    print("📄 检测到 cookie.txt，正在解析注入...")
                    self._inject_raw_cookie(raw_cookie)
                    if self._check_valid():
                        print("✅ cookie.txt 有效，同步至 session.json")
                        self._save_session()
                        return self.session
                    print("⚠️  cookie.txt 中的 Cookie 已过期")
            except Exception as e:
                print(f"⚠️  cookie.txt 读取失败: {e}")

        # 3. 本地缓存
        if os.path.exists(API["SESSION_FILE"]):
            try:
                with open(API["SESSION_FILE"]) as f:
                    self.session.cookies.update(json.load(f))
                if self._check_valid():
                    print("✅ 已恢复本地 Session，无需重新登录。")
                    return self.session
                print("⚠️  本地 Session 失效，尝试重新登录...")
            except Exception:
                pass

        # 4. 手动输入 Cookie
        return self._cookie_login()

    def _cookie_login(self):
        """手动输入 Cookie 登录"""
        print("\n🔑 请在浏览器登录抖音后，将 Cookie 粘贴到下方")
        raw_cookie = input("   Cookie> ").strip()
        if raw_cookie:
            self._inject_raw_cookie(raw_cookie)
            if self._check_valid():
                print("✅ Cookie 有效，正在保存 Session...")
                self._save_session()
                return self.session
            else:
                print("❌ 提供的 Cookie 无效。")
        raise RuntimeError("未提供有效 Cookie，无法继续。")

    def _check_valid(self) -> bool:
        """检查当前 Session 是否有效（调用自身信息接口）"""
        try:
            params = {
                "device_platform": "webapp",
                "aid": "6383",
            }
            endpoint = build_endpoint_with_abogus(
                "https://www.douyin.com/aweme/v1/web/user/profile/self/", params
            )
            r = self.session.get(endpoint, timeout=8)
            return r.json().get("status_code") == 0
        except Exception:
            return False

    def _inject_raw_cookie(self, raw: str):
        """解析 key=value; key2=value2 格式的 Cookie 字符串"""
        for item in raw.split(";"):
            item = item.strip()
            if "=" in item:
                k, v = item.split("=", 1)
                self.session.cookies.set(k.strip(), v.strip())

    def _save_session(self):
        """持久化 Cookies 到本地"""
        with open(API["SESSION_FILE"], "w") as f:
            json.dump(dict(self.session.cookies), f, indent=2)
        print(f"💾 Session 已保存至 {API['SESSION_FILE']}")


# ==========================================
# --- 下载引擎 ---
# ==========================================

class DouyinEmojiEngine:
    def __init__(self, session):
        self.session = session
        self.emojis: list = []

    def fetch_list(self):
        """拉取表情资源列表（分页 + ABogus 签名）"""
        print("\n🔍 正在拉取表情资源列表...")
        cursor   = 0
        has_more = True

        while has_more:
            params = {
                "device_platform":  "webapp",
                "aid":              "1128",
                "channel":          "channel_pc_web",
                "scenes":           "CUSTOM_STICKER_PAGE",
                "custom_cursor":    str(cursor),
                "custom_page_size": "100",
                "version_code":     "170400",
                "version_name":     "17.4.0",
                "cookie_enabled":   "true",
                "screen_width":     "1536",
                "screen_height":    "864",
                "browser_language": "zh-CN",
                "browser_platform": "Win32",
                "browser_name":     "Chrome",
                "browser_version":  "90.0.4430.212",
                "browser_online":   "true",
                "engine_name":      "Blink",
                "engine_version":   "90.0.4430.212",
                "os_name":          "Windows",
                "os_version":       "10",
                "cpu_core_num":     "12",
                "device_memory":    "8",
                "platform":         "PC",
            }

            try:
                endpoint = build_endpoint_with_abogus(API["STICKER"], params)
                resp = self.session.get(endpoint, timeout=15)

                if resp.status_code == 403:
                    print("🚫 403: 签名验证失败或需要登录 Cookie。")
                    print("   → 请提供有效的登录 Cookie（运行时设置 DY_COOKIE 环境变量）")
                    break

                if resp.status_code != 200:
                    print(f"⚠️  接口返回 {resp.status_code}，跳过此页。")
                    break

                data = resp.json()
                page = data.get("custom_sticker_page_list", {})

                for res in page.get("resources", []):
                    for s in res.get("stickers", []):
                        url_info = s.get("animate_url") or s.get("static_url")
                        if url_info and url_info.get("url_list"):
                            # 优先选择包含 origin 的链接，否则选择第一个
                            best_url = url_info["url_list"][0]
                            for u in url_info["url_list"]:
                                if "origin" in u.lower():
                                    best_url = u
                                    break

                            self.emojis.append({
                                "id":  s.get("id_str", "unknown"),
                                "url": best_url,
                            })

                has_more = page.get("has_more", False)
                cursor   = page.get("next_cursor", cursor)

                if not has_more or cursor == 0:
                    break

                print(f"  └─ 已发现 {len(self.emojis)} 个资源...", flush=True)

            except Exception as e:
                print(f"❌ 请求异常: {e}")
                break

        print(f"✅ 资源扫描完毕，共 {len(self.emojis)} 个表情。")

    def run(self):
        """并发下载 → 图片转换 → ZIP 打包"""
        if not self.emojis:
            print("⚠️  无可下载资源，可能原因：")
            print("   1. 未登录或 Cookie 已过期")
            print("   2. 接口参数需要更新")
            print("   3. 账号下无自定义表情")
            return

        print(f"\n⚡ 启动 {DOWNLOAD['WORKERS']} 线程并发下载...")
        results = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=DOWNLOAD["WORKERS"]) as ex:
            futures = {ex.submit(self._download_one, item): item for item in self.emojis}
            for future in concurrent.futures.as_completed(futures):
                res = future.result()
                if res:
                    results.append(res)
                    print(f"\r  📦 进度: {len(results)}/{len(self.emojis)}", end="", flush=True)

        if not results:
            print("\n⚠️  所有资源下载失败。")
            return

        print(f"\n\n🗜️  正在打包 → {DOWNLOAD['ZIP_NAME']}...")
        with zipfile.ZipFile(DOWNLOAD["ZIP_NAME"], "w", zipfile.ZIP_DEFLATED) as zf:
            for r in results:
                zf.writestr(r["name"], r["data"])

        size_mb = os.path.getsize(DOWNLOAD["ZIP_NAME"]) / 1024 / 1024
        print(f"🎉 任务完成！共打包 {len(results)} 个文件 ({size_mb:.2f} MB)")
        print(f"   路径: {os.path.abspath(DOWNLOAD['ZIP_NAME'])}")

    def _download_one(self, item):
        """
        原子化下载：包含重试机制与 TLS 模拟。
        """
        for _ in range(DOWNLOAD["RETRIES"]):
            try:
                # 必须维持 chrome124 指纹，否则 CDN 可能会针对频繁请求封禁
                resp = requests.get(item["url"], timeout=12, impersonate="chrome124")
                resp.raise_for_status()
                # 实时调用转换引擎
                data, ext = self._convert(resp.content)
                return {"name": f"sticker_{item['id']}.{ext}", "data": data}
            except Exception:
                time.sleep(0.5)
        return None

    @staticmethod
    def _convert(content: bytes):
        """
        高保真引擎：WebP 动图 → 优质 GIF (解决黑边与杂色)
        优化策略：
        1. 使用自适应采样 (ADAPTIVE) 重新量化色彩，避免断层。
        2. 处理 Alpha 通道掩模，确保边缘透明度平滑。
        3. 设置 disposal=2 恢复背景，防止动态重叠伪影。
        """
        try:
            img = Image.open(io.BytesIO(content))
            # 只有动图需要转换以兼容社交平台，静态 WebP 自身画质即最优
            if getattr(img, "is_animated", False):
                frames = []
                durations = []
                
                for frame in ImageSequence.Iterator(img):
                    # 转换为 RGBA 以提取精确透明度
                    rgba_frame = frame.convert("RGBA")
                    
                    # 建立透明度掩模：将半透明部分映射到透明索引位
                    alpha = rgba_frame.getchannel('A')
                    # 阈值处理：a <= 128 视为透明
                    mask = Image.eval(alpha, lambda a: 255 if a <= 128 else 0)
                    
                    # 核心转换：RGB 模式量化为 P 模式，为透明预留一个位置
                    p_frame = rgba_frame.convert('RGB').convert('P', palette=Image.ADAPTIVE, colors=255)
                    # 将透明索引 (255) 强制覆盖到掩模区域
                    p_frame.paste(255, mask)
                    
                    frames.append(p_frame)
                    durations.append(frame.info.get("duration", 100))
                
                out = io.BytesIO()
                frames[0].save(
                    out,
                    format="GIF",
                    save_all=True,
                    append_images=frames[1:],
                    duration=durations,
                    loop=0,
                    transparency=255, # 指定索引 255 为透明
                    disposal=2,      # 每一帧后恢复背景，防止残留
                    optimize=True     # 启用 LZW 压缩优化
                )
                return out.getvalue(), "gif"
        except Exception as e:
            print(f"  ⚠️  质量增强转换失败 (保留原图): {e}")
            
        # 静态图或转换失败时返回原始字节，保持 WebP 原生最高画质
        return content, "webp"


# ==========================================
# --- 入口 ---
# ==========================================

def main():
    print("╔══════════════════════════════════════════╗")
    print("║  Douyin Emoji Professional (P8-ABogus)   ║")
    print("║  纯协议 · curl_cffi · 纯Python ABogus签名  ║")
    print("╚══════════════════════════════════════════╝\n")

    try:
        print("🔧 初始化认证模块...")
        auth    = DouyinAuthenticator()
        session = auth.get_session()

        engine = DouyinEmojiEngine(session)
        engine.fetch_list()
        engine.run()

    except KeyboardInterrupt:
        print("\n\n👋 已终止。")
    except Exception as e:
        import traceback
        print(f"\n❌ 异常: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    main()
