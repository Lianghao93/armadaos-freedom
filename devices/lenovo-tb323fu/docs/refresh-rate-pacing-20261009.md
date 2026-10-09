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

## 17:30—17:50：164 档仍未通过，明确保留失败证据

相位延后 800 µs 的单项候选实测约 120.52 FPS，仍有大量双周期间隔，已关闭该候选开关，不作为维护补丁。

新候选允许已经完成的非 FIFO 游戏 base 使用本轮空 timer 留出的绘制机会。22 项独立边界检查及完整 Gamescope 构建通过，运行 `/proc/247318/exe` SHA256 为 `2e53fd7c1c7da1223b2efd7d998ca6f326c53444036f8e27d685610a5175eb92`，整机 boot_id 仍为 `eedcf26f-e74c-4f81-86fc-e1b31b8226fb`。正常 HUD、原四面体色彩及无补帧的 Brotato 标题场景，30 秒 CSV 统一取 [3,28]：2988 帧 / 25 秒 = 119.52 FPS，间隔均值推算 119.519，P50 6.164 ms、P95 12.319 ms；1078 个间隔落在 12—20 ms。164 档没有通过，不能标记为全部五档完成。

同窗 10.02 秒 trace 全部 CPU overrun 为零：物理 vblank 1643 次，均值 6.097569 ms，完成翻页 1359 次；物理 164 Hz 不等于游戏已经达到 164 FPS。日志首 128 次绘制判定中，49 个空 timer 机会有 40 个随后消费、9 个未消费，未复现 overlay-only timer 提前绘制。因此放弃先做 HUD 延后的猜测。

只读 `/proc/247318/mem` 采样通过二进制 SHA 和 nm/objdump 偏移核对，2 秒 1800 行，与阶段日志对齐：失败时剩余 5,787,320 ns，前后采样 lastDraw 均为 5,679,025 ns，加原 350,000 ns 余量后的预算 6,029,025 ns 大于剩余时间。预算拒绝得到实证；lastDraw 包含从 timer 计划唤醒开始的等待，不能当成当前绘制工作本身。

另一个跳周期样例：已消费目标 P=32918636820561，T=6097561，观测下一目标 Q=32918649014561。Q−T 比 P+T 早 1122 ns。现有 preemptive 计算把新物理相位网格与旧目标最低时间混用，严格 ceil 对这 1122 ns 也会加完整 T，静态计算精确符合 Q；目前缺少实际 Calc 分支日志，仍属待验证归因。

下一候选分别开关验证：preemptive 延续已消费 timer 的同一预测周期、真实 Mark 继续按屏幕重新校相；绘制预算另外测量 paint_all 开始至实际提交端点的工作耗时，保留原整帧反馈不变。加入有界 Calc 和预算日志，先单项对照，再决定最终维护方案。本阶段不加入未经定位的 overlay 延后，不写内核或启动镜像、不重启整机。

### 18:25 本轮补证：周期对照无效，实际工作仍超预算

- r2 同一二进制三项对照（164 Hz、正常 HUD、原始 tetrahedral/nearest LUT）：仅周期锚点 3039/25=121.56 FPS；周期+工作预算 3083/25=123.32 FPS；仅工作预算 3131/25=125.24 FPS。三项均不通过 164 档，不能称五档修复完成。
- 64 条 CalcNextWakeupTime 实际诊断里，26 次 preemptive 计算全部 target=prior+T，没有复现数学例子中的 ceil 额外跨期；该周期开关已关闭，不能把理论路径当作现场根因。
- paint 工作时间实测 4.2→5.7 ms，预算拒绝仍与缺帧相符。GPU composite 的约 3.48 ms 是两秒均值，BOTTOM_OF_PIPE 起始排除了此前队列等待；不能把它与单帧完整工作时间直接相减。
- 128 次实际 paint 中未复现 overlay-only 消耗机会：空机会 49、消费 40、预算拒绝 9。暂不引入 overlay 延后策略。
- 后续只做 shader 等价优化组合对照及默认关闭、限定机型/164 档的 64 帧阶段计时，定位提交前／GPU 等待／DRM 提交；保持原有等待和同步。CPU/GPU 频率、温控、分辨率未改。

### 18:55 等价 shader 组合对照（164 仍未通过）

- 已验证 branchless tetrahedral 与透明像素／未混合 SDR 优化组合：3836 帧/25秒=153.44 FPS，均值6.517ms，P50 6.114ms、P95 12.025ms、P99 12.157ms。比前一组合有改善，但不能当成164通过。
- GPU composite 查询为两秒平均3149us（325/328次）；原始 tetrahedral/nearest 的四次采样与颜色语义保留，8128个float32数学模型用例无差异。这不是GPU位级颜色验收。
- 新的64帧阶段诊断初版错误使用ARMADA_DEVICE和164作为输出单位，未激活，所以没有有效阶段日志。已对照运行进程确认实际GAMESCOPE_INTERNAL_DEVICE_ID=lenovo-tb323fu，g_nOutputRefresh=164000mHz并修正。该空记录不可作为诊断或通过证据。

