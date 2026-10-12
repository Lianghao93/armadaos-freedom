# Y700 Gen 5 / TB323FU 平板适配

这是 `mydanyi/armadaos-y705` 的设备维护入口。仓库名按用户指定；实际设备仍是 **TB323FU / SM8850P**。

ArmadaOS 上游完整 main 历史从 `armada-os/armada` 复制，初始基线 `887fd306b17b3060a70cc749e09630e1a640af14`。此前装机记录的平板预览版基线为 `816091ecff7bebf78d4005175a4e5c3e237e91ab`，两者不能混为同一已验证版本。保留上游 LICENSE.md 和原始构建入口。最新引导源码及实机状态见 [2026-10-12 STAR 菜单同步记录](docs/status-20261012-kshim-star.md)。

## 已纳入的成果

- `system_files/usr/lib/armada/devices/lenovo-tb323fu.conf` 与 device-env：识别设备，声明 SM8850 的 6+2 CPU 拓扑、屏幕方向和旋转 shader。设备实际使用的账号配置不入库。
- `rootfs/`：双 USB 路由脚本和 udev 规则。已在长边充电、短边 G9 手柄组合上复测恢复；其他插拔顺序和休眠仍待测。这些文件先作为设备 overlay 保存，尚未自动并入全机型镜像。
- `diagnostics/`、`tests/`：有大小上限的运行记录、逐帧与同步分析，以及现有 USB/记录器回归测试。服务器 Docker 中通过 18 项 USB/记录器测试；这不等于完整镜像和游戏稳定性验收。
- `boot/quiet-thermal-no-gpu.dtsi`：仅移除 quiet 温区 44/46/48/50°C 的四个 GPU cooling-map，保留 CPU 绑定和独立 GPU 结温保护。TF 和机身 DTB 离线语义差异均只有这四项。2026-10-09 经用户批准写入并重启：完整回读一致，现场 quiet 温区仅剩七个 CPU 绑定，Steam/MangoHud 已恢复。
- `boot/kshim-success-source/`：更新为本轮实际构建的完整 kShimC 非依赖源码快照，包含 STAR_CHART 主题、中英文切换、文楷菜单字体、横屏布局、高级启动菜单及此前启动解析修复。`.config` 来自实际 runtime 构建配置；Git 中的文本统一为 LF。源码归档与依赖边界见 [固定来源](docs/SOURCES-AND-STATUS.md)，构建脚本及输入说明见 [STAR 构建记录](boot/star-chart-build/README.md)。
- `patches/boot/`：保留打包器的本地提交、共享 PayloadFrom 变更和 kShimC 本地四提交，作为历史追溯；这些旧补丁不是当前 STAR 源码的完整增量，不应再次无条件叠加到当前快照。
- `docs/`：温控、充电限制及帧时间诊断证据。设备原有 80% 充电上限由充电固件沿用，Linux 调整入口尚未实现。

- 音频保护 filter/WirePlumber 生命周期、Decky 临时目录和 Wi-Fi 扫描/5GHz 修复已收录 `rootfs/` 与 `kernel/runtime-fixes/`，详见各部署说明；源码入库不等于镜像自动安装。
- MangoHud 电池与温度采集作为 `Patch9` 接入 spec；`Patch10` 修正与配套 Gamescope 的 app-ready / visible 帧时间字段对应关系。曲线显示真实呈现间隔，不做平滑。CPU/GPU 功耗和 VRAM 温度仍无真实接口。
- Prime CPU 编号按实际 policy2 的 `related_cpus=2 3` 修正为 `2-3`。本轮只修正仓库元数据；未刷新设备 Control 服务或调整运行中的核分配/频率。

