# SM8850 输出颜色 LUT 移植记录

当前状态：仅离线研究，未部署。现场稳定使用 quiet GPU 解绑镜像 `ca420325...830bda`，运行内核仍为 `7.3.0-rc4-tb323fu-t42`。

## 问题与测量

DPU vblank 8.333344ms稳定；正常 UI Gamescope GPU 合成约10.7ms，跨2/3扫描周期。临时单变量关闭色管约3.3ms；随后已恢复。当前CRTC没有 DEGAMMA_LUT/DPU_3D_LUT，现有Gamescope0017不能卸载。此结论不等于所有长间隔与死机已修复。

## 基线和依赖

复用 Armada `0068-drm-msm-dpu-lutdma-dspp-igc-gamut.patch` 与 `0069-dt-bindings-display-msm-dpu-lutdma-reg.patch`。0068在作者T42有11处hunk上下文不匹配，原因是已存在SPR、VFP和文件尾上下文，不能删作者逻辑或重复添加GC。0069在原T42严格dry-run通过。当前只复制目标源码并在自有工作区部分apply，原T42完整源码只读。

保留作者0008 SPR/DSC、0065 VFP、0079颜色变化强制完整modeset，以及0082双GDSC代码。Foundation合并分成接口/CTL/DSPP、CRTC、KMS生命周期三组，再单独加入8850支持。

## 固定厂家依据

OnePlusOSS `android_kernel_modules_and_devicetree_oneplus_sm8850` 固定提交 `5ab2a689ff87d7d28c511f1762cf41c1b90d965a`：oplus_build.sh明确SM8850与Canoe；DPU13和MDSS/DSPP绝对地址与T42匹配。

- LUTDMA v4：资源0x09b60000/0x7000；SB偏移0x800；decode0x18360114；SB只写QUEUE0。
- CTL原生id从1起；slot=0x1000*id，IOVA+0、command+4；完成status+0x44、clear+0x48、BIT3。
- 保留现有在途缓冲区等待、屏障、last descriptor和runtime-resume生命周期。
- IGC v5.1：偏移0x1260、版本0x50001；沿用已验v5无dither的257-entry/16-bit packing与BIT8，不启用HFI/high precision/dither。
- GAMUT v4.3：偏移0x1000、版本0x40003；17³ LUT。
- Kaanapali catalog增加独立v4 reg_dma配置，不冒用v3；保留PCC/SPR。
- Schema仍为qcom,sm8650-dpu.yaml，保留Kaanapali双power-domain约束。
- 普通厂家配置无sid_phys；SID切换仅trusted VM。保持msm_mdss.c、dpu_hw_top.c与SMMU参数不变，不引入VM/SID写入。

厂家错误清理定义+0x5c与实际函数写+0x60矛盾仍未决，不能默默猜寄存器。trigger_sel_off=0x119c也没有普通SB提交消费者，不新增任意MMIO。

## 验证和交付边界

CodeBuddy执行小项代码修改；Codex逐项差异审查。编译仅指定192.168.88.9 Docker；检查patch完整应用、SPR/VFP/GDSC保全和kernel构建。未编译/未上机前保留WIP状态。任何新镜像写入和重启需用户本次决定；现场需要确认LUT属性、DMA完成、R/G/B/灰阶颜色、逐帧数据和恢复路径后才能认定成功。

## 2026-10-09 用户调整优先级与内核路线

1. 先解决Steam右侧120Hz设置、实际显示扫描/游戏帧率与最高可用模式不一致的问题。
2. 再处理主UI帧时间锯齿、延迟与颜色处理卸载；当前foundation修改仅保留NAS WIP，不继续部署。
3. 将TB323FU板级DTS、驱动及已验证补丁移植进Armada的packages/kernel构建链，同时接通设备启动镜像打包、安装与更新入口。最终采用Armada内核包与系统内OTA更新，独立作者T42内核只作为当前验证/恢复基线。

不能把用户空间已更新称为设备内核已更新。新的内核/镜像更新仍需独立验证和用户决定重启。
