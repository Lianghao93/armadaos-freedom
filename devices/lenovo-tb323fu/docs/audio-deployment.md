# TB323FU 扬声器保护：PipeWire 手动加载 → WirePlumber 0.5.18 自动 software DSP

2026-10-09 01:54 香港时间实际状态：三份配置均已部署，parser/保护 sink/RAW 隐藏和两路功放均已核验；用户已听到正常声音。新的 unit 启停绑定本轮已实测：停止 WirePlumber 时 gain 自动停止、两路恢复 159；重启后保护 sink 重新就绪、gain 自动成功、两路 21。以下迁移步骤为复现说明。

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

## 未测项 / 未解决项（配置写出 ≠ 真机验收）

- parser / live-load 已核验；graph 首字节必须为 `{`，原首行注释导致解析失败的问题已修正。
- 保护 sink 的真实链接、RAW 对 Pulse 隐藏已核验；普通客户端仅一份 Speakers。
- helper 等待声卡/滤镜的远程测试 106 项已通过，真实 WP 停启增益变化已核验；用户听感已通过。重启后的完整启动时序仍待复核。

部署服务重启、gain 生命周期/声卡就绪、真实验证由 root 另行负责；本文不含执行脚本。helper 源保持不变，
仅 `tb323fu-speaker-gain.service` 的两行依赖/安装行（`BindsTo=`、`WantedBy=`）由单服务扩为
`pipewire.service wireplumber.service`；`After=` 行本就包含两个服务、保持不变。runtime unit 的
stop/start 已在真实用户会话核验，依据实际 gain 状态而非 unit 文本；听感由用户单独确认。
