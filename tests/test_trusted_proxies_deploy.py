"""部署模板可信代理回归锁（审查 02 M2）

docker 部署曾默认信任 172.16.0.0/12：同网段任意容器/主机（或经 docker-proxy
进入、来源显示为网桥网关的外部连接）都能伪造 X-Real-IP / XFF 换限流桶。
现在 compose 网络固定网段，后端只信前端 nginx 容器的固定地址，前端 nginx
只对入口脚本生成的可信前置清单透传 X-Real-IP；裸机默认仍只信本机。
"""

import ipaddress
import re
from pathlib import Path

from starlette.requests import Request

from app import rate_limit
from app.config import Settings

ROOT = Path(__file__).resolve().parents[1]
DOCKER = ROOT / "deploy" / "docker"
BARE = ROOT / "deploy" / "bare"

LOOPBACK = {"127.0.0.1", "::1"}


def _env(path: Path) -> dict:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z0-9_]+)=(.*)$", line.strip())
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def _compose_default(var: str) -> str:
    txt = (DOCKER / "docker-compose.yml").read_text(encoding="utf-8")
    m = re.search(r"\$\{" + var + r":-([^}]*)\}", txt)
    assert m, f"compose 缺少 ${{{var}:-...}}"
    return m.group(1).strip()


def _entries(raw: str) -> list:
    return [x.strip() for x in raw.split(",") if x.strip()]


def _assert_single_hosts(entries):
    for item in entries:
        net = ipaddress.ip_network(item, strict=False)
        assert net.num_addresses == 1, f"可信代理不得是网段: {item}"


def test_docker_trusted_proxies_only_frontend_container():
    env = _env(DOCKER / ".env.example")
    for raw in (env["TRUSTED_PROXIES"], _compose_default("TRUSTED_PROXIES")):
        entries = _entries(raw)
        _assert_single_hosts(entries)
        assert "172.16.0.0/12" not in raw
        assert set(entries) - LOOPBACK == {env["FRONTEND_IPV4"]}


def test_docker_network_pinned_and_consistent():
    env = _env(DOCKER / ".env.example")
    assert _compose_default("PLASMID_SUBNET") == env["PLASMID_SUBNET"]
    assert _compose_default("PLASMID_GATEWAY") == env["PLASMID_GATEWAY"]
    assert _compose_default("FRONTEND_IPV4") == env["FRONTEND_IPV4"]
    subnet = ipaddress.ip_network(env["PLASMID_SUBNET"])
    gw = ipaddress.ip_address(env["PLASMID_GATEWAY"])
    fe = ipaddress.ip_address(env["FRONTEND_IPV4"])
    assert gw in subnet and fe in subnet and gw != fe
    txt = (DOCKER / "docker-compose.yml").read_text(encoding="utf-8")
    assert "ipv4_address: ${FRONTEND_IPV4:-" in txt


def test_frontend_nginx_realip_only_from_host_proxy_geo():
    # 前端 nginx 内置只信容器回环，其余可信前置由入口脚本生成后 include
    # （默认只绑回环时为网关，见 test_deploy_host_proxy_trust）
    conf = (DOCKER / "nginx.conf").read_text(encoding="utf-8")
    m = re.search(r"geo \$from_host_proxy \{(.*?)\}", conf, re.S)
    assert m
    trusted, includes = [], []
    for line in m.group(1).splitlines():
        parts = line.split("#")[0].split()
        if len(parts) != 2 or parts[0] == "default":
            continue
        if parts[0] == "include":
            includes.append(parts[1].rstrip(";"))
            continue
        assert parts[1].rstrip(";") == "1"
        trusted.append(parts[0])
    _assert_single_hosts(trusted)
    assert set(trusted) == LOOPBACK
    assert includes == ["/etc/nginx/host-proxy.geo"]
    assert re.search(r"map \$from_host_proxy \$trusted_realip \{\s*1\s+\$http_x_real_ip;\s*"
                     r"default\s+\$remote_addr;", conf)


def test_bare_and_code_default_loopback_only():
    assert set(_entries(_env(BARE / ".env.example")["TRUSTED_PROXIES"])) == LOOPBACK
    assert set(_entries(Settings.model_fields["TRUSTED_PROXIES"].default)) == LOOPBACK


def _req(peer, headers):
    scope = {"type": "http", "method": "GET", "path": "/api/x",
             "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
             "client": (peer, 5555), "query_string": b""}
    return Request(scope)


def test_docker_default_rejects_spoof_from_other_bridge_peer(monkeypatch):
    env = _env(DOCKER / ".env.example")
    monkeypatch.setattr(rate_limit.settings, "TRUSTED_PROXIES", env["TRUSTED_PROXIES"])
    # 审查探针 P3：同网段其它对端 + 伪造 XFF，修复前解析为 6.6.6.6
    spoof = {"X-Forwarded-For": "6.6.6.6, 172.18.0.1", "X-Real-IP": "6.6.6.7"}
    assert rate_limit.get_client_ip(_req("172.18.0.3", spoof)) == "172.18.0.3"
    assert rate_limit.get_client_ip(_req("172.28.0.5", spoof)) == "172.28.0.5"
    # 前端 nginx 容器仍被信任
    fe = env["FRONTEND_IPV4"]
    assert rate_limit.get_client_ip(_req(fe, {"X-Real-IP": "203.0.113.7"})) == "203.0.113.7"
