# Y700 五代 TB323FU 当前温控与充电限制报告

核对日期：2026-10-09。设备：SM8850P，ArmadaOS 20261007.816091e，Linux 7.3.0-rc4-tb323fu-t42。
温度与冷却状态快照：香港时间 06:42:38–06:42:40。后面的温度是这个时间窗口的读数，不是游戏中的历史最高温。
本报告未修改任何温控值、充电百分比或分区。

## 1. 结论与已确认缺口

当前可见 **78 个温区、4 个 cooling device、42 条温区到频率控制的绑定**。77 个温区来自设备树，另一个为充电驱动导出的电池温度。现有策略混合了主线模板、Qualcomm 开发补丁及作者板级移植，**并非完整照搬 Android thermal-engine**。

- quiet 的 CPU/GPU 分档明确参考 Android 默认 user case 0；GPU 539 MHz 已改成主线可用的 500 MHz。
- CPU 95°C 被动降频是作者板级增加；GPU 105°C 被动降频来自 Qualcomm 主线开发补丁。
- SoC 的 120/125°C 和 PMIC 的保护点来自既有主线模板。
- 作者明确没有移植 Android 的 LCD 背光限幅与充电电流分档。当前设备树的 42 条频率绑定全部控制 CPU/GPU。
- 充电固件内部完整的 JEITA、过温停充和降流温度表没有通过当前 Linux 接口导出，不能给出未经证实的温度。
- Linux 能读写充电起止百分比，但用户确认当前没有可用的设置界面。这是已记录的入口缺口。

当前静态快照 CPU/GPU cooling state 都为 0，quiet 37.178°C，尚未触发 quiet 的 43°C 分档。它不能证明游戏负载下各档的实际频率和恢复行为；此前 GPU fault 与自动重启也尚未证明由温控导致。

## 2. 全部温区的门槛分类

单位均为 °C。passive 表示被动降频触发点；hot 表示 HOT 通知门槛，不能一律解释为关机；critical 是 Linux 热框架的临界保护点。下面“没有频率绑定”指未绑定 CPU/GPU cooling map，不表示芯片或充电固件不存在独立保护。

| 类别 | 数量 / zone ID | passive | hot | critical | 回差 | 当前 Linux 频率绑定 |
|---|---|---|---|---|---|---|
| CPU / LLC | 20：1–2、6–19、25–28 | 95 | 120 | 125 | passive/hot 5；critical 0 | 每区绑定对应 CPU policy |
| GPU | 11：37–47 | 105 | 120 | 125 | passive/hot 5；critical 0 | 每区绑定 GPU |
| 其他 SoC TSENS | 24：3–5、20–24、29–36、48–55 | 无 | 120 | 125 | hot 5；critical 0 | 无 |
| 普通板级 NTC | 14：57–64、66–71 | 无 | 80 | 无 | 2 | 无 |
| quiet 板级 NTC | 1：65 | 43–50 分档 | 80 | 无 | 每档 2 | 11 条 CPU/GPU 绑定 |
| PMH0101 | 1：76 | 95 | 无独立 hot 档 | **115** | 0 | 无 |
| 其他 PMIC | 6：56、72–75、77 | 95 | 115 | 145 | 0 | 无 |
| qcom-battmgr-bat | 1：0 | 未导出 | 未导出 | 未导出 | 未导出 | 无 |

CPU / LLC 的被动轮询为 100 ms，GPU 为 10 ms，PMIC 模板为 100 ms；quiet 的普通及被动轮询都是 1000 ms。当前 sysfs 的温区策略均为 step_wise。无完整 polling 属性的温区不能凭此推断没有驱动中断或硬件保护。

普通 NTC 包括 xo、lcm、skin、flash-led、wlan、usb2-conn、rear-cam、qtm、batt、usb、ufs、batt2、wls、fcam。这些名字不是 PCB 物理位置证明；特别是 quiet 与独立的 skin 通道不能混称。

## 3. quiet 的完整设置

测量来源：PMK8850 ADC 通道 0x14c，经 PMH0101 GPIO3 模拟输入，100 kΩ 上拉、比率式 NTC 测量。skin 使用另一个 0x144 通道。尚无板图证明 quiet 在背壳、屏幕或某颗芯片旁。