- Gamescope / Armada Control：按 `lenovo-tb323fu` 和内置 `DSI-1` 匹配五档 30/60/90/120/165；旧内核只公布 164 时保留 164 回退，不虚构模式、不限制其他机型或外屏。真实 165 面板时序补丁保存在 `kernel/runtime-fixes/`，替换原 0109；EDID 高像素时钟修复为 Gamescope `0034`。两者分别解决物理扫描模式与合成 EDID，不能互相替代。用户已确认五档正确，并在关闭 SGSR 的《土豆兄弟》中确认 165 FPS。
- PR #665 的边缘滑动起点判定和侧栏打开期间暂停滑动手势已同步到 Gamescope `0033`、Control 前后端及系统 RPC。用户已确认拖动滑条不会再误关菜单。Control 从同一源码构建到系统种子，避免只替换 homebrew 插件后被开机同步覆盖。

- 桌面触摸校准持久化：TB323FU profile 声明单位校准矩阵，`desktop-bootstrap` 每次桌面登录按机型自动应用（持久化进用户 `kcminputrc` + KWin live 属性）。现场已接受身份矩阵（能打开、位置准确）；固件 WDT 静默/黑屏恢复仍是独立问题，本次未做内核或永久固件修复。详见 [desktop-touch-20261010.md](docs/desktop-touch-20261010.md)。

- kShimC STAR 菜单：原厂 Android、机身 ArmadaOS、TF 卡 ArmadaOS、高级启动模式、语言、重启和关机；高级页包含 Linux 诊断、Recovery、Fastboot 和 9008。用户已确认整体菜单测试无问题，后台确认本轮镜像启动到 `7.3.0-rc4-tb323fu-t42 #5` 的机身 Linux，Gamescope 和 Steam 正常运行。高级动作及中英文切换尚未逐项单独验收；语言只在本次开机内生效，重启恢复中文。底部标注「适配维护：B站『单翼』｜免费使用，谨防收费诈骗。」

## 当前关键问题

90 Hz 仍有偶发物理花屏，切到 165 再切回 90 只能临时恢复。主界面低帧、复杂场景帧时间波动和此前异常重启没有完成整体修复；不能以菜单五档、平均 FPS 或静态场景通过替代动态场景验收。

SGSR 与高刷组合的问题已通过关闭 SGSR 对照定位，按用户要求由上游继续处理，本次不叠加新的 SGSR 实验。小黄鸭核心在独立仓库维护，不随本次 Y700 系统源码同步。保留用户的 60% / 80% 性能设置。

最新引导改动、自测和验收范围见 [2026-10-12 STAR 菜单记录](docs/status-20261012-kshim-star.md)。[2026-10-11 同步记录](docs/status-20261011.md)及较早的逐轮诊断保留为历史证据。本次同步不继续主界面高刷实验，设备 profile 保持 `ARMADA_GAMESCOPE_USE_ROTATION_SHADER=1`，与已恢复的 #5 内核配套。

## 构建和验证边界

所有编译和 CPU 密集工作在指定构建服务器的 Docker 执行；GitHub Actions 暂时关闭，避免复制上游工作流后自动构建或发布。源码、补丁、结果和证据以 NAS 上的维护 checkout 为准。

当前仓库是可维护的源码起点，**不是已验收的 Y700 镜像发行版**。需要补齐固定的内核/引导源清单、设备 overlay 安装入口、可重复打包与版本标识，再发布设备镜像。保留现有上游构建定义不代表 Y700 启动镜像已自动接入。

本次同步复用已经取得的 ARM64、主机、QEMU 和镜像校验证据，没有为仓库同步再次编译。STAR 构建配方仍依赖固定 CrDK/picolibc/LVGL、单独的 libfdt 构建输入及不随仓库分发的设备 payload；不能仅凭源码已入库宣称全新克隆可直接生成或通过系统 OTA 更新设备启动镜像。

标准 ArmadaOS 的 Btrfs / OSTree 布局仍是目标；当前设备上的早期 flat ext4 安装偏离目标，迁移尚未获准。不要沿用这项偏差制作正式发行镜像，也不要对现有游戏和数据直接重格式化。

更新设备启动镜像、重启由用户逐次决定。PR 和上游发布另需用户验收及本次明确批准。私有维护仓库的本次源码复制和成果入库已获授权。
