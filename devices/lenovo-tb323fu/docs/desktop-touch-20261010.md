# Y700 桌面触摸校准持久化（2026-10-10）

本报告汇总本次已实现的改动、验证范围与回滚方式，只保留已确认事实。

## 背景与结论

- 驱动重载后原始触摸输入恢复，说明触摸链路可用；仅绑定输出 DSI-1 不能修复桌面点击。
- 改用 4x4 单位矩阵后经用户现场验收：**能打开，位置准确**。
- 固件 WDT 静默与缺失 `RESET_STATE_REK` 等待仍是独立待查问题；本次未改内核，不宣称修复固件恢复。

## 实现

- 新增可选变量 `ARMADA_DESKTOP_TOUCHSCREEN_CALIBRATION`，默认空；仅 TB323FU profile 设置触摸屏名 `NVTCapacitiveTouchScreen` 与 4x4 单位矩阵，按现有机型配置机制处理，不影响其他机型。
- `device-env` 支持三个精确 model 名称，含真机 `Lenovo Legion Tab Y700 5th Gen`，并保留原有别名。
- 每次桌面登录在 completed 标记提前返回之前写入用户 `kcminputrc`，并更新 KWin live 属性；失败仅记警告，不阻断登录。
- `system_files` 经现有构建脚本安装；Gamescope 触摸 udev 规则未改动。

## 验证

- 指定服务器 Docker：新增三名称回归后的 `tests/desktop-touchscreen-test.sh` 与 `tests/desktop-bootstrap-test.sh` 均通过（exit 0）；更早缺 Y700 主触摸屏字段时按预期 exit 1。未构建完整镜像，Docker 不能确认真机 live 触摸行为。
- 五个运行时系统文件已安装，NAS 与真机逐文件 SHA256 核对一致；备份存于本任务 `device-backup`。安装过程未重启、未切换会话，保留机器原有 CPU/IRQ 差异及用户 60%/80% GPU 设置。
- 真机当前为游戏模式，用户已接受的校准配置仍在；新桌面登录自动应用入口已脚本自测，但尚未再次切换桌面进行现场复测。此前为现场用户验收，新登录入口为脚本级验证，二者范围不同。

## 回滚

1. 恢复 `device-backup` 中五个运行时系统文件及用户 `kcminputrc` 备份。
2. 正常重新登录桌面。
