# -*- coding: utf-8 -*-
"""从本机 Edge/Chrome 的 cookie 库读出指定域的 cookie，并可写入 config.json。

背景：m.weibo.cn 的 getIndex 接口会做风控（HTTP 432），需要浏览器里有效的登录 cookie。
Edge 运行时会独占锁定 Cookies 文件（ERROR_SHARING_VIOLATION）→ **必须先退出浏览器**。

用法：
  python tools/read_edge_cookies.py                 # 读 weibo 域，打印名称统计
  python tools/read_edge_cookies.py --write-config  # 同时写入 config.json: weibo_cookie
  python tools/read_edge_cookies.py --browser chrome

解密链路（Chromium v80+）：
  Local State 的 os_crypt.encrypted_key --DPAPI--> AES-256 key
  cookies.encrypted_value: v10/v11 = b'v10' + nonce(12) + ct + tag(16)  --AES-GCM--> 明文
  若出现 v20 前缀说明启用了 App-Bound Encryption，本脚本无法解密（需浏览器侧导出）。
"""
import argparse
import base64
import ctypes
import json
import os
import shutil
import sqlite3
import sys
from ctypes import wintypes
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

BROWSER_ROOTS = {
    "edge": Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "User Data",
    "chrome": Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data",
}


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def dpapi_decrypt(data: bytes) -> bytes:
    """调用 Windows DPAPI 解密（当前用户上下文）。"""
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out))
    if not ok:
        raise OSError(f"CryptUnprotectData 失败，err={ctypes.GetLastError()}")
    out = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    return out


def load_aes_key(user_data: Path) -> bytes:
    ls = json.loads((user_data / "Local State").read_text(encoding="utf-8"))
    enc = base64.b64decode(ls["os_crypt"]["encrypted_key"])
    if enc[:5] != b"DPAPI":
        raise ValueError("encrypted_key 前缀非 DPAPI，无法处理")
    return dpapi_decrypt(enc[5:])


def decrypt_value(key: bytes, blob: bytes) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not blob:
        return ""
    prefix = blob[:3]
    if prefix in (b"v10", b"v11"):
        nonce, ct_tag = blob[3:15], blob[15:]
        return AESGCM(key).decrypt(nonce, ct_tag, None).decode("utf-8", "replace")
    if prefix == b"v20":
        raise ValueError("该 cookie 使用 App-Bound Encryption(v20)，脚本无法解密")
    # 老格式（v80 之前）：整个密文走 DPAPI
    return dpapi_decrypt(blob).decode("utf-8", "replace")


def snapshot_cookie_db(user_data: Path, profile: str) -> Path:
    """退出浏览器后直接复制；若仍在运行会抛 PermissionError。"""
    src = user_data / profile / "Network" / "Cookies"
    if not src.exists():
        src = user_data / profile / "Cookies"
    if not src.exists():
        raise FileNotFoundError(f"未找到 cookie 库: {src}")
    tmp = Path(os.environ["TEMP"]) / "wbcookie" / "Cookies"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, tmp)          # 浏览器未退出 → PermissionError
    return tmp


def read_cookies(browser: str, profile: str, domain_filter: str):
    user_data = BROWSER_ROOTS[browser]
    if not user_data.exists():
        raise FileNotFoundError(f"未找到 {browser} 用户数据目录: {user_data}")
    db_path = snapshot_cookie_db(user_data, profile)
    key = load_aes_key(user_data)

    con = sqlite3.connect(str(db_path))
    cur = con.cursor()
    cur.execute(
        "SELECT host_key, name, encrypted_value, path, is_secure, is_httponly, expires_utc "
        "FROM cookies WHERE host_key LIKE ? ORDER BY host_key, name",
        (f"%{domain_filter}%",))
    rows = cur.fetchall()
    con.close()

    out = []
    for host, name, enc, path, secure, httponly, exp in rows:
        try:
            val = decrypt_value(key, enc)
        except Exception as e:
            print(f"  ⚠️ 解密失败 {host} {name}: {e}")
            continue
        if val:
            out.append({"host": host, "name": name, "value": val,
                        "path": path or "/", "secure": bool(secure), "httponly": bool(httponly)})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--browser", default="edge", choices=["edge", "chrome"])
    ap.add_argument("--profile", default="Default")
    ap.add_argument("--domain", default="weibo")
    ap.add_argument("--write-config", action="store_true",
                    help="把拼好的 cookie 串写入 config.json 的 weibo_cookie")
    args = ap.parse_args()

    try:
        cookies = read_cookies(args.browser, args.profile, args.domain)
    except PermissionError:
        print(f"❌ cookie 库被占用：请先完全退出 {args.browser}（含后台进程）后重试。")
        sys.exit(2)
    except Exception as e:
        print(f"❌ {type(e).__name__}: {e}")
        sys.exit(1)

    if not cookies:
        print(f"⚠️ 未找到含 '{args.domain}' 的 cookie")
        sys.exit(3)

    # 按 host 分组展示（不打印值）
    by_host = {}
    for c in cookies:
        by_host.setdefault(c["host"], []).append(c["name"])
    for h, names in sorted(by_host.items()):
        print(f"  {h:24s} {len(names):2d} 项: {', '.join(names)}")

    # 组装 cookie 串：优先 m.weibo.cn（含 SUB/SUBP 登录态），再合并 weibo.com / weibo.cn
    pref = [".weibo.cn", "m.weibo.cn", ".weibo.com", "weibo.com", "weibo.cn", ".sina.com.cn", ".sina.cn"]
    ordered, seen = [], set()
    for host in pref:
        for c in cookies:
            if c["host"] == host and c["name"] not in seen:
                ordered.append(c); seen.add(c["name"])
    for c in cookies:
        if c["name"] not in seen:
            ordered.append(c); seen.add(c["name"])

    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in ordered)
    print(f"\n共 {len(ordered)} 项，cookie 串长度 {len(cookie_str)}")
    print(f"  含登录关键项 SUB: {'SUB' in seen} | SUBP: {'SUBP' in seen}")

    if args.write_config:
        cfg_path = BASE / "config.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        if cfg.get("weibo_cookie"):
            cfg["weibo_cookie_prev"] = cfg["weibo_cookie"]
        cfg["weibo_cookie"] = cookie_str
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"✅ 已写入 config.json 的 weibo_cookie（原值备份到 weibo_cookie_prev）")
    else:
        outp = BASE / "data" / "weibo_cookie_browser.txt"
        outp.parent.mkdir(exist_ok=True)
        outp.write_text(cookie_str, encoding="utf-8")
        print(f"✅ 已写入 {outp}（未改动 config.json）")


if __name__ == "__main__":
    main()
