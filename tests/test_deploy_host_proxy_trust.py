"""前端 nginx 可信宿主机前置回归锁（审查 02 M2 / 03 L7）

v2.9.0 把前端 nginx 的可信来源收窄到 compose 网关 172.28.0.1。但经 docker-proxy
（userland proxy、IPv6 入站、rootless 等）转发的连接，来源同样显示为网关：
FRONTEND_BIND=0.0.0.0 对外发布时，直连发布端口的客户端带 X-Real-IP 就能换
限流桶。现在可信清单由入口脚本 nginx-host-proxy.sh 按 FRONTEND_BIND /
PLASMID_GATEWAY / HOST_PROXY_ADDRS 生成后 include 进 geo：只绑回环时信网关，
对外发布时谁都不信；客户端自带的 X-Forwarded-For 也只在经可信前置时保留。
沙箱无 nginx/docker：脚本用 sh 黑盒跑，nginx 的 geo/map 按语义用 Python 求值，
再把结果头交给后端 get_client_ip 端到端核对。
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from starlette.requests import Request

from app import rate_limit
from tests.test_deploy_forwarded_proto import _geo, _geo_lookup, forwarded_proto

DOCKER = Path(__file__).resolve().parents[1] / "deploy" / "docker"
SCRIPT = DOCKER / "nginx-host-proxy.sh"
CONF = (DOCKER / "nginx.conf").read_text(encoding="utf-8")
SH = shutil.which("sh")

needs_sh = pytest.mark.skipif(SH is None or sys.platform.startswith("win"), reason="需要 sh")

GATEWAY = "172.28.0.1"
FRONTEND = "172.28.0.10"


def _gen(tmp_path, **env):
    out = tmp_path / "host-proxy.geo"
    if out.exists():
        out.unlink()
    full = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOST_PROXY_GEO_FILE": str(out)}
    full.update(env)
    proc = subprocess.run([SH, str(SCRIPT)], env=full, capture_output=True, text=True)
    text = out.read_text(encoding="utf-8") if out.exists() else None
    return proc, text


def _map(var_in, var_out):
    m = re.search(rf"map \${var_in} \${var_out} \{{(.*?)\}}", CONF, re.S)
    assert m, f"nginx.conf 缺少 map ${var_in} ${var_out}"
    out = {}
    for ln in m.group(1).splitlines():
        ln = ln.split("#")[0].strip().rstrip(";")
        if ln:
            k, v = ln.split(None, 1)
            out[k] = v.strip()
    return out


def _frontend_headers(peer, client_headers, include_text):
    """按 nginx.conf 求出前端 nginx 发给后端的 X-Real-IP / X-Forwarded-For"""
    flag = _geo_lookup(_geo("from_host_proxy", include_text), peer)
    nginx_vars = {
        "$http_x_real_ip": client_headers.get("X-Real-IP", ""),
        "$remote_addr": peer,
        "$proxy_add_x_forwarded_for": ", ".join(
            [x for x in (client_headers.get("X-Forwarded-For", ""), peer) if x]),
    }

    def pick(var):
        table = _map("from_host_proxy", var)
        return nginx_vars[table.get(flag, table["default"])]

    return {"X-Real-IP": pick("trusted_realip"), "X-Forwarded-For": pick("forwarded_for")}


def _backend_ip(headers):
    scope = {"type": "http", "method": "GET", "path": "/api/x",
             "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items() if v],
             "client": (FRONTEND, 5555), "query_string": b""}
    return rate_limit.get_client_ip(Request(scope))


SPOOF = {"X-Real-IP": "6.6.6.6", "X-Forwarded-For": "6.6.6.7"}


@pytest.fixture
def docker_backend(monkeypatch):
    # 后端按 docker 模板配置：只信前端容器固定地址
    env = (DOCKER / ".env.example").read_text(encoding="utf-8")
    trusted = re.search(r"^TRUSTED_PROXIES=(.*)$", env, re.M).group(1).strip()
    entries = {x.strip() for x in trusted.split(",")}
    assert FRONTEND in entries and GATEWAY not in entries
    monkeypatch.setattr(rate_limit.settings, "TRUSTED_PROXIES", trusted)


# ---- 入口脚本 ----

@needs_sh
def test_loopback_bind_trusts_gateway(tmp_path):
    proc, text = _gen(tmp_path, FRONTEND_BIND="127.0.0.1", PLASMID_GATEWAY=GATEWAY)
    assert proc.returncode == 0, proc.stderr
    assert text == f"{GATEWAY} 1;\n"


@needs_sh
def test_defaults_match_compose_defaults(tmp_path):
    # 不传任何变量：等同 compose 默认（只绑回环 + 默认网关）
    proc, text = _gen(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert text == f"{GATEWAY} 1;\n"


@needs_sh
@pytest.mark.parametrize("bind", ["0.0.0.0", "203.0.113.5", "::", ""])
def test_published_port_trusts_nobody(tmp_path, bind):
    # 审查 02 M2：对外发布时网关来源可能是 docker-proxy 转发的外部直连
    env = {"PLASMID_GATEWAY": GATEWAY}
    if bind:
        env["FRONTEND_BIND"] = bind
    proc, text = _gen(tmp_path, **env)
    assert proc.returncode == 0, proc.stderr
    if bind:
        assert text == ""
        assert "FRONTEND_BIND" in proc.stderr
    else:  # 空值按默认回环处理，与 compose 的 ${FRONTEND_BIND:-127.0.0.1} 一致
        assert text == f"{GATEWAY} 1;\n"


@needs_sh
def test_custom_gateway_follows_env(tmp_path):
    proc, text = _gen(tmp_path, FRONTEND_BIND="127.0.0.1", PLASMID_GATEWAY="10.99.0.1")
    assert proc.returncode == 0 and text == "10.99.0.1 1;\n"


@needs_sh
def test_explicit_list_and_none(tmp_path):
    proc, text = _gen(tmp_path, FRONTEND_BIND="0.0.0.0",
                      HOST_PROXY_ADDRS="172.28.0.1, 2001:db8::1/128,127.0.0.1,::1")
    assert proc.returncode == 0, proc.stderr
    assert text == "172.28.0.1 1;\n2001:db8::1 1;\n"
    proc, text = _gen(tmp_path, FRONTEND_BIND="127.0.0.1", HOST_PROXY_ADDRS="none")
    assert proc.returncode == 0 and text == ""


@needs_sh
@pytest.mark.parametrize("bad", ["172.16.0.0/12", "0.0.0.0/0", "172.28.0.0/24",
                                 "2001:db8::/64", "evil.example.com", "1.2.3.4;"])
def test_rejects_networks_and_garbage(tmp_path, bad):
    # 失败即拒绝启动（官方入口脚本 set -e），且不留下半截清单
    proc, text = _gen(tmp_path, HOST_PROXY_ADDRS=bad)
    assert proc.returncode != 0
    assert text is None
    assert not list(tmp_path.glob("host-proxy.geo.tmp*"))


# ---- nginx 语义 + 后端端到端 ----

def test_published_port_spoof_via_gateway_is_ignored(docker_backend):
    # 修复前：geo 写死信任网关，下面解析为 6.6.6.6
    hdrs = _frontend_headers(GATEWAY, SPOOF, include_text="")
    assert hdrs == {"X-Real-IP": GATEWAY, "X-Forwarded-For": GATEWAY}
    assert _backend_ip(hdrs) == GATEWAY


def test_host_proxy_real_ip_still_passes_on_loopback_bind(docker_backend):
    hdrs = _frontend_headers(GATEWAY, {"X-Real-IP": "203.0.113.7",
                                       "X-Forwarded-For": "203.0.113.7"},
                             include_text=f"{GATEWAY} 1;\n")
    assert hdrs["X-Real-IP"] == "203.0.113.7"
    assert hdrs["X-Forwarded-For"] == f"203.0.113.7, {GATEWAY}"
    assert _backend_ip(hdrs) == "203.0.113.7"


def test_untrusted_peer_cannot_inject_xff(docker_backend):
    # 非可信来源不带 X-Real-IP 时，后端会退到 XFF；前端不得转发客户端自填的 XFF
    hdrs = _frontend_headers("203.0.113.9", {"X-Forwarded-For": "6.6.6.7"},
                             include_text=f"{GATEWAY} 1;\n")
    assert hdrs == {"X-Real-IP": "203.0.113.9", "X-Forwarded-For": "203.0.113.9"}
    assert _backend_ip(hdrs) == "203.0.113.9"


def test_proto_not_trusted_when_published():
    assert forwarded_proto(GATEWAY, "https", include_text="") == "http"
    assert forwarded_proto(GATEWAY, "https") == "https"


def test_all_api_proxies_use_guarded_xff():
    assert "$proxy_add_x_forwarded_for;" not in re.sub(r"map .*?\}", "", CONF, flags=re.S)
    sets = re.findall(r"proxy_set_header\s+X-Forwarded-For\s+(\S+);", CONF)
    assert sets and all(v == "$forwarded_for" for v in sets)


# ---- 镜像与编排接线 ----

def test_dockerfile_installs_entrypoint_script():
    df = (DOCKER / "Dockerfile.frontend").read_text(encoding="utf-8")
    m = re.search(r"COPY deploy/docker/nginx-host-proxy\.sh (/docker-entrypoint\.d/\S+\.sh)", df)
    assert m, "前端镜像未安装入口脚本"
    assert f"chmod 755 {m.group(1)}" in df  # 官方入口只执行有 x 权限的 .sh
    # 预置空清单：绕过入口脚本时 include 不缺文件，且默认谁都不信
    assert ": > /etc/nginx/host-proxy.geo" in df


def test_compose_passes_trust_inputs_to_frontend():
    compose = (DOCKER / "docker-compose.yml").read_text(encoding="utf-8")
    fe = compose[compose.index("  frontend:"):]
    fe = fe[:re.search(r"\n  \S", fe[3:]).start() + 3]
    for line in ("FRONTEND_BIND=${FRONTEND_BIND:-127.0.0.1}",
                 "PLASMID_GATEWAY=${PLASMID_GATEWAY:-172.28.0.1}",
                 "HOST_PROXY_ADDRS=${HOST_PROXY_ADDRS:-auto}"):
        assert line in fe, line
    env = (DOCKER / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^HOST_PROXY_ADDRS=auto$", env, re.M)


def test_script_is_posix_sh():
    head = SCRIPT.read_text(encoding="utf-8").splitlines()[0]
    assert head == "#!/bin/sh"  # nginx:alpine 只有 busybox sh
    assert "[[" not in SCRIPT.read_text(encoding="utf-8")
