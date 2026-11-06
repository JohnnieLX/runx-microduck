# 变更记录 / Changelog

本文件只记录润象科技（Runx Technology）维护分支 `our-fork` 相对上游
[fanhao375/microduck-replica](https://github.com/fanhao375/microduck-replica) 的改动。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

上游自身的历史不在本文件重复，见 `git log origin/master`。

## [Unreleased]

### Changed

- **文档许可放宽**：本项目原创文档由 CC BY-NC-SA 4.0 改为 **CC BY 4.0**，
  允许商业使用、改写与再分发，只需署名。上游模型及其派生几何不受影响，
  仍为 CC BY-NC-SA 4.0。LICENSE 相应从“双许可”改为按内容类型分三条声明（本次提交）。

### Added

- **自由语音对话**：`software/duck-echo/` 新增云端识别、大模型应答与语音合成链路
  （`remote_models.py`、`remote_dialogue.py`），按住 R3 说话即可闲聊，不再受固定指令表限制。
  识别、应答与合成的地址和模型由 `/etc/default/duck-echo` 注入，仓库内不含任何凭证（`6f62869`）。
- **动作二次确认**：新增 `intent_gateway.py`，对话中的“坐下／站起来”只生成候选，
  6 秒内再次确认才下发动作，超时作废（`6f62869`）。
- **对话模式禁用本地语法**：`speech_control.py` 在对话模式下不启动板载命令识别，
  避免同一句话被本地规则提前执行（`6f62869`）。
- **升级包白名单**：OTA 打包白名单补入对话相关模块，`duck-echo.service` 的
  `RestrictAddressFamilies` 增加 `AF_INET AF_INET6`，保证升级后外网链路可用（`2591fdb`）。
- **Studio 电源控制**：`software/studio/microduck-helper` 新增 root 级 `power` 动词，
  支持 `shutdown` / `reboot`，带 action 白名单与调用者记录（`c78c92d`）。
- **Studio 电源接口**：`software/studio/app.py` 新增 `/api/power` 路由，
  网页端新增电源卡片，采用两步确认交互（`c78c92d`）。

### Fixed

- **升级自锁**：Studio 就绪检查的 `guarded` 判据改为
  `updates.installing() and not own_prepared`，不再因残留 install marker 永久阻塞升级入口（`c78c92d`）。
- **升级包目录权限**：`build_package.py` 补发 `0755` 父目录条目，
  修复解压后 Studio 目录不可进入（CHDIR 失败）的问题（`36d9b29`）。
- **升级预检过严**：`control.py` 预检去掉对 `homed` 的要求，避免合法升级被误拒（`85f3f4a`）。

### Security

- `/api/power` 要求 session、origin、CSRF 三重校验，并限制请求体 4KB，
  防止网页端被跨站请求触发关机或重启（`c78c92d`）。

## 许可说明

以上改动全部落在 Apache-2.0 覆盖范围内（`software/studio/`、`software/updater/`）。
本分支未修改任何 CC BY-NC-SA 内容，也未新增上游模型或动作模型。
