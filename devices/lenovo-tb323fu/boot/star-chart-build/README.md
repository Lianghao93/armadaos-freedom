# STAR_CHART 构建配方

配套源码位于 [`../kshim-success-source/`](../kshim-success-source/)，固定来源见 [SOURCES.md](../../docs/SOURCES.md)。此目录提供构建脚本和输入约定，尚不是全新克隆后即可一键生成设备镜像的工程。

## 文件

| 文件 | 用途 |
|---|---|
| `build.sh` | 主机测试、主题预览、ARM64 runtime、QEMU 与 boot 打包流程 |
| `build_offline_boot.py` | 使用独立输入组装并校验八条目 manifest 和 boot；可选外置 initramfs 九条目布局 |
| `build_external_initramfs.py` | 由此前已验收的内置 initramfs 模板和统一内核模块目录重建外置 initramfs |
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

## 外置 initramfs

`build_external_initramfs.py` 在 Linux Docker 中以此前已验收的内置 initramfs 为模板，离线重建五代统一内核使用的外置 initramfs。模板必须是 gzip 压缩的 newc cpio；脚本先校验全部成员路径（拒绝绝对路径、空组件和 `..`），再交给 `cpio` 解包。模板可能含设备节点，因此解包步骤需要 root。完整说明见 [EXTERNAL_INITRAMFS.md](EXTERNAL_INITRAMFS.md)。

```text
python3 -B build_external_initramfs.py \
  --template-initrd inputs/initramfs-template.cpio.gz \
  --module-dir inputs/staging/lib/modules/7.2.6-... \
  --kernel-release 7.2.6-... \
  --out initramfs-candidate
```

- `--module-dir` 必须正好是 `<staging>/lib/modules/<KVER>`，且目录名与 `--kernel-release` 完全一致；`--out` 必须为空，脚本不会清空已有目录或修改输入。
- 依赖容器内真实的 `gzip`、`cpio`、`depmod` 和带 ZSTD 的 `mksquashfs`；缺失时明确失败，不使用替身工具。
- 除 `/lib/modules` 外，模板中的 `init`、`armada-root-resolve`、辅助程序、固件和字体原样保留；打包前后核对哈希、权限、符号链接目标和设备号。`init` 与根解析策略由既有模板决定，本脚本不修改。
- `/lib/modules` 整体替换为 ZSTD squashfs 的 `<KVER>.sqfs`。模块暂存排除顶层 `vmlinuz`、`dtb`、`build`、`source`，保留其余 `.ko` 和模块索引，再在正确的 `lib/modules/<KVER>` 布局中运行 `depmod`。
- 旧 `init` 只解析未压缩 `.ko`：发现 `.ko.xz`、`.ko.zst` 或 `.ko.gz` 会直接失败，不改变现有模块加载策略。`qcom_q6v5_pas.ko` 和 `nt36536_ts.ko` 必须存在。
- 输出为 root 所有、newc 格式、`gzip -n` 压缩的 `initramfs.cpio.gz`；成员按名称排序并将时间戳归零。`artifact.json` 只记录 KVER、哈希、计数和 `hardware_validated: false`。

initramfs 内的模块镜像只用于早期启动。与当前完整 KVER 匹配的模块仍必须单独安装到所选内部或 TF 卡根文件系统，本脚本不安装、也不修改该根目录。未提供 TF 根目录时，TF 侧模块和启动均未验证。

## 离线 boot 的外置 initramfs（可选）

默认命令不变，仍生成八条目 manifest（条目 0..6 加条目 7 `Attached Shim`），默认 `Image-01`、超时 8000 ms。追加 `--initrd <path>` 后切换为九条目布局：条目 0..6 保持原索引，条目 7 为三份 Linux 菜单共享的字节精确 blob（`Type=Blob`、flags 0、`Align=16`，即 64 KiB 对齐），`Attached Shim` 顺延到条目 8。64 KiB 对齐使 initrd 按页取整后的保护区间不与其前面的已选 DTB 重叠：对支持的 4/16/64 KiB 页大小均成立，页大小未知时也取保守值。打包配置为 `[Image-07] ... Type=Blob` 后接 `[Manifest]`，shim 仍由 `[Pack] Shim=` 自动追加，不复制 Linux payload。

外置模式会在三份派生 DTB（internal、TF、diagnostic）的 `/chosen` 写入 `kshim,initrd-entry = <7>`，并删除其中的 `linux,initrd-start` / `linux,initrd-end`；输入 DTB 本身不被修改。脚本要求传入的 internal/TF DTB 已含 `/chosen` 与 `bootargs`，根 `compatible` 同时含 `lenovo,baldur`、`qcom,kaanapali`，且 `bootargs` 含 `baldur.end=hold` 与 `cpuidle_psci_domain.allow_cluster_off=0`。校验还比对源/派生 DTS：除上述 initrd 属性外不得有任何差异，`armada,boot-stage` 仍为 internal/TF 的 `os`、diagnostic 的 `maintenance`。

`--initrd` 文件同时参与输入哈希、blob 字节精确比对、布局不重叠、UnpackTool 回读和重打包逐字节一致性检查；报告记录条目索引与 shim 索引。九条目外置 initrd 路径已在 TB323FU 的机身系统上验证：统一 Linux 7.2.6 #5 启动到 Steam，原生 DRM／Adreno 840 和 165 Hz 显示已运行，用户确认实体画面恢复。最终镜像的 307 项离线检查、15 项生产 initrd 布局回放及四项真实 Linux QEMU 检查均通过。离线检查本身不代表真机验收；TF 启动、触屏与游戏体验、休眠及长期显示稳定性仍待进一步验证，公共安装／OTA 流程也尚未接通。实际打包仍需在 Linux Docker 中执行。

## 默认启动条目（可选）

`build_offline_boot.py` 的 `--default-entry` 只决定 manifest 的默认条目，取值为 `android`、`internal`、`tf`、`diagnostic`，分别对应固定布局的条目 0..3（`Image-00`..`Image-03`）。不写该选项时默认仍为 `internal`（`Image-01`），命令行、八/九条目布局、8000 ms 超时及其余校验均不变。

`--default-entry diagnostic` 让 `Image-03` 成为 manifest 默认条目：它沿用已有的 `maintenance` 启动阶段并停留在 initramfs 内，不安装常规根文件系统模块，也不校验正常 OS 启动，仅用于采集日志。该选项只写 manifest 默认值，不保存用户偏好，也不改变菜单顺序、payload 或 DTB 内容。

## 字体

菜单使用 `src/ui/fonts/lxgw_wenkai_menu.ksf`，来源及 OFL 许可位于资产同目录。使用 `tools/gen_ui_font.py` 的菜单覆盖模式和 `menu-required-text.txt` 维护子集；新增菜单、翻译或 manifest 标签后，更新清单并验证字形及预览。菜单子集不是完整中文字库。

本目录不提供 OTA 集成。固定依赖和设备 payload 的自动准备、完整镜像构建入口仍待完善。测试输出和部署记录在仓库外保存。
