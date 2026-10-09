# TB323FU Gamescope 高刷新档位出帧异常

2026-10-09；《土豆兄弟》1942280；7.3.0-rc4-tb323fu-t42；Gamescope运行版本3.16.31^1-1.100902.fc44.armada。问题尚未修复或验收。

## 用户要求及复现

只保留30、60、90、120、164五档，删除其余联动帧数选项。这是屏幕刷新与Gamescope帧数上限的联动设置；用同一游戏逐档验证实际出帧达到相应档位，不能以菜单可见或物理刷新正确代替验收。

用户复现90档立即90帧，120档立即降至60帧，164档也降到约60帧。未修改游戏自身配置。

## 已取得证据

- 只读drmCrtcGetSequence，CRTC106：90档实测90.00024Hz、120稳定后120.00020Hz、164档163.99958Hz。先前120转换中的92.788Hz混合样本已排除，未当作120稳定值。
- 164档30秒内真实DPU vblank4920次、完成翻页1640次；完成翻页间隔18.2928ms，序号差恒3，即约54.667张画面/秒。这是当前故障证据，不能描述164档出帧正常。
- 90档翻页间隔中位数11.112ms，序号差中位数1；120档16.669ms、序号差中位数2。个别间断及120 trace覆盖范围必须结合各CPU统计，不把带间断的全窗平均当成稳定FPS。
- 游戏XWayland :1在物理90/120切档后RandR仍上报163.925Hz。原源码切档仅更新Steam server0、漏更新游戏server1。Wayland呈现周期还会使用真实输出周期，因此不能仅凭此缺口解释全部退帧。
- 固定刷新timerfd调度中，offset饱和到T且提前wake已过时，GetNextVBlank会把wake推进1T，随后target=wake+offset把仍未来的物理target也跳过。这与90的一周期、120的两周期、164的三周期翻页现象吻合。
- 同窗Gamescope提交至退休时间约7.55ms；它包含排队和调度，不能冒充纯GPU渲染时长。164档6.098ms周期下仍须解决实际合成/提交成本，单次调度修复不保证164帧。

## 正在实施

固定刷新timerfd将physical target与wake分开；过期wake夹到当前时刻，target保持真实相位。消费后的preemptive重排推进旧target至少1T，避免无新flip时自旋；新硬件回调重新校准。VRR、nudge、GPU完成等待及pendingflip保护保留。

代码候选及纯计算回归已由CodeBuddy生成，静审通过。2026-10-09 12:30，远程 Docker 使用 GCC 16、C++20 和 -Wall -Wextra -Werror 完成纯计算回归：60 checks, 0 failures。完整生产 RPM 正在编译；现场验证尚未通过，未部署候选。

现有TB323FU Lua仅列出五个Hz；Steam现代服务会派生额外分频帧数选项。需要在真实生成/消费点按机型过滤，不能添加无效Lua字段或改变其他设备的合法选项。

所有测试最终需形成30/60/90/120/164的实际出帧记录。当前90可用、120和164仍失败。未重启系统或触碰boot。


## 12:55 前后追加证据

Armada Control 候选五档过滤通过远程 Docker：40 项 node 测试（包含 9 项新策略测试）及 i18n 检查，Rollup 构建成功。静审覆盖首次 QAM 未创建查询、同步 disposer、晚到 Promise/Proxy 失效，以及 exact+refetchType:all 刷新已有缓存；尚未在设备加载候选。

30/60 两档各 10.002 秒对照：FEX 游戏 PID63816 提交次数分别900/1797。同一 OpenGL 游戏每帧3次 GPU 提交，对应约29.994/59.888游戏帧/秒。30档仍可能每秒约60次合成翻页（包括HUD重复绘制），不能把合成翻页当成游戏FPS。

此前根据 gamescopectl help 未出现log项判断“没有composite timing”不成立：help列命令，动态log变量未出现在该列表。当前已部署binary包含composite_timing，实际开启 log_composite_timing debug 后成功记录该计时。

