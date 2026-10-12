# Lenovo Legion Tab Gen 5 / Y700（TB323FU）

这是 ArmadaOS 的 TB323FU / SM8850P 平板适配目录。上游项目为 [armada-os/armada](https://github.com/armada-os/armada)，组件来源和固定版本见 [SOURCES.md](docs/SOURCES.md)。

本仓库维护源码与补丁，尚不提供完整、可重复构建并通过整机验收的 Y700 镜像。部分设备文件仍是独立 overlay，需要按目录说明集成；不能直接把上游通用镜像当作本机安装包。

## 目录

- `boot/`：kShimC 源码、quiet 温区 GPU 解绑设备树片段，以及 [STAR_CHART 构建配方](boot/star-chart-build/README.md)。
- `kernel/`：固定版本的板级源码和 `runtime-fixes/` 中的设备补丁。
- `rootfs/`：双 USB、音频、Wi-Fi 等设备配置。扬声器配置见 [部署说明](docs/audio-deployment.md)。
- `patches/boot/`：引导及打包器补丁。旧 kShimC 邮箱补丁用于来源追溯，不应重复叠加到当前源码快照。
- `diagnostics/`、`tests/`：诊断工具和回归测试源码。运行生成的报告、设备日志及备份在仓库外保存。

## 已接入的功能

- 设备识别、CPU 拓扑、性能配置和桌面触摸校准。
- 机型匹配的 30／60／90／120／165 Hz 档位、真实 165 Hz 面板时序和高像素时钟 EDID 修复；旧内核仍可回退到其实际公布的 164 Hz。
- kShimC 中英文 STAR_CHART 菜单：Android、机身 ArmadaOS、TF 卡 ArmadaOS、高级启动、语言、重启和关机。高级启动包含 Linux 诊断、Recovery、Fastboot、9008；并非所有高级动作均已逐项验收。
- 双 USB 路由、扬声器保护、5 GHz Wi-Fi 扫描，以及 MangoHud 电池和温度采集。
- G9 2026 基础按键和 RGB 开关、颜色、亮度。匹配 TB323FU 设备型号和 USB `3537:1136`；其他 G9 型号不套用此配置。
- `armada-g9-double-home.service` 被动监听 G9 主输入及同一设备的 Consumer Control 接口：双击 Home 打开右侧快捷菜单，单按保留 Steam 原行为。依赖现有 InputPlumber 共享输入支持；其他型号不启动监听。
- Armada Control 通过实际风扇控制能力决定是否显示风扇页、风扇曲线和充电风扇设置。

## 已知限制

90 Hz 偶发物理花屏、Steam 主界面低帧、复杂场景帧时间波动，以及游戏期间死机／自动重启仍未完全解决。SGSR 高刷问题与面板时序、补帧是不同链路，不应混为同一项修复。

设备 overlay、内核及引导的完整镜像构建和 OTA 集成仍待完成。G9 的全部背键、IMU、震动、多模式和插拔／休眠后的 RGB 恢复尚未完整验证。硬件充电固件可能沿用 Android 的充电上限，Linux 调整入口尚未接入。

小黄鸭插件在独立仓库维护，不包含在本目录。保留上游及各组件的许可证、字体授权和来源记录。