### 19:45 正常 HUD 基线与定时器对照准备

- 阶段诊断 r2 的 64 条记录全部有效：完整 paint 工作均值 4.022 ms，present 等待均值 3.812 ms，record/submit 均值 72.7 us，atomic 均值 42.0 us。日志位于 pending-flip wait 之前，不包含之后的日志/等待，不能直接当整个周期。
- 关闭用户阶段日志后的 10 秒 trace 没有 CPU buffer overrun；显示端约 164 次/秒，只有局部两个双周期间隔。第一处存在约 1.69 ms 的 XWM 调度延迟。没有 hrtimer_expire 事件也可能是已过期 timer 同步置 ready，不能凭缺事件证明取消竞态。
- 同一 branchless/透明/SDR 版本的 25 秒 Brotato 标题场景、正常 HUD：30=750/25=30.00 FPS；60=1492/25=59.68 FPS（一次125 ms长帧）；120=2986/25=119.44 FPS。90 的 CSV 已保留并送远程分析。五档最终验收仍未完成。
- 164 档短时隐藏 HUD 对照：4084/25=163.36 FPS，P50 6.09745 ms、P95 6.246 ms；HUD 已恢复。这是定位对照，不是最终修复或正常 HUD 通过证明。
- 新的默认关闭、精确 TB323FU 限定开关 y700_validate_timerfd_expiration，在 ScheduleMutex 内先读真实到期计数；EAGAIN 保留未来 timer/state。真实 Linux timerfd+epoll fixture 先取得旧 readiness、不读旧计数即 rearm 后验证 NotReady 与未来 timer 保留，正常到期一次 Ready、重复 NotReady、无效 FD Error；远程 -Wall -Wextra -Werror 编译和 fixture 通过。完整 binary 编译通过，SHA81fb52f9...404799，并已仅重载用户会话、核对 /proc/exe SHA 与不变 boot_id。实际 FPS 改善仍待 A/B，不作为已证根因。

19:53 补证：90 档 2244/25=89.76 FPS，P50 11.1111 ms、P95 11.250 ms。timerfd 同 binary 正常 HUD 的 164 档 A/B，关闭3895/25=155.80，开启3893/25=155.72，无有意义改善；开关已关闭，不进入最终启用配置。下一版仅扩展默认关闭的有限帧编号/回调日志，不改变调度行为。

20:01 场景区分：继续存档进入游戏内第3波商店，同 binary／正常HUD／164档25秒4083帧=163.32 FPS，P50 6.09787ms、P95 6.2099ms，整机未重启。标题场景同档仍155.8，不把商店通过等同所有场景通过。用户既有存档/模组文件未手工修改；正常继续游戏进入商店。

### 20:16 旧 base 与 overlay 合成的关联证据

- 日志版本0540e104...fee5bb正常HUD、关闭诊断日志的标题场景3960/25=158.40FPS，仍不通过164。日志记录窗口内256次base Present有8个重复ID，不能外推为全部3秒FPS。
- 11108先后两次Present间隔6.105ms，第二次来自overlay-only重绘；新11110的done被主循环接受时已超过该target124us。done到callback仅146.8us，到新base Present175.9us，因此不把callback独立等整T当主要根因。已有成功late路径done到callback28.5us，不引入另一项callback改变。
- 同窗trace样例95097：旧base重复Present=.969650提交，GS完成=.974161，target=.975277561；附近FEX唯一三提交组最后retire=.970618，早target约4.66ms；XWM在合成后等待pending flip，主循环95099 done=.975525626才接受。没有commitID与GPU序号/fence Signal的直接映射，不能宣称这组三提交已证明该游戏buffer的fence ready。
- 静态顺序：main先处理done，再检查/import新Wayland资源；已知完成的import也可能在本轮done处理之后直接Signal。下一次候选仅针对已经在队列中尚未被主循环接受的nonFIFO新game commit、164档Normal timer且overlay-only时暂缓一次原旧base合成，并允许保留overlay dirty的late机会；用既有nIgnoredOverlayRepaints保证下一timer回到原paint，不改callback/limiter/频率/温控。另排除adaptive_sync_ignore_overlay=true以免其loopreset破坏fallback。默认关闭，未验证FPS收益，不作为完成成果。

### 20:30—20:46：用户降低分辨率后的五档对照

用户将《土豆兄弟》的游戏分辨率降低，并明确确认看到164帧，随后要求固化当前方案。只读 XRandR 查询确认游戏 XWayland CRTC 为1920×1200（164档合成的模拟时序163.868Hz）；实际内屏保持164000mHz，内部有效targetFPS=164。未手工修改游戏画质/模组/存档，保留正常四档HUD与色彩，未启用补帧。

