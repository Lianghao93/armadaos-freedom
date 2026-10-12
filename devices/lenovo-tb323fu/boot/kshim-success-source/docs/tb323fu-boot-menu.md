# TB323FU 启动菜单

`configs/tb323fu_defconfig` 选择 SURFACE_DARK、中文、逆时针 270 度输出和高级启动菜单。相对先前 90 度候选，原生画面翻转 180 度，逻辑横屏尺寸不变。其他配置默认不启用板级旋转、独立说明布局或 Qualcomm 重启目标。

- 主菜单：原厂 Android、机身 ArmadaOS、TF 卡 ArmadaOS、高级启动模式、重启、关机。
- 高级启动模式：Linux 诊断模式、Recovery 恢复模式、Fastboot 模式、9008 下载模式、返回主菜单。
- 默认机身 ArmadaOS，显示“8 秒后启动机身 ArmadaOS”并动态更新秒数；任何有效按键交互取消自动启动，进入或返回高级菜单不会恢复倒计时。
- Linux 诊断项通过自定义 DT 的 `/chosen/armada,boot-stage = maintenance` 识别；平台层保存原 payload 索引，不能用显示行号直接索引 manifest。
- 说明文字读取同一 payload 的自定义 DT：`armada,boot-stage = maintenance` 优先识别诊断环境，其次按 `/chosen/armada,root-medium = internal / tf` 识别机身和 TF 卡。实际输入 DT 使用 `root-medium`，不存在 `boot-medium`。只有 TB 配置启用的 `CONFIG_KSHIM_MENU_BASE_ANDROID` 将无自定义 DT 的 base image 明确描述为 Android；其他配置保留“启动设备原有系统。”，未知元数据使用“启动所选系统。”。不按显示名称或固定顺序猜测。
- 右侧标题、说明和按钮文字跟随焦点；按钮文字由平台动作元数据指定。倒计时和失败提示独立显示，左下固定“音量＋／－切换 · 电源键确认”。`CONFIG_KSHIM_MENU_ENTRY_DETAILS` 仅由此配置启用；Surface 双栏高度按实际菜单行数和消息、按钮、页脚所需空间居中，小屏列表独立滚动，其他配置保留默认布局。描述 API 为可选扩展，旧名字数组接口继续可用。

| 选项 | 说明 | 按钮 |
| --- | --- | --- |
| 原厂 Android | 启动设备原有的 Android 系统。 | 启动 |
| 机身 ArmadaOS | 启动安装在机身存储中的 ArmadaOS。 | 启动 |
| TF 卡 ArmadaOS | 启动安装在 TF 卡中的 ArmadaOS，请先插入对应 TF 卡。 | 启动 |
| 高级启动模式 | 进入 Linux 诊断、恢复或刷机模式。 | 进入 |
| 重启 | 重新启动设备。 | 重启 |
| 关机 | 关闭设备电源。 | 关机 |
| Linux 诊断模式 | 启动 Linux 诊断环境，用于检查启动和硬件问题。 | 启动 |
| Recovery 恢复模式 | 进入 Android 恢复模式，进行系统维护。 | 进入 |
| Fastboot 模式 | 进入 Fastboot，通过 USB 连接电脑进行维护或刷写。 | 进入 |
| 9008 下载模式 | 进入高通 9008 下载模式，供电脑端工具恢复或刷写。 | 进入 |
| 返回主菜单 | 返回系统启动选项。 | 返回 |

原生扫描输出保持 1904×3040、stride 7616，LVGL 使用 3040×1904 的逻辑画布。在 flush 中旋转脏矩形，不改上级 BL 的 framebuffer 描述或 DT，不额外申请全屏缓冲。主机预览先走同一真实帧缓冲路径，再固定将原生图像逆时针旋转 90 度，按设备正常横握方向展示；不随配置选择反变换掩盖方向差异。四角实色块测试检查实际 LVGL flush 到物理像素的位置。真机方向仍需用户核对。

`menu_text.h` 自行先加载生成的 `config.h`，避免调用方头文件顺序把本应为中文的标题和按钮提前固化为英文。主机中文测试读取真实机型 Kconfig，另有英文配置回归。

## 高级模式的实现与验证边界

Recovery/Fastboot 使用硬件 DT 指向的 SDAM restart reason；9008 使用 DT 指向的 SCM download cookie 和已查询支持的暖复位入口。正常重启原因分别为 1/2；本机 cell 位域为 `<1 7>`，保留 bit 0。9008 的 EDL 值为 1，不是崩溃转储值 0x10。

只在用户确认相应菜单动作时访问这些重启控制字段。调用返回视为失败；回滚原字段并保留菜单。能力不支持、DT 不匹配或操作失败不能冒充已经进入目标模式。完整协议来源和固件限制见本次交付目录 `qcom-reboot-evidence.md`。

## 菜单前延迟的诊断

用户报告联想标志约停留五分钟。当前未确认根因，也未宣称此延迟已修复。

开启 `CONFIG_KSHIM_BOOT_TIMING` 时，向成功准备的自定义 Linux DT `/chosen` 写入：

- `kshim,boot-timing-version`：大端 u32，当前为 1。
- `kshim,boot-timing-ms`：8 个大端 u64，依次为 C 入口、资源解析结束、MMU 初始化阶段结束、字体准备结束、UI 初始化结束、菜单内容设置结束、首帧 flush 完成、菜单选择结束。
- `kshim,boot-entry-counter-ms`：C 入口时的原始计数器毫秒值；原点未验证，不能当作从上电经过的时间。

阶段值相对 C 入口，缺失为 UINT64_MAX。最后一项包含用户在菜单等待的时间。C 入口以前的固件加载/解压、GOT/BSS/栈清零和重定位不在相对计时内；这组数据首先用于判断五分钟是否消耗在 kShimC 的 C 初始化路径。只导出诊断属性，失败不阻止启动，不改 Android 原厂 DT。

## 离线验证

相关目标：`menu_text_chinese_test`、`menu_text_english_test`、`framebuffer_rotation_test`、`tb323fu_menu_test`、`platform_advanced_menu_test`、`qcom_reboot_test`、`boot_metrics_test`。

真实原厂 DT 可通过 `qcom_reboot_test --hardware-dtb merged-stock.dtb` 只读解析，不执行任何重启或写操作。

主机回归、真实 512 KiB LVGL 堆、ARM64 构建、QEMU manifest 交接与完整 boot 字节/AVB 检查均在远程 Docker 执行。通过这些检查不等于 Recovery、Fastboot、9008 或横屏菜单已经通过真机验收。具体证据由本次交付 `RESULT.md` 和日志记录。
