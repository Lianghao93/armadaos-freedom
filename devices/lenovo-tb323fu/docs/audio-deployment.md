# TB323FU 扬声器保护部署

使用 WirePlumber software DSP 保护扬声器，并将功放增益服务绑定到 PipeWire 和 WirePlumber 的生命周期。

## 文件映射

| 本仓库源文件 | 目标部署路径 | 属主/权限 |
| --- | --- | --- |
| `tb323fu-speakers-protection.json` | `/usr/share/tb323fu/pipewire/tb323fu-speakers-protection.json` | root:root / mode 0644 |
| `61-tb323fu-speakers-dsp.conf` | `/usr/share/wireplumber/wireplumber.conf.d/61-tb323fu-speakers-dsp.conf` | root:root / mode 0644 |
| `tb323fu-speaker-gain.service` | `/usr/lib/systemd/user/tb323fu-speaker-gain.service` | root:root / mode 0644 |

`61-tb323fu-speakers-dsp.conf` 中 `filter-path` 写死为上面的绝对部署路径。

## 迁移步骤（root 负责，本文不执行）

1. 备份并**停用**旧的手动加载配置 `/usr/share/pipewire/pipewire.conf.d/60-tb323fu-speakers.conf`——
   必须把它**移出所有会被扫描的 `*.conf` 目录**；仅改 basename 但仍以 `.conf` 结尾无效。
   目的是确保不再有原手动 `context.modules` 实例，避免出现两个同名保护 sink。
2. 部署上面三个新文件到对应路径。
3. 由 root 在**实际的 armada 用户会话**中（而非 root 自身的 user 会话）替换该 unit 后执行
   `systemctl --user daemon-reload` 与 `systemctl --user enable tb323fu-speaker-gain.service`。
   `enable` 依据 `WantedBy=pipewire.service wireplumber.service` 同时创建
   `pipewire.service.wants/tb323fu-speaker-gain.service` 与
   `wireplumber.service.wants/tb323fu-speaker-gain.service`。unit 现为
   `After=pipewire.service wireplumber.service` 且 `BindsTo=pipewire.service wireplumber.service`，
   因此 WirePlumber 的停止/重启一并参与 gain 生命周期：`ExecStop` 会在被绑定的 WP 服务停止前停下 gain；
   WP 由用户 `Wants` 启动时可一并启动 gain，并让既有 helper 等待保护 filter。
4. 由 root 重启/重载音频服务完成切换。

说明：
- 真实 RAW node 仍保留给 WirePlumber 连接使用；但对普通 PipeWire/Pulse 客户端不再可见
  （`hide-parent` 把匹配硬件 node 的所有非 wireplumber.daemon 客户端权限设为 `-`，WP 自身仍可链接）。
- 规则只按精确 `node.name` 匹配该 HiFi speaker sink；耳机 / Bluetooth / 其他 sink 不匹配。
- 保留原有两个 LADSPA 插件依赖（swh-plugins：`sc4_1882`、`fast_lookahead_limiter_1913`），未引入新依赖。

## 回滚

1. 停用新增的 WP fragment（移出被扫描的 `*.conf` 目录）。
2. 恢复原手动 `60-tb323fu-speakers.conf`。
3. 由 root 重启音频服务。