当前路径：/sys/class/thermal/thermal_zone65。zone 编号只是本次启动的编号，修改工具应按 type=quiet-thermal 找它。

连续升温时各档累积的上限如下，CPU/GPU 列单位 MHz。频率是固定 DTS 标注的目标，最终上限还会取其他温区与性能档的更严格限制。CPU6 的 cooling 表含两个禁用 boost 档，不应直接拿 available_frequencies 的索引猜 state。

| quiet 触发温度 | CPU 0–5 | CPU 6–7 | GPU | 释放该档温度 | 本档新增 cooling state |
|---:|---:|---:|---:|---:|---|
| 43 | 1996.8 | 2880 | 不新增限制 | 41 | CPU0=13；CPU6=13 |
| 44 | 1996.8 | 2880 | 902 | 42 | GPU=3 |
| 45 | 998.4 | 1747.2 | 902 | 43 | CPU0=22；CPU6=20 |
| 46 | 998.4 | 1747.2 | 646 | 44 | GPU=6 |
| 47 | 384 | 1017.6 | 646 | 45 | CPU0=27；CPU6=26 |
| 48 | 384 | 1017.6 | 500 | 46 | GPU=8 |
| 49 | 384 | 883.2 | 500 | 47 | CPU6=27 |
| 50 | 384 | 883.2 | 222 | 48 | GPU=14 |

- trip_point_0：80°C，hot，回差 2°C。
- trip_point_1–8：依次为 43、44、45、46、47、48、49、50°C，均为 passive，回差均为 2°C。
- 每条 quiet cooling map 都设置 min_state=max_state；同一设备有多个有效限制时取更严格的档。
- 改触发温度、改回差、改降频强度是三种不同改动。CPU/GPU 内部 95/105°C 保护和各 PMIC 临界保护是独立温区。
- 永久设置位于作者 baldur-skin-thermal.dtsi，需随实际设备树更新。当前 sysfs trip 温度属性为 0644，但这次没有写值，也没有用写权限代替实际生效验证。

## 4. 已注册的降频控制设备

| cooling device | 控制对象 | 当前 state | max_state | 解释 |
|---|---|---:|---:|---|
| cooling_device0 | cpufreq-cpu0 / CPU0–5 | 0 | 27 | 28 档；state0 是该 cooling 表最高档 |
| cooling_device1 | cpufreq-cpu6 / CPU6–7 | 0 | 28 | 29 档，含两档禁用 boost |
| cooling_device2 | devfreq-3d00000.gpu | 0 | 16 | 17 档；最高表项 1200 MHz |
| cooling_device3 | ath12k_thermal | 0 | 100 | Wi-Fi 驱动接口；100 是状态范围，不是温度 |

当前 42 条 DTS 绑定没有连接到 ath12k_thermal；其固件内部热管理尚未核实。CPU/GPU 当前 state0 表示本次没有热降频请求，实际频率仍可能低于硬件最高值。

当前 Eco 档另外限制 CPU0–5 至 2112 MHz、CPU6–7 至 2438.4 MHz、GPU 至 646 MHz。这是用户性能策略，不是温度触发点。Balanced 切换实测 CPU 上限 2899.2/3398.4 MHz、GPU 902 MHz，随后已恢复 Eco。

## 5. 电池、充电与 Android 设置

快照：电量80%，Not charging，电池电流0，固件电池温度34.3°C；长边 USB 供电在线，约9.328V，输入电流限制3A。USB_TYPE 与 UCSI 部分字段之间可能不一致，这些单个字段不能代替全部充电状态。

当前：
- charge_control_start_threshold=70：起充百分比。
- charge_control_end_threshold=80：停充百分比。
- 它们是电量百分比，**不是70/80°C，也不是充电温度限制**。

用户已确认在 Android 设置过80%上限。Linux 的 qcom_battmgr 驱动会向同一个充电固件查询 start/end，当前固件返回70/80；它不需要读取 Android 分区。已检查的当前 udev/helper/systemd 路径没有发现作者可选的强制70/80启动规则。因此现象与保留先前 Android 设置一致；这次没有追溯原始写入记录，也未验证彻底断电后的持久行为。

驱动现有接口范围：start 50–95%，end 55–100%，默认差值5%。实际写入通过 PMIC GLINK 指令交给固件，Linux 已有修改能力。当前缺少界面入口，已记录 **CHG-UI-001**。现有驱动 setter 即使固件请求失败也可能返回0，后续界面必须重新读取固件返回值确认，不能只凭写命令成功提示“已设置”。

