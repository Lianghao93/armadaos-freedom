# Lenovo Legion Y700 Gen4（TB322FC / `lenovo,elden`）

ArmadaOS 的 Y700 Gen4（TB322FC）适配目录。设备树兼容串为 `lenovo,elden`、`qcom,sm8750`，机型名为 "Lenovo Legion Y700 Gen4"。

本目录只维护板级描述与本地补丁，不是独立板级内核：它与其它 SM8750 机型共用 Linux 7.2.6 基础内核和 Armada（armadaos-freedom）补丁集，位于隔离的多机型分支；板级差异以本目录的文件叠加。

当前为 WIP：Android 启动、引导菜单与按键已在真机确认；内部 Linux 与 Steam 可用，触摸坐标经使用确认为准确。其余项目见“未决事项”，因此本目录不声明适配完成、稳定或全部硬件可用。

## 文件

- `../../packages/kernel/dts/sm8750-lenovo-elden.dts`
  从上游固定版本原样复制（byte-for-byte）的板级 DTS，保留原始 SPDX（BSD-3-Clause）与 Qualcomm 版权，不直接编辑。
  来源：https://github.com/h0cheung/tb322fc-linux-kernel
  固定提交：6f97250ead155fc3b980856323e99a4623dae73d
  固定文件：https://github.com/h0cheung/tb322fc-linux-kernel/blob/6f97250ead155fc3b980856323e99a4623dae73d/arch/arm64/boot/dts/qcom/sm8750-lenovo-elden.dts
- `../../packages/kernel/dts/sm8750-lenovo-elden.dts.patch`
  在上述原始 DTS 之上叠加的本地 unified 补丁，只含面向运行时的最小适配。
- `../../packages/kernel/dts/sm8750-lenovo-elden-camera.dtsi`
  相机伴生片段：从同一固定提交的 `sm8750.dtsi` 第 3072–3309 行逐字摘出 `cci0`、`cci1`、`camss` 三个完整节点，置于 `&soc {}` 下，仅把缩进归一化一个 tab，保留源文件 BSD-3-Clause SPDX 与 Qualcomm 版权。它补齐板级 DTS 引用的 `&camss`、`&cci0`、`&cci0_i2c0`、`&cci1`、`&cci1_i2c0` 标签；不引入其它 SoC 差异，也不新增驱动。

## 本地补丁内容

- 紧接 `#include "sm8750-pmics.dtsi"` 之后包含相机伴生片段。
- 删除 `memory@a0000000` 节点及其 RAM 注释：内存节点由运行时引导 DT（kShim handoff_memory）重建，原作者的固定 RAM map 未在本机确认。
- `chosen` 保留 `#address-cells`、`#size-cells`、`ranges` 与 simple-framebuffer 模板；`framebuffer` 去掉 unit-address 与固定 `reg`/`width`/`height`/`stride`/`format`，改加 `status = "disabled"`，由运行时 simplefb 处理器按模板填真实地址与尺寸。不向 framebuffer 添加 `memory-region`。
- 给 `reserved-memory` 下的 `ramoops@c2000000` 与 `splash-region@fc800000` 加 `status = "disabled"`，保留其地址元数据但停用，因为这些固定地址未经验证。
- 从 `&mdss` 删除 `memory-region = <&splash_mem>` 与 `iommus`，其余内容不变。
- 保留原 DTS 的 `&apps_smmu` 覆盖（含 `qcom,preserve-boot-mappings`）：该属性的消费方是 `../../packages/kernel/patches/0146-iommu-arm-smmu-elden-boot-handoff.patch`，本目录不重复实现，也不引入全局 SMMU 变通。
- 其余内容（音频、WiFi、USB、面板、触摸等）保持不变。

## 未决事项

- Steam 内正常重启后物理屏幕仍为黑屏；空闲且仅显示 HUD 时屏幕同样为黑屏。
- 充电与双 USB 端口（长边／短边语义）仍未完成。
- OTA 的源码策略与钩子已实现，但引导载荷同步会超时，端到端更新尚未验收通过。
- 设置中的手动／自动选择与默认条目目前只在本次会话内生效，持久化明确延后。
- 相机未验证；此处只提供控制节点。

构建与测试报告保存在仓库之外，本文件不作构建成功声明。本目录不规定固定的内建 DTB、内建 initramfs、固定内核命令行或 OTA 方式。