同一游戏/164档 GPU timestamp 合成区间：颜色开启約7270us，短时关闭 GAMESCOPE_COLOR_MANAGEMENT_DISABLE 对照约2417–2458us。该区间仍可能包含GPU抢占，不等同纯算术执行时间。对照结束已remove原先不存在的属性、恢复颜色，日志级别info。不会以永久关闭颜色作为最终修复。下一步在保留四面体插值语义下检查shader成本。

## 13:40 运行态检查

兼容现有校准API的Armada Control runtime包通过40项测试及i18n/构建，实际dist对设备旧包仅150新增、0删除。仅部署dist/index.js及config.py（deviceId新增一行），没有覆盖新版校准后台。运行中的Steam现代GetState接口确认supported_frame_rates只有[30,60,90,120,164]，supported_refresh_rates保持原五档；整机boot_id未变化。

调度单项RPM 100904构建成功并已加载，/proc/113233/exe SHA256为0f71a52489de8c015bb7547659aa190ba2899cb501242480d6f748a9d6b9072a。120档10.003秒、零overrun，FEX116321共有1689提交（按三次提交每帧估算约56.3FPS）；164档1503提交，仍未达标。GPU composite timing约7119-7120us。公式修正不代表真实吞吐改善。

保留四面体LUT语义的branchless候选：8128 float32模型案例无差异；实际glslang SPIR-V函数0个OpBranchConditional、4次OpImageSampleExplicitLod；增量编译完成。候选SHA256为5159dabefa771ee867676c2183c30a1a82f11dc46b559e3545094e823af5bcdb。第一次会话重载启动早于install完成，/proc/121816/exe仍为旧binary并显示deleted，所以该次不算候选验证；停止后重新加载，在13:39确认/proc/123786/exe与候选SHA一致。下面的实测必须对应此进程及FEX125010。

重载会话后的游戏启动发生两次FEX SIGSEGV（带mod资源/脚本报错），另次成功启动。未改动游戏mods或用户存档；保留日志，不能把崩溃归咎于刷新设置或认定本轮已修。整机没有重启、boot未写。
## 13:56：内部有效限帧和色彩开关对照

旧X atom GAMESCOPE_FPS_LIMIT持续60不是内部有效上限，现代Wayland入口不会回写它；不能据此宣称120→60映射错误。已使用此运行binary对应未strip ELF的nm符号偏移、/proc/123786/maps映射及root只读/proc/123786/mem，核实真正g_nSteamCompMgrTargetFPS在90/120/164分别为90/120/164；ChangeFPS标志为1。UI值确实进入内部限帧。读取未暂停/修改进程。

同一FEX125010游戏：90档2661提交/约10秒≈88.7FPS；120颜色开启1806≈60.2FPS，而临时color disabled时3576≈119.2FPS；164内部有效上限164，临时color disabled时4887≈162.9FPS。每次对照后remove原本不存在的属性，核实notfound，恢复完整颜色。该色彩合成路径构成本轮主要瓶颈，branchless候选6884–6885us仍不够。GPU cur/min/max当前均902MHz，未更改CPU/GPU策略。

新候选仅对GAMESCOPE_INTERNAL_DEVICE_ID精确匹配lenovo-tb323fu使用上游perform_3dlut_native + Vulkan linear sampler，保留shaper、3D LUT、gamut、night及输出EOTF处理；其他设备默认原4面体/nearest。用specialization constant8固定GPU路径。三线性与四面体数学上不同，不能把前面的8128相等测试当作该候选颜色等价证据。正在远程增量编译，尚未部署/通过五档实测，不标修复完成。## 14:45：更准确的归因和透明区域候选

硬件三线性 LUT 候选真实加载后（SHA209ee359…1a6，型号gate=1），120档仍约60帧，未通过。保持完整色彩不变、仅临时隐藏HUD，游戏从1803提交/10秒变为3603提交/10秒（同一菜单场景，先前已观察每帧3提交）；已恢复HUD。说明透明全屏overlay的合成成本是关键，而不只是120设置值或屏幕时序。

实际选项过滤已生效：GetState中的supported_frame_rates和supported_refresh_rates均只有30/60/90/120/164。以现代服务和内部真实目标为准，不再用未镜像更新的旧X atom60推断生效帧数。

