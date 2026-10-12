# STAR_CHART 构建配方与输入边界

这里保存 2026-10-12 TB323FU STAR 菜单实际构建所用的脚本与菜单字形清单。配套源码位于 [`../kshim-success-source/`](../kshim-success-source/)，准确结果及实机验收范围见 [本轮状态](../../docs/status-20261012-kshim-star.md)。本次仓库同步复用既有构建结果，没有重新编译。

## 文件与用途

| 文件 | 用途 |
|---|---|
| `build.sh` | 原执行环境的主机测试、STAR 预览、512 KiB 菜单测试、ARM64 runtime、QEMU 与 boot 打包流程 |
| `build_offline_boot.py` | 使用独立输入组装并核对八条目 manifest 和 boot 候选 |
| `repack_boot_v4.py` | 保持原有 boot v4 包装结构的重打包工具 |
| `menu-required-text.txt` | 本轮中英文菜单必需字形清单，用于字体子集生成和覆盖核对 |

这些脚本保留实际执行路径 `/work` 及输入文件名，便于追溯；它们不是只需全新克隆后执行一个命令就能完成的自动发行入口。脚本中的构建、字体生成及其他重 CPU 步骤应在指定构建服务器 Docker 中执行，不在本地电脑编译。

## 源码快照

本轮原始构建输入 `source.tar` 的 SHA-256 为：

`3f8dc6d08e3945c874d8f1ab0789917db9781f62f77db98fecec547c6b9b1c5e`

仓库保存该归档的非依赖源码内容，`.config` 使用实际构建输出的 `runtime.config`；入库文本统一 LF。原归档哈希用于核对历史构建输入，不表示从 Git 再归档会得到相同字节。实际构建脚本也会规范化 `ports/crdk/patches/*.patch` 的行尾。[本轮菜单变更统计](../../docs/evidence/kshim-star-20261012/source-semantic-stat.txt)记录相对此前已验收菜单基线的变化，不是归档与入库文件的字节核验报告。

源码归档排除顶层 `.git/`、`lib/` 和 `third_party/`。不要因此删除 `src/` 内同名的源码子目录。旧 `patches/boot/` 用于历史追溯，不能将旧 kShimC 提交再次无条件应用到当前完整快照。

## 需要单独准备的依赖

| 依赖 | 本轮固定版本 |
|---|---|
| CrDK | `b6c3335136b7f6b28271917de11e8f11982975fc` |
| picolibc | `f7edb0869e64b9d687f46961adeef7154921fe92` |
| LVGL | `7f07a129e8d77f4984fff8e623fd5be18ff42e74` |

版本来自实际 gitlink；`.gitmodules` 只提供 URL。本轮使用既有依赖归档恢复这些目录，CrDK 还应用源码内 `ports/crdk/` 补丁层。`lib/libfdt` 和 `libfdt_env.h` 是另行恢复的构建输入，未被 `.gitmodules` 完整描述；本同步没有补完其自动获取和公开复现链。不能仅初始化声明的子模块就认定依赖已齐全。

打包还依赖当前工程的 MultiBootKernelPatcher/UnpackTool，保留的来源与本地变更见 `../../patches/boot/` 及 [固定来源记录](../../docs/SOURCES-AND-STATUS.md)。不要用未核对的上游最新打包器替换本轮工具。

## 执行目录与 payload 输入

原脚本按下列布局工作：

```text
/work/
  source.tar
  source/                 # 解包源码，并恢复固定依赖
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

`Image.linux-165` 是构建脚本沿用的文件名；本轮实际 payload 是已验证的 `7.3.0-rc4-tb323fu-t42 #5`，不应仅按文件名猜测内核版本。其 SHA-256 为 `bfcc03543585aa9f7015917eebcb20fade8c1ef849080e459f653f8bd1c3a38d`。其余输入和 manifest 内容按 [boot-report.json](../../docs/evidence/kshim-star-20261012/boot-report.json)核对。

Android/设备 payload、原厂 boot、现场分区备份及生成的 96 MiB boot 和 4 MiB runtime 不随本目录分发。这里也不收录设备连接、上传或刷写脚本。构建成功不授权设备写入或重启。

## 字体维护

最终 WenKai 资产为 `src/ui/fonts/lxgw_wenkai_menu.ksf`，86,077 字节、686 个字形。来源是 LXGW WenKai Regular v1.522，固定提交 `e8b5b48b79f19f29aa68b0a178eab3472ea9f7e8`；同目录保留来源 JSON 和 OFL 授权文本。

使用源码中的 `tools/gen_ui_font.py` 菜单覆盖模式及这里的 `menu-required-text.txt` 维护字形子集。本轮清单覆盖 233 个非空白必需字符，无缺字。新增翻译、菜单说明或 manifest 标签时，需要更新清单并重新做覆盖与预览验证，不能把该资产当作完整中文字库。

最终资产 SHA-256：`c27192c3e32d8744ced14adfc606ddd5557241f22c4f6a01ec0df24aefcc5d60`。必需文案清单 SHA-256：`130653f630498ef0bb0057248d7e61fbcc6ba3c90cb10999c2c1ad1dc32c0692`。若仓库文本规范化改变清单原始字节，应以字形集合及来源记录核对，而不是错误地认定字体数据被修改。

## 验证与交付边界

最终 runtime SHA-256 为 `8f49609199586e27fccf07e5feff966e95e733ba05987b0c35f607153092c13e`，固定长度 4,194,304 字节；已用 3,370,624 字节，保留原有内存预算。最终 boot SHA-256 为 `3e9ca47df76c123a42f2a72b6769750971fb37639fa8e14ed78bac9f526310d1`，设备完整回读一致。

已取得的验证为主机首轮 44/45 加修正后定点 1/1，STAR 3/3 加确认动画修正后定点 1/1，512 KiB 菜单 1/1、QEMU 9/9 及镜像检查 262/262；不是最终源码完整重跑所有主机测试。用户确认整体菜单无问题，后台确认 #5 机身 Linux/Steam；高级模式和语言尚未逐项单独验收。

本目录不提供 OTA 集成，也不宣称 Steam 主界面高刷问题已经解决。固定依赖、构建环境与设备 payload 的完整自动取得方式仍需后续单独整理，之后才能形成正式设备镜像构建入口。
