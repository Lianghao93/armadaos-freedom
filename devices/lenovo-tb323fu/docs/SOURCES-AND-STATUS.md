# 固定来源与回收记录

## 当前快照：2026-10-12 STAR 菜单

`boot/kshim-success-source/` 现保存本轮实际 ARM64/QEMU 构建所用 `build/source.tar` 的完整非依赖源码；已替换下面 2026-10-09 的局部回收快照。实际构建的 `runtime.config` 保存为该目录的 `.config`。Git 入库时统一文本 LF 行尾，因此归档 SHA 表示原始构建输入，不能用来声称 Git 重新打包后的归档逐字节相同。

| 本轮对象 | SHA-256 / 来源 |
|---|---|
| 原始构建输入 `source.tar` | `3f8dc6d08e3945c874d8f1ab0789917db9781f62f77db98fecec547c6b9b1c5e` |
| 实际 4 MiB kShimC runtime | `8f49609199586e27fccf07e5feff966e95e733ba05987b0c35f607153092c13e` |
| 实机完整回读一致的 96 MiB boot 镜像 | `3e9ca47df76c123a42f2a72b6769750971fb37639fa8e14ed78bac9f526310d1` |
| 保留的 #5 Linux payload | `bfcc03543585aa9f7015917eebcb20fade8c1ef849080e459f653f8bd1c3a38d` |
| WenKai 菜单资产 `lxgw_wenkai_menu.ksf` | `c27192c3e32d8744ced14adfc606ddd5557241f22c4f6a01ec0df24aefcc5d60`，86,077 字节 |
| 字体上游 | LXGW WenKai Regular v1.522，提交 `e8b5b48b79f19f29aa68b0a178eab3472ea9f7e8`；资产同目录保留来源 JSON 与 OFL 文本 |

本轮镜像仍是原有八个 manifest 条目，仅更换 shim；其余七个条目的数据哈希、偏移、大小、flags 和 DT index 与构建基底一致。默认条目仍是机身 Linux，倒计时 8 秒。没有用本次主题变更替换 Linux、Android 或设备树 payload。

运行状态以本轮部署记录为准：用户已确认整体菜单测试无问题，后台确认机身 Linux `7.3.0-rc4-tb323fu-t42 #5`、Gamescope 与 Steam 已运行。设备恢复使用 GPU 旋转，仓库既有 profile 的 `ARMADA_GAMESCOPE_USE_ROTATION_SHADER=1` 与之吻合，不另改设备策略。高级动作和语言切换未逐项单独验收。

本次只是同步已验证的源码及既有证据，没有重新编译。构建记录见 [STAR 构建说明](../boot/star-chart-build/README.md)，准确测试计数及未完成项见 [本轮状态](status-20261012-kshim-star.md)。[本轮菜单变更统计](evidence/kshim-star-20261012/source-semantic-stat.txt)记录相对此前已验收菜单基线的 29 个文件变化，并非构建快照与入库源码的一致性证明。入库复制另已核对：忽略行尾后，仅增加实际 runtime 的 `.config`；对应比较记录保存在 NAS 的本次同步目录。

### 依赖和复现边界

原始非依赖归档不包含顶层 `lib/`、`third_party/` 和 `.git/`。下列版本来自实际使用的 gitlink；只有 `.gitmodules` 的 URL 不能恢复这些版本。

| 依赖 | 固定 gitlink |
|---|---|
| Project-Aloha/CrDK | `b6c3335136b7f6b28271917de11e8f11982975fc` |
| picolibc/picolibc | `f7edb0869e64b9d687f46961adeef7154921fe92` |
| lvgl/lvgl | `7f07a129e8d77f4984fff8e623fd5be18ff42e74` |

CrDK 还需要快照中 `ports/crdk/` 的补丁层及其来源记录。本轮执行环境从既有依赖归档恢复上述依赖；`lib/libfdt` 及 `libfdt_env.h` 也是单独构建输入，不由当前 `.gitmodules` 覆盖。此同步没有补完 libfdt 自动获取及完整依赖归档的公开复现链，不能将它描述为全新克隆即可构建的发行工程。设备 payload、现场备份、runtime/boot 二进制与连接脚本均不入库。