当前动态电池温区没有 trip；batt-thermal/batt2-thermal 的80°C仅是板级 NTC 的 hot 点，不能当作实际停充温度。未导出的固件 JEITA、过温停充、充电降流阈值均标为未知。

## 6. 与 Android 的已知差异和记录问题

| 编号 | 项目 | 当前证据 / 状态 |
|---|---|---|
| THERM-001 | quiet 与 Android 默认 case0 | CPU/GPU分档有来源；539MHz→500MHz，改成主线 step_wise 及1秒轮询 |
| THERM-002 | LCD 亮度温控限幅 | 作者明确未移植；当前没有对应 quiet cooling map |
| THERM-003 | Android 充电电流分档 | 作者明确未移植；固件独立保护仍可能存在，完整表未知 |
| THERM-004 | CPU/GPU 内部保护 | 主线模板/Qualcomm补丁/作者新增混合；不能称全部复制 Android |
| CHG-UI-001 | Linux 充电上限设置入口 | 用户确认缺失；内核读写接口存在，当前保留70/80 |
| THERM-005 | quiet 物理位置和温度对应 | ADC线路已知；实物位置、板温与外壳温度关系未知 |
| STAB-001 | 先前 GPU fault / 自行重启 | GPU fault/WebHelper crash已证实；与温控的因果尚未证明，持续记录已开启 |

本报告没有补回 Android 整套 thermal-engine，也不把已有传感器注册等同于所有动态策略已验收。

## 7. 全78个温区明细

以下为快照读数。P=passive、H=hot、C=critical；Δ为该触发点回差。单位°C；“绑定数”是本温区指向 cooling device 的实例数量。详细原值是毫摄氏度；power_supply TEMP 另为十分之一摄氏度。

