# 固定来源与回收记录（2026-10-09）

| 组件 | 固定来源 |
|---|---|
| 维护仓库起点 | armada-os/armada main `887fd306b17b3060a70cc749e09630e1a640af14` |
| 当前装机用户空间 | Armada preview `816091ecff7bebf78d4005175a4e5c3e237e91ab` |
| 作者板级源码 | joonhoekim/tb323fu-linux `11a25bf89b65fff48112a91a0b25bc247e7736d8`，kernel/PROVENANCE.md 中保留原来源 |
| Linux 基线 | `93f51579e7df248780214094418f205253383cc5`，v7.3-rc4，运行 release `7.3.0-rc4-tb323fu-t42` |
| kShimC 缓存上游 | sunflower2333/kShimC feature/2610 `121734f64dd97d26efaf94a264cdbe7ac0551e53` |
| kShimC 本地四提交 | HEAD `01a8c015e9a1889ed01ee63df8c5107413425648`，mbox 另存 patches/boot/ |
| CrDK 子模块 | Project-Aloha/CrDK `b6c3335136b7f6b28271917de11e8f11982975fc`，还需 ports/crdk 的构建补丁层 |
| 打包器缓存上游 | Project-Aloha/DualBootKernelPatcher feature/2610 `2276163d066596cb0750e76cbfeec5c193b1779f` |
| 打包器本地基线 | HEAD `aa819b0d1be76e83256f64cf77c0f6dfe4174eea` + 共享 PayloadFrom 工作树补丁与测试 |

`boot/kshim-success-source/` 是从实际构建服务器成功版本目录回收的 .config、CMake、src、ports、configs、docs、tests。其 main.c SHA 为 `0f9b58b93306ef972081b4ce7f3a93c41db85be35d615da8d9fc7dcae130666b`，与 NAS 上此前成功片段一致。它不同于后续最新研究工作树的 main.c，不混作一个版本。

已成功使用的 shim 二进制 SHA：`03e8ed4ac48fe7f950f5a96860c050e685efe70d33509f38c93b22fe181a0126`。本库保存源码和来源信息，未把设备备份、镜像、固件分区、账号、网络密码或私有 DLL 纳入。

依赖的完整 submodule 仍需按固定版本取得；此回收尚未做全量源码再构建、二进制复现，不能据此认定镜像构建入口完整。固定作者源码已保留；旧的 64f7e6b shallow 作者快照不作为 T42 来源。

## quiet GPU 解绑候选

两份 DTB 语义差异均仅移除 quiet-thermal 四个 GPU map。原 PackedKernel 解包再打包的 SHA 完全相同；修改后再次解包，Image 与 shim 逐字节相同，新 DTB 也逐字节匹配。Boot v4/AVB NONE 保持有效结构，分区镜像长度仍为 100663296 字节。

- 当前已运行 boot_a SHA：`dd442b230a7a48f71e848758d8e461f0f85278eafe0b480d99d4b79651892c1d`；本轮重新在设备上只读确认。
- 待测试镜像 SHA：`ca4203253b9ffeebf72374b8ef87d16b42e575c2528c2d240a4cf65868830bda`。
- 状态：2026-10-09 08:37:43 写入；经用户明确批准重启。常规完整回读及 O_DIRECT 完整回读均与候选一致，双 GPT 未变。新 boot ID `eedcf26f-e74c-4f81-86fc-e1b31b8226fb`；quiet 温区实际只含七个 CPU 绑定，Steam/MangoHud 恢复。高温负载与用户体验仍待验证。

## 主 UI 帧时间

实际 CRTC 106 的完整只读属性查询成功，未暴露 DEGAMMA_LUT 或 DPU_3D_LUT，只有 CTM。现有 Gamescope 0017 硬件卸载条件不满足。

临时色管开关的对照把合成 GPU 时间 10.7ms 降到 3.3ms；不能把全局禁色管当作最终优化。逐帧 CSV 多数间隔约 8.3ms，但有长间隔。原设置已恢复；色彩一致性、整段帧时间稳定性和游戏死机仍待解决。
