# 许可与版权说明

[English](LICENSING.md)

## 项目身份与原创贡献

**armadaos-freedom（ArmadaOS Freedom）** 是基于 ArmadaOS 独立维护和扩展、面向 ARM64 平板的 Linux 发行版项目。

Copyright (C) 2026 danyi and the armadaos-freedom contributors.

上述声明适用于 danyi 及 armadaos-freedom 各贡献者享有版权的原创代码和修改，不主张拥有继承自 ArmadaOS 或引入自其他项目的内容的版权。这些内容原有的版权及来源署名继续有效。

Freedom 的工作包括平板系统集成、机型专用配置、kShimC 引导集成，以及输入和控制软件的新增功能。一项功能可能同时包含我们的修改和上游代码，其作者和许可应以实际文件及历史记录为准。

## 默认许可

除文件或组件另有适用许可外，armadaos-freedom 自行编写的软件和脚本采用 **GNU 通用公共许可证第 2 版或任何后续版本**（`GPL-2.0-or-later`）发布。GPL 第 2 版的完整文本见 [LICENSE](LICENSE)。

这些作品按 GPL 所述不提供担保。对既有组件的修改遵循该组件的许可要求。本仓库的默认许可不替代组件的专用许可，也不改变第三方内容原有的授权。

## 随附和引用的组件

下表用于说明主要组件的许可范围，不替代各组件自身的声明。具体以组件的许可证文件、SPDX 标识、源码声明及来源记录为准。

| 组件 | 许可及适用条款来源 |
| --- | --- |
| 从 ArmadaOS 继承的原创软件和脚本 | `GPL-2.0-or-later`；保留 Armada 贡献者的版权声明，见[上游许可证](https://github.com/armada-os/armada/blob/main/LICENSE.md)。 |
| kShimC 源码快照 | kShimC 原创源码为 `GPL-3.0-only`；另有许可的代码和资产继续遵循各自条款。见快照内的 [LICENSE](devices/lenovo-tb323fu/boot/kshim-success-source/LICENSE) 和[许可说明](devices/lenovo-tb323fu/boot/kshim-success-source/README.md#license--许可证)。 |
| Armada RGB | 按[包清单](packages/armada-rgb/Cargo.toml)中的声明采用 `GPL-3.0-or-later`。 |
| TB323FU 板级支持快照 | 包含 GPL、MIT、BSD 及文档许可等多种授权；保留 Joonhoe Kim 及其他作者的声明。见快照内的 [NOTICE](devices/lenovo-tb323fu/kernel/tb323fu-linux-11a25bf8/tb323fu-linux-11a25bf89b65fff48112a91a0b25bc247e7736d8/NOTICE)、`REUSE.toml` 和 `LICENSES/`。 |
| 内核补丁和设备树 | 遵循每个文件的许可并保留原作者署名。引入的设备支持保留其来源，包括经 ArmadaOS 继承自 ROCKNIX 的文件。见[内核补丁来源](packages/kernel/PATCHES.md)及 [TB323FU 组件来源](devices/lenovo-tb323fu/docs/SOURCES.md)。 |
| 源自 Universal Blue image-template 和 Bazzite 的构建工具 | 相应上游内容采用 `Apache-2.0`，保留其声明。见 [image-template](https://github.com/ublue-os/image-template) 和 [Bazzite](https://github.com/ublue-os/bazzite)。 |
| 菜单字体 | SIL Open Font License 1.1 及各字体随附的声明。见[字体来源](devices/lenovo-tb323fu/docs/SOURCES.md#字体)和字体资产目录。 |
| 其他软件包、依赖、固件和资产 | 遵循各自的许可和分发条款，不统一归入本仓库的默认 GPL 许可。 |

固定版本及本次同步 kShimC 许可和 FTM4 清理的范围见[组件来源](devices/lenovo-tb323fu/docs/SOURCES.md)。当前启动菜单使用 kShimC，这不改变其他继承的设备支持文件的来源和许可。

## 再分发与贡献

保留适用的版权声明、许可证文本、来源署名和修改说明。分发受许可约束的二进制或系统镜像时，应满足所含组件各自许可对对应源码提供方式等事项的要求。

新增 Freedom 原创源码文件应注明实际版权人和适用许可。修改既有文件时，保留原有声明，并按相应许可要求注明修改及日期。贡献代码不会自动将贡献者的版权转让给 danyi、ArmadaOS 或其他贡献者。