| zone ID | type | 当前°C | 触发点 / 回差°C | 绑定数 |
|---:|---|---:|---|---:|
| 0 | qcom-battmgr-bat | 34.300 | 未导出 | 0 |
| 1 | cpullc-0-0-thermal | 41.500 | H120 / Δ5；C125 / Δ0；P95 / Δ5 | 1 |
| 2 | cpullc-0-1-thermal | 42.200 | H120 / Δ5；C125 / Δ0；P95 / Δ5 | 1 |
| 3 | qmx-0-0-thermal | 41.800 | H120 / Δ5；C125 / Δ0 | 0 |
| 4 | qmx-0-1-thermal | 41.800 | H120 / Δ5；C125 / Δ0 | 0 |
| 5 | qmx-0-2-thermal | 42.600 | H120 / Δ5；C125 / Δ0 | 0 |
| 6 | cpu-0-0-0-thermal | 42.000 | H120 / Δ5；C125 / Δ0；P95 / Δ5 | 1 |
| 7 | cpu-0-0-1-thermal | 42.400 | H120 / Δ5；C125 / Δ0；P95 / Δ5 | 1 |
| 8 | cpu-0-1-0-thermal | 41.600 | H120 / Δ5；C125 / Δ0；P95 / Δ5 | 1 |
| 9 | cpu-0-1-1-thermal | 42.400 | H120 / Δ5；C125 / Δ0；P95 / Δ5 | 1 |
| 10 | cpu-0-2-0-thermal | 41.600 | H120 / Δ5；C125 / Δ0；P95 / Δ5 | 1 |
| 11 | cpu-0-2-1-thermal | 42.400 | H120 / Δ5；C125 / Δ0；P95 / Δ5 | 1 |
| 12 | cpu-0-3-0-thermal | 41.600 | H120 / Δ5；C125 / Δ0；P95 / Δ5 | 1 |
| 13 | cpu-0-3-1-thermal | 42.400 | H120 / Δ5；C125 / Δ0；P95 / Δ5 | 1 |
| 14 | cpu-0-4-0-thermal | 42.000 | H120 / Δ5；C125 / Δ0；P95 / Δ5 | 1 |
| 15 | cpu-0-4-1-thermal | 42.400 | H120 / Δ5；C125 / Δ0；P95 / Δ5 | 1 |
| 16 | cpu-0-5-0-thermal | 41.600 | H120 / Δ5；C125 / Δ0；P95 / Δ5 | 1 |
| 17 | cpu-0-5-1-thermal | 41.300 | H120 / Δ5；C125 / Δ0；P95 / Δ5 | 1 |
| 18 | cpullc-1-0-thermal | 43.600 | H120 / Δ5；C125 / Δ0；P95 / Δ5 | 1 |
| 19 | cpullc-1-1-thermal | 43.200 | H120 / Δ5；C125 / Δ0；P95 / Δ5 | 1 |
| 20 | qmx-1-0-thermal | 44.000 | H120 / Δ5；C125 / Δ0 | 0 |
| 21 | qmx-1-1-thermal | 43.200 | H120 / Δ5；C125 / Δ0 | 0 |
| 22 | qmx-1-2-thermal | 43.200 | H120 / Δ5；C125 / Δ0 | 0 |
| 23 | qmx-1-3-thermal | 43.600 | H120 / Δ5；C125 / Δ0 | 0 |
| 24 | qmx-1-4-thermal | 43.200 | H120 / Δ5；C125 / Δ0 | 0 |
| 25 | cpu-1-0-0-thermal | 43.000 | H120 / Δ5；C125 / Δ0；P95 / Δ5 | 1 |
| 26 | cpu-1-0-1-thermal | 44.200 | H120 / Δ5；C125 / Δ0；P95 / Δ5 | 1 |
| 27 | cpu-1-1-0-thermal | 42.200 | H120 / Δ5；C125 / Δ0；P95 / Δ5 | 1 |
| 28 | cpu-1-1-1-thermal | 42.200 | H120 / Δ5；C125 / Δ0；P95 / Δ5 | 1 |
| 29 | nsphvx-0-thermal | 40.300 | H120 / Δ5；C125 / Δ0 | 0 |
| 30 | nsphvx-1-thermal | 40.700 | H120 / Δ5；C125 / Δ0 | 0 |
| 31 | nsphvx-2-thermal | 41.100 | H120 / Δ5；C125 / Δ0 | 0 |
| 32 | nsphvx-3-thermal | 40.700 | H120 / Δ5；C125 / Δ0 | 0 |
| 33 | nsphmx-0-thermal | 40.700 | H120 / Δ5；C125 / Δ0 | 0 |
| 34 | nsphmx-1-thermal | 40.700 | H120 / Δ5；C125 / Δ0 | 0 |
| 35 | nsphmx-2-thermal | 41.100 | H120 / Δ5；C125 / Δ0 | 0 |
| 36 | nsphmx-3-thermal | 41.100 | H120 / Δ5；C125 / Δ0 | 0 |
| 37 | gpuss-0-thermal | 42.800 | P105 / Δ5；H120 / Δ5；C125 / Δ0 | 1 |
| 38 | gpuss-1-thermal | 42.400 | P105 / Δ5；H120 / Δ5；C125 / Δ0 | 1 |
| 39 | gpuss-2-thermal | 42.800 | P105 / Δ5；H120 / Δ5；C125 / Δ0 | 1 |
| 40 | gpuss-3-thermal | 44.000 | P105 / Δ5；H120 / Δ5；C125 / Δ0 | 1 |
| 41 | gpuss-4-thermal | 43.200 | P105 / Δ5；H120 / Δ5；C125 / Δ0 | 1 |
| 42 | gpuss-5-thermal | 43.200 | P105 / Δ5；H120 / Δ5；C125 / Δ0 | 1 |
| 43 | gpuss-6-thermal | 43.600 | P105 / Δ5；H120 / Δ5；C125 / Δ0 | 1 |
| 44 | gpuss-7-thermal | 43.600 | P105 / Δ5；H120 / Δ5；C125 / Δ0 | 1 |
| 45 | gpuss-8-thermal | 43.600 | P105 / Δ5；H120 / Δ5；C125 / Δ0 | 1 |
| 46 | gpuss-9-thermal | 42.800 | P105 / Δ5；H120 / Δ5；C125 / Δ0 | 1 |
| 47 | gpuss-10-thermal | 43.200 | P105 / Δ5；H120 / Δ5；C125 / Δ0 | 1 |
| 48 | ddr-thermal | 43.200 | H120 / Δ5；C125 / Δ0 | 0 |
| 49 | mdmss-0-thermal | 41.800 | H120 / Δ5；C125 / Δ0 | 0 |
| 50 | mdmss-1-thermal | 40.700 | H120 / Δ5；C125 / Δ0 | 0 |
| 51 | mdmss-2-thermal | 40.700 | H120 / Δ5；C125 / Δ0 | 0 |
| 52 | mdmss-3-thermal | 41.500 | H120 / Δ5；C125 / Δ0 | 0 |
| 53 | camera-0-thermal | 41.100 | H120 / Δ5；C125 / Δ0 | 0 |
| 54 | camera-1-thermal | 41.100 | H120 / Δ5；C125 / Δ0 | 0 |
| 55 | video-thermal | 41.100 | H120 / Δ5；C125 / Δ0 | 0 |
| 56 | pmh0104-thermal | 37.000 | P95 / Δ0；H115 / Δ0；C145 / Δ0 | 0 |
| 57 | xo-thermal | 37.807 | H80 / Δ2 | 0 |
| 58 | lcm-thermal | 37.674 | H80 / Δ2 | 0 |
| 59 | skin-thermal | 39.892 | H80 / Δ2 | 0 |
| 60 | flash-led-thermal | 34.029 | H80 / Δ2 | 0 |
| 61 | wlan-thermal | 37.846 | H80 / Δ2 | 0 |
| 62 | usb2-conn-thermal | 35.971 | H80 / Δ2 | 0 |
| 63 | rear-cam-thermal | 35.817 | H80 / Δ2 | 0 |
| 64 | qtm-thermal | 37.636 | H80 / Δ2 | 0 |
| 65 | quiet-thermal | 37.178 | H80 / Δ2；P43 / Δ2；P44 / Δ2；P45 / Δ2；P46 / Δ2；P47 / Δ2；P48 / Δ2；P49 / Δ2；P50 / Δ2 | 11 |
| 66 | batt-thermal | 35.434 | H80 / Δ2 | 0 |
| 67 | usb-thermal | 36.707 | H80 / Δ2 | 0 |
| 68 | ufs-thermal | 38.246 | H80 / Δ2 | 0 |
| 69 | batt2-thermal | 34.841 | H80 / Δ2 | 0 |
| 70 | wls-thermal | 37.595 | H80 / Δ2 | 0 |
| 71 | fcam-thermal | 37.816 | H80 / Δ2 | 0 |
| 72 | pmh0110-d-thermal | 39.512 | P95 / Δ0；H115 / Δ0；C145 / Δ0 | 0 |
| 73 | pmh0110-f-thermal | 40.854 | P95 / Δ0；H115 / Δ0；C145 / Δ0 | 0 |
| 74 | pmh0110-g-thermal | 41.358 | P95 / Δ0；H115 / Δ0；C145 / Δ0 | 0 |
| 75 | pmh0110-i-thermal | 40.518 | P95 / Δ0；H115 / Δ0；C145 / Δ0 | 0 |
| 76 | pmh0101-thermal | 40.417 | P95 / Δ0；C115 / Δ0 | 0 |
| 77 | pmih0108-thermal | 39.110 | P95 / Δ0；H115 / Δ0；C145 / Δ0 | 0 |

## 8. 证据与未测试范围

本次核对：当前sysfs完整导出；当前 /sys/firmware/fdt 回收；192.168.88.9 Docker反编译成功；quiet全部11个min/max state、77个DTS温区和42条map核对；实际t42相关源码与作者固定11a25bf8补丁、主线模板比较。没有修改温度或用仿真温度触发降频，也未故意触发critical保护。

- [当前完整sysfs原始记录](evidence/all-thermal-current.log)
- [当前真实FDT反编译](refs/live-fdt-current.dts)
- [温控及充电来源逐项核对](THERMAL-SOURCE-ANALYSIS.md)
- [实际t42充电驱动](refs/kernel-thermal-sources/drivers/power/supply/qcom_battmgr.c)
- [当前频控与USB供电复核](inputs/thermal-report/core-perf-after.log)
- [持续温度/频率/供电记录快照](inputs/thermal-report/telemetry/runtime.jsonl)

报告涵盖Linux目前可见配置；未导出的充电、Wi-Fi等固件内部策略不在已证范围。quiet各档实际游戏负载效果和冻机前温度需下一轮持续记录验证。
