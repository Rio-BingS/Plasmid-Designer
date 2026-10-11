"""容器 nginx X-Forwarded-Proto 回归锁（审查 03 M3）

此前 deploy/docker/nginx.conf 写死 proxy_set_header X-Forwarded-Proto $scheme，
容器内恒为 http，覆盖了宿主机 TLS 前置传来的 https，AUTH_COOKIE_SECURE=auto
在 HTTPS 部署下永远不给会话 Cookie 置 Secure。沙箱无 nginx：这里解析配置里的
geo/map，用 Python 按 nginx 语义（geo 最长前缀、map 正则按序首个命中、~* 忽略
大小写）求值，并核对每个 /api 反代都用 $forwarded_proto。
"""

import ipaddress
import re
from pathlib import Path

import pytest

CONF = Path(__file__).resolve().parents[1] / "deploy" / "docker" / "nginx.conf"
TXT = CONF.read_text(encoding="utf-8")


def _block(header):
    m = re.search(re.escape(header) + r"\s*\{\n(.*?)\n\}", TXT, re.S)
    assert m, f"nginx.conf 缺少 {header}"
    return [ln.split("#")[0].strip().rstrip(";") for ln in m.group(1).splitlines()]


def _geo(name):
    entries = {}
    for ln in _block(f"geo ${name}"):
        if ln:
            k, v = ln.split(None, 1)
            entries[k] = v.strip()
    return entries


def _geo_lookup(entries, ip):
    addr = ipaddress.ip_address(ip)
    best, best_len = entries["default"], -1
    for k, v in entries.items():
        if k == "default":
            continue
        net = ipaddress.ip_network(k, strict=False)
        if addr.version == net.version and addr in net and net.prefixlen > best_len:
            best, best_len = v, net.prefixlen
    return best


def _map_entries():
    m = re.search(r'map\s+"\$proto_from_trusted:\$http_x_forwarded_proto"\s+\$forwarded_proto', TXT)
    assert m, "nginx.conf 缺少 $forwarded_proto 的 map"
    out = []
    for ln in _block(m.group(0)):
        if not ln:
            continue
        mm = re.match(r'("([^"]*)"|\S+)\s+(\S+)$', ln)
        assert mm, ln
        out.append((mm.group(2) if mm.group(2) is not None else mm.group(1), mm.group(3)))
    return out


def forwarded_proto(peer, header, scheme="http"):
    flag = _geo_lookup(_geo("proto_from_trusted"), peer)
    key = f"{flag}:{header}"
    default = None
    for k, v in _map_entries():
        if k == "default":
            default = v
            continue
        assert k.startswith("~*"), k
        if re.search(k[2:], key, re.I):
            return v
    return {"$scheme": scheme}.get(default, default)


@pytest.mark.parametrize(
    "peer,header,expected",
    [
        ("172.28.0.1", "https", "https"),       # 宿主机 nginx 经 docker 网桥
        ("172.28.0.1", "HTTPS", "https"),
        ("172.28.0.1", "https, http", "https"),  # 多级代理取最左
        ("127.0.0.1", "https", "https"),
        ("172.28.0.1", "http", "http"),
        ("172.28.0.1", "", "http"),               # 前置未发头 → 本连接协议
        ("172.28.0.1", "httpsx", "http"),         # 非法值不透传
        ("172.28.0.1", "ftp", "http"),
        ("172.18.0.1", "https", "http"),          # 同机其它 docker 网段 → 不采信
        ("203.0.113.9", "https", "http"),         # 公网直连伪造头 → 不采信
        ("10.0.0.5", "https", "http"),
    ],
)
def test_forwarded_proto_resolution(peer, header, expected):
    assert forwarded_proto(peer, header) == expected


def test_proto_trust_matches_realip_trust():
    # 两组网段必须一致：只透传可信前置的头
    realip = {k for k in _geo("trusted_realip") if k != "default"}
    proto = {k for k in _geo("proto_from_trusted") if k != "default"}
    assert realip == proto
    assert _geo("proto_from_trusted")["default"] == "0"


def test_api_proxies_use_forwarded_proto():
    assert "X-Forwarded-Proto $scheme" not in TXT
    sets = re.findall(r"proxy_set_header\s+X-Forwarded-Proto\s+(\S+);", TXT)
    assert sets and all(v == "$forwarded_proto" for v in sets)
    # 每个设置 X-Forwarded-For 的 /api 反代都要带上协议
    assert len(sets) == len(re.findall(r"proxy_set_header\s+X-Forwarded-For\b", TXT))


def test_docker_readme_host_proxy_sends_proto():
    readme = (CONF.parent / "README.md").read_text(encoding="utf-8")
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in readme
    assert "Strict-Transport-Security" in readme
    assert "return 301 https://" in readme
    assert "AUTH_COOKIE_SECURE" in (CONF.parent / ".env.example").read_text(encoding="utf-8")
