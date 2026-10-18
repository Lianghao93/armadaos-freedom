# TB322FC Android 引导打包（Android-only kShim）

本目录只包含 TB322FC 的 Android-only 打包驱动与说明。它把 4 MiB 的 Manifest
runtime 作为 Attached Shim，与一张原厂 Android 镜像组合成单基座（base-only）布局，
并用现有 v4 内核替换器与官方 avbtool 生成离线校验产物。

范围仅限“原厂 Android + 自动附加 Shim”这一条路径：**不包含** Linux、
自定义 DTB 或 initrd 分支，也不做 gzip，全程输入/内嵌内核均为未压缩的
raw ARM64 Image。本目录的驱动不打包 Linux；已安装系统中的共享 Linux 打包器是
`system_files/usr/libexec/armada/kshim-build-boot.py`，两者互不替代。

## 构建 4 MiB runtime

runtime 由 kShim 源码树构建（`K = devices/lenovo-tb323fu/boot/kshim-success-source`），
使用独立的 TB322FC profile（继承 TB323FU UI，但按下方列表裁剪菜单项）：

```sh
# 在 K 源码树根目录
python3 tools/build_runtime.py --config configs/tb322fc_android_defconfig --out <runtime-out>
# 产物 <runtime-out>/kShimC.bin，必须恰好 4 MiB
```

该 profile 保留：QCOM_KEYS、QCOM_SPMI、debounce2、FRAMEBUFFER/LVGL、
UI 旋转 270、LVGL_BUFFER_BYTES=243712、
入场动画 0、STAR_CHART 主题、语言切换/中文/条目详情/Android 基座，
以及 KSHIM_MENU_POWER_ACTIONS、KSHIM_MENU_ADVANCED_ACTIONS、
KSHIM_MENU_SESSION_SETTINGS、KSHIM_QCOM_REBOOT。显式关闭：
KSHIM_QEMU_PLATFORM、KSHIM_TOUCH、KSHIM_SMP、KSHIM_UEFI_TEST_MENU、banner、
KSHIM_DEBUG_CONSOLE 与 KSHIM_BOOT_TIMING。设备信息为共享实现，无需新增选项。

## 打包 CLI

runtime 构建、测试与打包在 Linux Docker 构建环境中执行；脚本本身不访问设备、不联网。
打包使用本目录的独立驱动（默认复用同级 TB323FU 的
`star-chart-build/repack_boot_v4.py`，也可用 `--repacker` 覆盖）：

```sh
python3 <repo>/devices/lenovo-tb322fc/boot/build_android_boot.py \
  --stock-boot <96MiB 原厂 boot.img> \
  --runtime   <4MiB kShimC.bin> \
  --packer    <MultiBootKernelPatcher> \
  --unpacker  <UnpackTool> \
  --avbtool   <avbtool 路径> \
  [--repacker <repack_boot_v4.py 路径>] \
  --out       <输出目录> \
  [--source-label <记录用标签>]
```

`--source-label` 仅作记录用途。

## 输入约束

- `--stock-boot` 必须是 96 MiB（100663296 字节）、魔数 `ANDROID!`、header
  版本 4、header_size 1584、ramdisk_size 0、signature_size 0，且带 AVB footer。
  内核直接从 boot header 的 kernel_size 处截取，无需单独的 `--stock-image`。
  应使用与本机 Android 版本匹配的自有原厂 boot 备份。
- 截取出的内核必须是 raw ARM64 Image：`ARM\x64` 魔数在 0x38、image_size>=64、
  非 `UNCOMPRESSED_IMG`、且不是 gzip。
- `--runtime` 必须恰好 4 MiB。

## 菜单与校验边界

菜单为 Android 基座（默认项）+ 设备信息 + 语言切换/中文 + 按键操作；电源、
高级与会话设置项已启用，调试串口、触屏与 SMP 已关闭。

离线检查覆盖 Manifest ABI 校验、字节级往返与 avbtool 校验。校验用的 AVB
元数据是**未签名**的，需要解锁 bootloader，无法保留原厂 Lenovo 签名。