新的精确TB323FU候选恢复原四面体LUT及nearest采样，仅在alpha-blend overlay采样到RGBA全0时跳过CTM/LUT；底层、none/未知mode、非零alpha、alpha0但RGB非0保留。原colorimetry.h与reference SHA一致。15项边界/透明混合模型测试及所有着色器、完整增量binary编译已在192.168.88.9 Docker通过。binary SHA61c9f1bdd080956f18e03b01eee59ad7be05266d592c9b09bb70597dc01b2546。不是任意颜色变换逐像素旧行为等价，也尚未通过五档实测。

后续采用现成MangoApp CSV记录游戏buffer GPU-ready commit间隔，并与DPU翻页交叉对照。消费者与Gamescope协议中的两个frametime字段名反置、偏移相同，必须按实际语义标注：CSV不是物理vblank，也不能单独证明每个新帧已扫描显示。GPU提交/3只作为同场景辅助估算，不作为全部场景最终验收。

## 15:05：透明候选实测与截图方法更正

原色彩透明候选：MangoApp CSV的30/60/90/120均接近对应档位；164 HUD开时约82，短时HUD隐藏后约164，已恢复HUD，仍未通过全部五档。GPU composite约3883-3908us。仅作阶段结果，不认为任务完成。

先前使用 gamescopectl screenshot PATH 4 的命令错误：客户端只转发前两个参数，第三个4被忽略，所得是base-only，不能证明HUD/输出颜色。把 PATH 4 放入同一个参数同样不成立（服务器未分割value）。改用根X属性GAMESCOPECTRL_DEBUG_REQUEST_SCREENSHOT=4；将硬编码/tmp/gamescope.png只在不存在时链接到任务device cache，取得真正含HUD的90档屏幕buffer，证明HUD确实显示且约90FPS。前面命名为screenbuffer的旧截图需视为base-only。

组合候选SHA a383d2fbac76f0c53abb50936155e9b869b2562345635ae2e3c5470b91c88d24，保留透明guard并在精确TB323FU型号使用native3dlut/linear；其他机型仍tetrahedral/nearest。原tetrahedral颜色算法与trilinear不同，不能宣称颜色逐像素等价，画面和HDR仍需复核。远程全着色器/增量binary编译已过，正在会话加载，未通过实测。


## 15:37—15:49：164仍失败，转向逐阶段时序取证

组合native+透明候选a383d2fb…c88d24实测164仍约82FPS，未通过，不能进入最终补丁。原四面体+透明候选五档25秒稳定窗CSV：30=29.970、60=59.820、90=89.880、120=118.560、164=81.940 FPS（游戏GPU-ready提交间隔推算，与翻页交叉核验；不是GPU渲染时长）。

保留原四面体/nearest的SDR未混合像素优化候选62a77b95…14af9已远程编译并加载，exact型号gate=1，boot_id不变。仅满足精确机型、SDR、无ITM/debug、基础层opacity1且透明premult/coverage overlays时，省去最后互逆transfer；shaper/四面体LUT保留。双精度数学模型5项通过，UNORM8半步边界最大取整差1LSB；不能据此宣称GPUfloat32 bitexact或HDR验收。

实际164仍约82，30秒Mango CSV与真实带HUD截图确认；合成GPU计时3480us。实际g_nNestedRefresh=0，g_nOutputRefresh=164000，内部targetFPS=164，已排除nested120。vblank日志rolling4.42ms、draw4.53ms、redzone1.65ms、offset6.07ms；28µs是wake相对前一次刷新边沿的相位，并非渲染仅剩28µs。现有trace显示合成启动比上一完成flip晚约6.34ms，正在对齐Mark传入stamp/调用now、schedule target/wake、实际Poll。新增诊断仅各64条、默认关闭、不改变调度，未作为生产补丁。

当前内核CRTC106/107/108只有CTM和VRR_ENABLED，无DEGAMMA_LUT/GAMMA_LUT/DPU_3D_LUT，因此现有0017硬件颜色卸载不能在本轮直接用开关启用。无kernel/boot写入、无整机重启。本轮未改CPU/GPU频率、温控或游戏mods/配置；首次Steam重载后的FEX启动SIGSEGV仍单独记录，不认为已修。