`patches/boot/` 的旧提交与补丁、`boot/quiet-thermal-no-gpu.dtsi` 继续保留。旧补丁用于追溯，不是需要重复叠加到当前源码的安装步骤。

## 历史记录：2026-10-09

以下版本、哈希及状态描述均属于该日记录，不能替代上面的当前 STAR 快照。

| 组件 | 固定来源 |
|---|---|
| 维护仓库起点 | armada-os/armada main `887fd306b17b3060a70cc749e09630e1a640af14` |
| 当时装机用户空间 | Armada preview `816091ecff7bebf78d4005175a4e5c3e237e91ab` |
| 作者板级源码 | joonhoekim/tb323fu-linux `11a25bf89b65fff48112a91a0b25bc247e7736d8`，kernel/PROVENANCE.md 中保留原来源 |
| Linux 基线 | `93f51579e7df248780214094418f205253383cc5`，v7.3-rc4，运行 release `7.3.0-rc4-tb323fu-t42` |
| kShimC 缓存上游 | sunflower2333/kShimC feature/2610 `121734f64dd97d26efaf94a264cdbe7ac0551e53` |
| kShimC 本地四提交 | HEAD `01a8c015e9a1889ed01ee63df8c5107413425648`，mbox 另存 patches/boot/ |
| CrDK 子模块 | Project-Aloha/CrDK `b6c3335136b7f6b28271917de11e8f11982975fc`，还需 ports/crdk 的构建补丁层 |
| 打包器缓存上游 | Project-Aloha/DualBootKernelPatcher feature/2610 `2276163d066596cb0750e76cbfeec5c193b1779f` |
| 打包器本地基线 | HEAD `aa819b0d1be76e83256f64cf77c0f6dfe4174eea` + 共享 PayloadFrom 工作树补丁与测试 |

该日 `boot/kshim-success-source/` 是从实际构建服务器成功版本目录回收的 .config、CMake、src、ports、configs、docs、tests。其 main.c SHA 为 `0f9b58b93306ef972081b4ce7f3a93c41db85be35d615da8d9fc7dcae130666b`，与 NAS 上当时的成功片段一致。它不同于当时后续研究工作树的 main.c；该历史哈希不再描述当前目录。

已成功使用的 shim 二进制 SHA：`03e8ed4ac48fe7f950f5a96860c050e685efe70d33509f38c93b22fe181a0126`。本库保存源码和来源信息，未把设备备份、镜像、固件分区、账号、网络密码或私有 DLL 纳入。

当时的局部回收尚未做全量源码再构建、二进制复现，不能据此认定镜像构建入口完整。固定作者源码已保留；旧的 64f7e6b shallow 作者快照不作为 T42 来源。当前依赖边界见上面的 2026-10-12 记录。

### quiet GPU 解绑候选（历史）

两份 DTB 语义差异均仅移除 quiet-thermal 四个 GPU map。原 PackedKernel 解包再打包的 SHA 完全相同；修改后再次解包，Image 与 shim 逐字节相同，新 DTB 也逐字节匹配。Boot v4/AVB NONE 保持有效结构，分区镜像长度仍为 100663296 字节。

- 当时已运行 boot_a SHA：`dd442b230a7a48f71e848758d8e461f0f85278eafe0b480d99d4b79651892c1d`；当轮重新在设备上只读确认。
- 当时的候选镜像 SHA：`ca4203253b9ffeebf72374b8ef87d16b42e575c2528c2d240a4cf65868830bda`。
- 状态：2026-10-09 08:37:43 写入；经用户明确批准重启。常规完整回读及 O_DIRECT 完整回读均与候选一致，双 GPT 未变。quiet 温区实际只含七个 CPU 绑定，Steam/MangoHud 恢复。高温负载与用户体验仍待验证。

### 主 UI 帧时间（历史）

实际 CRTC 106 的完整只读属性查询成功，未暴露 DEGAMMA_LUT 或 DPU_3D_LUT，只有 CTM。现有 Gamescope 0017 硬件卸载条件不满足。

临时色管开关的对照把合成 GPU 时间 10.7ms 降到 3.3ms；不能把全局禁色管当作最终优化。逐帧 CSV 多数间隔约 8.3ms，但有长间隔。原设置已恢复；色彩一致性、整段帧时间稳定性和游戏死机仍待解决。
