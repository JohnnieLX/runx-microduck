# Runx Microduck（润象 Microduck 复刻）

**简体中文** · [English](README.en.md)

润象科技（Runx Technology）维护的 Microduck 四足机器鸭仓库，fork 自
[fanhao375/microduck-replica](https://github.com/fanhao375/microduck-replica)。
复刻本体沿用上游，本仓在 `our-fork` 分支上维护自己的迭代：语音对话、语音动作执行、
Studio 网页电源控制、升级链路可靠性修复等，逐条记录见 [CHANGELOG.md](CHANGELOG.md)。

> **原始项目 README**：[README-upstream.md](README-upstream.md)（存档上游原文，随上游 `master` 同步）·
> [上游 GitHub 原文](https://github.com/fanhao375/microduck-replica/blob/master/README.md)。
>
> 采购、打印、装配、标定、镜像烧录这些复刻教程都在原始 README 和下面的[资料导航](#资料导航)里；
> 本文件只回答三件事：这个仓库是什么、跟上游有什么不同、从哪里开始。

<p align="center"><a href="software/studio/README.md"><img src="assets/studio-overview-20261025-demo.jpg" alt="Microduck Studio 网页中控界面，设备读数为模拟数据" width="920"></a><br><sub>Studio 网页中控，设备读数为模拟数据</sub></p>

## 这是什么

一套可整机复刻的桌面四足机器鸭，硬件基线为 **15 颗飞特 HD-1910 舵机 + Radxa ZERO 3W + RPI Robot HAT**，
上游已完成站立、行走、前滚翻后起身的实机验证。

本仓包含：

| 部分 | 位置 | 说明 |
|---|---|---|
| 复刻镜像与软件包 | `software/image/`、上游[发布页](https://github.com/fanhao375/microduck-replica/releases/latest) | 可烧录系统镜像、OTA 升级包 |
| 网页中控 | [software/studio/](software/studio/README.md) | 配网、标定、遥控、设备状态、模型与电源管理 |
| 语音链路 | [software/duck-echo/](software/duck-echo/README.md) | 板端采集回放，识别／应答／合成走自建服务 |
| 升级引擎编排层 | `software/updater/` | 签名校验、原子替换、失败回滚 |

不包含：`robotd`（运动控制核心）与 `updaterd`（更新引擎）为闭源二进制，本仓只提供编排与部署层；
3D 模型、打印工程与装配说明书在独立的 [CAD 仓库](https://github.com/fanhao375/microduck-replica-cad)。

## 本分支做了什么

全部改动落在 `our-fork` 分支，只做增量补丁，不改上游的运动安全逻辑：

| 方向 | 内容 | 状态 |
|---|---|---|
| **语音对话** | 按住 R3 说话，云端完成识别 → 大模型应答 → 语音回放，不受固定指令表限制 | 实机跑通 |
| **动作直接执行** | 说“坐下／站起来”，解析出动作后立即执行，不用再说一次确认 | 板端验证通过 |
| **杜绝本地抢跑** | 对话模式下关闭板载命令识别，同一句话不会被本地规则提前执行 | 已生效 |
| **网页电源控制** | Studio 里直接关机／重启，root 白名单加 session、origin、CSRF 三重校验 | 已上线 |
| **升级可靠性** | 修掉升级自锁、解压目录权限、预检过严三处问题 | 已修复 |
| **文档许可** | 本项目原创文档放宽为 CC BY 4.0，可商用可改写，只需署名 | 已调整 |

### 语音对话怎么用

1. 按下 R3 开始（再按一次退出），听到提示音后说话；
2. 闲聊直接回答；“小鸭子，请坐下”会直接执行动作并回一句结果；
3. 动作被安全条件拦下时，会说明原因，可换个时机再试。

语音链路在板上完成采集与回放，识别、应答和合成走自建服务，模型与地址通过
`/etc/default/duck-echo` 注入，仓库内不含任何凭证。板端装配与测试见
[software/duck-echo/](software/duck-echo/README.md)。

### 边界

- 语音动作链路已用板端注入语音验证，真人整机复验仍在进行；
- 视觉多模态（VLM 看图）代码已在分支内，但按计划暂不启用；
- 摄像头在镜像里默认是测试源，真实摄像头与 ToF 的验证范围见上游版说明。

## 从哪里开始

| 想做的事 | 入口 |
|---|---|
| 第一次上手：烧镜像 → 配网 → 标定 → 遥控 | [首次使用指南](docs/上手指南/README.md)、[上游原始 README](README-upstream.md) |
| 先看鸭子能跑成什么样 | 上游 README 的[实机演示](README-upstream.md#鸭子现在的样子) |
| 打印与装配 | [CAD 仓库](https://github.com/fanhao375/microduck-replica-cad)、[BOM 采购清单](BOM/README.md)、上游 README 的[装配视频教程](README-upstream.md#-装配视频教程) |
| 改软件、看接口 | [software/studio/](software/studio/README.md)、[software/duck-echo/](software/duck-echo/README.md) |
| 想知道踩过哪些坑 | [踩坑记录](踩坑记录.md)、[调试记录](调试记录.md)、[构建日志](构建日志.md) |

软件版本沿革沿用上游基线：镜像基线 20261027，之后软件包修订到 2026.10.3101，
逐版说明见[历史更新](docs/历史更新.md)，润象自身补丁见 [CHANGELOG.md](CHANGELOG.md)。

## 资料导航

路径相对于本仓库根目录；机械 CAD 在独立仓库维护。

| 项目 | 仓库位置 | 说明与入口 |
|---|---|---|
| 使用与装配 | [CAD 仓库](https://github.com/fanhao375/microduck-replica-cad) · [BOM/](BOM/README.md) | [首次使用指南](docs/上手指南/README.md) · [采购清单](BOM/README.md) · [装配视频](README-upstream.md#-装配视频教程) |
| 软件与镜像 | [software/](software/README.md) | [Studio 中控](software/studio/README.md) · [镜像构建](software/image/README.md) · [语音链路](software/duck-echo/README.md) |
| 训练平台与工程 | [software/training-studio/](software/training-studio/README.md)<br>[software/training/](software/training/README.md) | 网页训练工作台、仿真环境与模型导出 |
| 电气与固件 | [hardware/](hardware/README.md) | [电路板工程](hardware/README.md) · [IMU 固件](hardware/imu_to_dxl/README.md) |
| 调试与分析工具 | [tools/](tools/README.md) | [工具导航](tools/README.md) · 烧卡、串口调试、飞特资料和分析脚本 |
| 文档与记录 | [docs/](docs/README.md) | [完整文档导航](docs/README.md) · [踩坑](踩坑记录.md) · [调试](调试记录.md) · [构建](构建日志.md) |
| 历史与社区 | [docs/](docs/README.md#深入研究与归档) | [官方更新跟踪](docs/官方更新跟踪.md) · [历史更新](docs/历史更新.md) · [社区贡献](docs/社区贡献.md) |
| 上游原文 | [README-upstream.md](README-upstream.md) | 采购、打印、装配、标定、训练与社区入口 |

## 许可与来源

代码采用 Apache-2.0，本项目原创文档采用 CC BY 4.0（可商用、可改写，只需署名），
上游 3D 模型与影像采用 CC BY-NC-SA 4.0（禁商用）。详见[许可与署名](NOTICE.md)及
[模型使用条款](software/image/overlay/usr/share/doc/microduck/模型说明.txt)。
**复刻镜像内的模型与影像不得随收费产品分发。**

本项目基于 [Pollen Robotics Microduck](https://pollen-robotics.com/microduck/) 复刻，
与 Pollen Robotics 无隶属关系，未获其背书；复刻主体由上游作者
[fanhao375](https://github.com/fanhao375/microduck-replica) 维护。

## 参与与反馈

本仓是润象的维护分支：本仓补丁、实机记录和问题请提到
[本仓 issue](https://github.com/JohnnieLX/runx-microduck/issues)。
属于复刻本体的改动请提交[上游仓库](https://github.com/fanhao375/microduck-replica)。

贡献方式、许可边界与提交规范见 [CONTRIBUTING.md](CONTRIBUTING.md)，
安全问题见 [SECURITY.md](SECURITY.md)。
