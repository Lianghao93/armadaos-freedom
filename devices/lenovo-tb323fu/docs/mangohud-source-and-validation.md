# TB323FU MangoHud 数据接入

目标是现有 Steam 四档叠加层的缺数值，不新增设置页或改预设。现场完整截图 hud-before.png 实际显示 CPU0℃、RAM0℃、GPU40℃，没有BATT。系统电池79%、CPU/GPU/DDR真实传感器已可由普通armada读取。

基线是MangoHud官方v0.8.4（tag992103e4fb744897826de04ea00a2f71e7018214）加Armada预览816091ec的8个补丁；保留FEX/MangoApp和厂商GPU、电池功耗/续航逻辑。原Source0归档SHA0f79671dbc825f33d7014088cb7750c166934ade26bbd6007a171aa2d960cd2f。最终源归档artifacts/mangohud-r1-final-source.tar SHAba8d0d459b22a38cf275120a3286bcce95c65cb786b43581380651578b9086c7。

CodeBuddy新增范围：
- battery.cpp：按power_supply/type=Battery识别，数量在写数组前限制为2；真实qcom-battmgr-bat替代厂商硬编码battery。SHA4ae4bcd1ae2d7e74e561748da5d75f3d566a80d7d196764628596f442674aeb2。
- cpu.cpp：保留旧匹配，再接受cpu_[0-9]+_[0-9]+_[0-9]+_thermal，读首个匹配测点，不是8核心最高温度。SHA8e7c9644623f0222d0d2900e1837d1a70383f4c4e1c591fcf2d0c2aa89af5f32。
- memory.cpp：保留spd5118，再接受ddr_thermal，保留有效测点取最大与m°C除1000。SHAe8f4df70298dff114cd844d01b77d9fcb97485a944792a1c352738bde77c8526。

新增差异分别反向还原后与vendor-baseline逐字节一致。GPU温度已由现有采集器实际读gpuss_5_thermal，没有新改GPU。当前没有CPU/GPU真实功耗接口；不能把0W或VRAM温度0℃解释为真实测量。充电/80%上限时电量仍应显示，电池功率0或剩余时间隐藏遵循原实现。

代码均由CodeBuddy写出。首次8patch+2处新增的CLI实际退出0但final为Max turns exceeded，因此据实际文件/diff/SHA核验，不宣称CLI成功；单独RAM小任务实际final success/exit0。未重复CLI或本地编译。

构建仅在192.168.88.9临时ARM64/Fedora44 Docker。Meson release、MangoApp/FEX/Wayland与system spdlog启用；文档与mangoplot未重建。组件版本spdlog1.15.3/fmt11.2/glfw3.4/libstdc++16.2.1与目标一致，目标glibc2.43-9高于构建2.43-8。仍以目标机器ldd及实际启动判断ABI，不把版本列表当作ABI通过。

原设备运行文件小备份artifacts/mangohud-r1-original-runtime.tar 8.9MB，SHA d7b946dfaceffdf55059383aa8368032fb3731f470cf390a76b67ec9a8accad7。没有备份整个Linux分区。构建/ABI/安装/重启与用户复测状态继续追加真实结果；当前仅源码审查通过和旧画面证实，不宣称部署完成。
## 2026-10-09 03:31 香港时间：已安装及正常重启实测

最终Docker构建退出0（含memory.cpp重新编译），stage退出0、ELF64/AArch64。目标平板mangoapp和三个.so的ldd无缺依赖；NAS→平板归档SHA ef2a1e89aed64b60c7b9ba298c29a293f6d8afd9fa79f91cc41a7bfd2fbf637e一致。四个文件原子替换，实际旧进程46207终止，新进程76397正常启动；CPU匹配hwmon16/cpu_0_4_0_thermal、GPU仍hwmon44、RAM新增hwmon50。

重载后第一张截图没有叠加层，不能冒称通过；用已有mangohudctl显示控制后完整截图hud-after-visible.png实际BATT79%、CPU38℃、RAM39℃、GPU39℃。03:22:12正常重启后BootID69376539-2318-4954-87bb-90e8f49bf2b0、root/dev/sda15，Steam自己恢复preset4，无再次显示控制；hud-after-reboot.png实际BATT80%、CPU40℃、RAM40℃、GPU41℃。已完成设备侧自测，用户复测请求已发送，尚未收到新答复；没有把截图自测当作用户验收。

mangoapp SHA ba1c331e66aa152e25709ade5c63b7a0a71ca741a8779f2f0bdf4aafd262bb4c
libMangoHud.so SHA a3eee1013b7296392b120ba0c269f0a1b2975ecdf489034012a7a3a7d72200fa
libMangoHud_opengl.so SHA 111f5df2002b495ac5f84f96a93267bf40449afb979dedbc92567f14423d4427
libMangoHud_shim.so SHA 561c28a33e4271da42acaf83440f692e26c9372d4a242eec3eb84d40f91ed0bc
这些SHA在正常重启后再次一致。RPM版本仍标记0.8.4-1.fc44.armada，本次是设备运行文件适配，不冒称已经打成官方RPM或已进入镜像；未来系统升级可能覆盖，源码/差异/原运行文件已留NAS。

build-evidence.tar SHA df05aeb30d67506458748336b8c14f142629e4c27171324fdb3acde5523b5876，包含setup/最终build/stage和Meson信息。构建获取的依赖源码另存fetched-subprojects.tar SHA ef000e7bc315f1d483307142f52504dbd4d34207ad60ab4703492fe9e444761d。原始源码与最终源都在NAS。临时ARM容器已停止删除，服务器本轮140MB执行复制已精确删除，既有镜像未删。

本轮没有测FEX/x86游戏注入库的真实游戏、拔电后的电池放电功率/续航或长期高负载温度。单独CPU/GPU功耗及VRAM温度仍没有真实接口，此次未补造数据。TF卡未插，修复仅装到机身root；TF自身模块/HUD未同步。未发布/推送/创建PR。