同一运行binary SHA0540e104f1d7f6709ae1e7120bb96e523635504c417559297af1147860fee5bb，菜单关闭的标题场景各30秒CSV，统一去掉首3秒和末2秒，25秒稳定窗：

| 档位 | 游戏帧间隔推算FPS | P95帧间隔ms |
|---|---:|---:|
|30|29.9599|33.49422|
|60|59.8799|16.81420|
|90|89.9597|11.26372|
|120|119.6793|8.473109|
|164|163.4391|6.29956|

164窗4086条游戏GPU-ready commit间隔，均值6.118488ms，P50 6.09615ms，P99 6.393717ms；6条为12—20ms，没有20ms以上长帧。这里记录游戏帧间隔，不把物理vblank次数当成游戏FPS，也不把它当GPU渲染耗时。首次两份20:20/20:23 CSV有模式切换/右侧菜单混入，不进入此表。

结论：在1920×1200和本次标题场景，正常HUD下五档均接近对应值，120/164不再固定掉到60。原生3040×1904的标题场景此前约158.4，不能将降低分辨率后的通过写成原生分辨率或所有游戏场景已通过。原生分辨率第3波商店曾达163.32，同样要区分场景。主界面长期帧时间抖动、FEX初次启动SIGSEGV、HDR及外屏未随本轮完成验收。

固化选择：0030重生为branchless tetrahedral/nearest +完全透明overlay fast path +受条件约束的SDR重复transfer省略；0031含late-base与实际paint work预算。只按精确lenovo-tb323fu型号在Lua启用late-base和work-budget。preemptive cycle及有界phase日志保持false；timerfd-read、frame-flow扩展日志、overlay-wait、native/trilinear、wakeup-floor不纳入这次最小生产源码。当前0031保留默认关闭的早期phase/cycle诊断，属于未启用代码，不称其提供性能收益。

封装一致性核验发现原临时rendervulkan.cpp快照少了上游0007格式策略和0009可选实时队列处理。维护补丁0030应用到完整上游+Armada1—29后保留二者；最终RPM采用顺序重放的rendervulkan.cpp，以免仓库重建与临时源不一致。其余10份生产源与选定归档一致。需在最终RPM安装后复测，不能将此前binary的测试直接当最终RPM验证。

21:00用户补充：原生分辨率选择最高性能档也能达到164FPS。只读XRandR随后确认3040×1904、模拟时序163.925Hz；性能档由用户切换，Codex未改变CPU/GPU策略。此前约158.4只代表当时性能状态/标题场景，不得归纳为原生分辨率固定上限。后台复测独立记录，首轮CSV因Steam改分辨率时重写Mango配置、去掉日志输出路径而没有产生新文件；该次不算新FPS证据，重新加载记录配置后再采样。

### 21:18 固化交付与用户复测结论

用户明确说明：原生分辨率在最高性能档也达到164FPS；随后要求不要继续采样，以其实际观察为结论。尊重该要求，停止继续FPS采样和本轮运行记录服务，不将退出游戏后的21:02稀疏CSV作为游戏原生帧率证据。不把此前低性能状态的158.4样本外推成分辨率固定上限。

最终正常RPM远程构建退出码0，版本1:3.16.31^1-1.100912.fc44.armada，主包与libs同版本；包含源码补丁0029—0032，0032只是默认关闭的64次上限DRM诊断，Lua明确false。正常%conf/%build/%install与BRP均执行，本轮不生成可选debug子包。最终二进制SHA256 b2ea307952133339c2d733fc85f308cabf50c75e2ffd95a4ae596c8781045cd2；源码包追加0032声明后生成成功，与包内保留的诊断来源对应。源与包校验、构建日志保存在docs/evidence/refresh-final-20261009和NAS任务evidence/production-final-install-r3。

设备RPM依赖预检和正式安装均成功，原100904主包/libs被100912正常替换。仅重载Steam/Gamescope用户会话：新PID366208的/proc/exe SHA与RPM包内一致，启动日志确认正确识别armada_lenovo_tb323fu_lcd；整机boot_id仍eedcf26f-e74c-4f81-86fc-e1b31b8226fb。按用户要求不对固化包继续收集新的FPS样本，之前1920×1200五档自测和用户原生最高档观察分开记录。没有新内核/boot写入或整机重启。

实际固化：Lua自动启用late-base/work-budget，关闭cycle/phase/present diagnostics；透明overlay与SDR重复工作优化精确型号gate，四面体/nearest颜色路径保留。未启用timerfd-read、overlay-wait、native/trilinear或wakeup-floor。原生性能档由用户决定；本轮没有修改CPU/GPU频率或温控限制。FEX启动崩溃、主界面其它卡顿、HDR/外屏等既有事项并不随此轮宣称全部完成。
