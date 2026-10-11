#!/bin/sh
# 生成前端 nginx 的「可信宿主机前置」清单（geo 片段，nginx.conf 中 include）。
# 由 nginx 官方镜像的 /docker-entrypoint.sh 在启动 nginx 前执行
# （镜像内路径 /docker-entrypoint.d/40-plasmid-host-proxy.sh），失败即拒绝启动。
#
# 只有来自清单内对端的 X-Real-IP / X-Forwarded-For / X-Forwarded-Proto 才透传给
# 后端。宿主机 nginx 经发布端口（docker-proxy 或 hairpin NAT）进来时，来源是
# compose 网络网关；但端口对外发布（FRONTEND_BIND=0.0.0.0）时，经 docker-proxy
# 直连的外部客户端（如 IPv6、userland-proxy 场景）同样显示为网关，信任网关等于
# 让任何人伪造来源 IP 换限流桶（审查 02 M2 / 03 L7）。因此：
#   HOST_PROXY_ADDRS=auto（默认）：FRONTEND_BIND 为本机回环时信任网关
#                                   PLASMID_GATEWAY，否则谁都不信
#   HOST_PROXY_ADDRS=none          ：谁都不信（只用 socket 对端地址）
#   HOST_PROXY_ADDRS=a,b           ：显式清单，只接受单个地址，不接受网段
# 容器内回环（127.0.0.1、::1）已在 nginx.conf 内置，这里跳过。
set -eu

out="${HOST_PROXY_GEO_FILE:-/etc/nginx/host-proxy.geo}"
bind="${FRONTEND_BIND:-127.0.0.1}"
gateway="${PLASMID_GATEWAY:-172.28.0.1}"
addrs="${HOST_PROXY_ADDRS:-auto}"

log() { echo "plasmid-host-proxy: $*" >&2; }

if [ "$addrs" = "auto" ]; then
    case "$bind" in
        127.*|::1|"[::1]"|localhost)
            addrs="$gateway"
            ;;
        *)
            addrs="none"
            log "FRONTEND_BIND=$bind 对外发布：不采信网关 $gateway 转来的 X-Real-IP 等头；" \
                "宿主机前置反代请改 FRONTEND_BIND=127.0.0.1（或显式设 HOST_PROXY_ADDRS）"
            ;;
    esac
fi

tmp="$out.tmp.$$"
: > "$tmp"
trap 'rm -f "$tmp"' EXIT

if [ "$addrs" != "none" ]; then
    for a in $(echo "$addrs" | tr ',' ' '); do
        host="${a%/*}"
        if [ "$host" != "$a" ]; then
            case "$host" in *:*) single=128 ;; *) single=32 ;; esac
            if [ "${a##*/}" != "$single" ]; then
                log "拒绝 HOST_PROXY_ADDRS 中的网段 $a：只接受单个地址"
                exit 1
            fi
        fi
        case "$host" in
            ""|*[!0-9A-Fa-f:.]*)
                log "HOST_PROXY_ADDRS 中的地址非法: $a"
                exit 1
                ;;
            127.0.0.1|::1)
                continue
                ;;
        esac
        printf '%s 1;\n' "$host" >> "$tmp"
    done
fi

mv "$tmp" "$out"
trap - EXIT
if [ -s "$out" ]; then
    log "可信宿主机前置: $(awk '{print $1}' "$out" | tr '\n' ' ')"
else
    log "未配置可信宿主机前置，客户端 IP 一律取连接对端"
fi
