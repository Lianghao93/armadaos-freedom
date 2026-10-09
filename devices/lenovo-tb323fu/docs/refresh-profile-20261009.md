# TB323FU 刷新率型号配置验证

2026-10-09，设备 TB323FU / SM8850P，内核仍为7.3.0-rc4-tb323fu-t42。

## 修改

保留既有 device_id / internal / connector 的型号匹配机制。空 EDID 不再提前结束 ParseEDID；各 libdisplay-info 调用守卫空指针。只提供 dynamic_refresh_rates 的配置也能生效，modegen仍只在同时具备rates和生成器时启用。

Y700专用Lua只匹配lenovo-tb323fu内屏DSI-1，列出30/60/90/120/164。使用内核现有时序，不生成新时序，不声明HDR或颜色配置。未命中配置时保留真实模式收集作为最后回退。

## 自测和现场证据

- 指定构建服务器Docker完成生产RPM构建，版本3.16.31^1-1.100902.fc44.armada。
- 打包工具清理BUILD后首个测试驱动未找到二进制；独立Docker以最终四文件逐字相同源补跑，mode_list的8个用例/32项断言、display lookup的11项断言、实际Y700 Lua校验均通过。测试为debug无LTO，生产RPM按原spec构建。
- RPM安装预检、安装和rpm -V通过。实际运行的Gamescope SHA256为4933664d0fc1a9d649fd21192ec7b93c947abe8384ca7b2a4a4f5c8fd060c039；Lua SHA256为0e2a108eb1a6b965035f683abc551386bdc07c40c338f44603b0b261332635dd。
- scriptmgr加载专用Lua，DRM明确记录Got known display: armada_lenovo_tb323fu_lcd。
- 现代Steam GamescopeService收到supported_refresh_rates=[30,60,90,120,164]。用户确认右侧五档全部可见。
- 内核active CRTC确认1904×3040 @164，clock1148209kHz，htotal2244，vtotal3120。启动日志记录120/164切换。只重载游戏会话，boot_id不变；电池Charging。

## 尚未验收

30/60/90/120逐档物理切换、每档实际游戏出帧与长时164Hz稳定性。五档能力上报和侧栏显示不能代替这些验收。主界面颜色合成耗时、帧时间波动、HUD/QAM冻结及崩溃仍在排查。

用户未授权新的启动镜像重启；本轮未触碰boot、CPU/GPU频率或温控。

## 构建接入

packages/gamescope/build.sh按现有序列收集patches/*.patch，因此0028随原流程应用。Lua在system_files/usr/share/gamescope/scripts/10-armada中按原overlay入口安装。尚未构建或验收完整Y700系统镜像；内核及OTA路线仍单独待办。
