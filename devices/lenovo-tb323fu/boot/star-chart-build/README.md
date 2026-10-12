# STAR_CHART 构建配方

配套源码位于 [`../kshim-success-source/`](../kshim-success-source/)，固定来源见 [SOURCES.md](../../docs/SOURCES.md)。此目录提供构建脚本和输入约定，尚不是全新克隆后即可一键生成设备镜像的工程。

## 文件

| 文件 | 用途 |
|---|---|
| `build.sh` | 主机测试、主题预览、ARM64 runtime、QEMU 与 boot 打包流程 |
| `build_offline_boot.py` | 使用独立输入组装并校验八条目 manifest 和 boot |
| `repack_boot_v4.py` | 保持 boot v4 包装结构的重打包工具 |
| `menu-required-text.txt` | 中英文菜单字形清单 |

## 依赖

恢复固定版本的 CrDK、picolibc 和 LVGL，并应用源码内 `ports/crdk/` 补丁层。`lib/libfdt` 和 `libfdt_env.h` 需单独准备；仅初始化 `.gitmodules` 中的子模块并不足够。打包需要配套 MultiBootKernelPatcher/UnpackTool 和 `patches/boot/` 的本地变更，具体版本见来源文档。

源码快照不包含顶层 `.git/`、`lib/` 和 `third_party/`。这不包括 `src/` 内同名的源码子目录，不能一并删除。

## 容器工作目录

```text
/work/
  source.tar
  source/                 # 源码和已恢复的固定依赖
  build.sh
  build_offline_boot.py
  repack_boot_v4.py
  inputs/
    boot-stock.img
    Image.stock
    Image.linux-165
    tf.dtb
    internal.dtb
    avbtool.py
  out/
  qemu-manifest/
  boot-candidate/
```

`Image.linux-165` 是脚本输入名，不能据此推断实际内核版本。Android payload、原厂 boot 和设备 DTB 必须由构建者从自己的设备准备，并核对 manifest 中的 payload、DT 索引及大小。

构建、字体生成及验证在 Linux Docker 环境执行。runtime 使用固定 4 MiB 槽位；生成 boot 的大小还受目标分区限制。保留镜像结构检查及回读验证流程，不将成功编译视为真机稳定性保证。

## 字体

菜单使用 `src/ui/fonts/lxgw_wenkai_menu.ksf`，来源及 OFL 许可位于资产同目录。使用 `tools/gen_ui_font.py` 的菜单覆盖模式和 `menu-required-text.txt` 维护子集；新增菜单、翻译或 manifest 标签后，更新清单并验证字形及预览。菜单子集不是完整中文字库。

本目录不提供 OTA 集成。固定依赖和设备 payload 的自动准备、完整镜像构建入口仍待完善。测试输出和部署记录在仓库外保存。
