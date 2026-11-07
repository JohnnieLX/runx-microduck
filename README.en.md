# Runx Microduck

**English** · [简体中文](README.md)

Maintained by Runx Technology. This repository is a fork of
[fanhao375/microduck-replica](https://github.com/fanhao375/microduck-replica): the replica itself comes from
upstream, while the `our-fork` branch carries our own iteration — voice dialogue, spoken motion execution,
web power control and updater reliability fixes. Each change is listed in [CHANGELOG.md](CHANGELOG.md).

> **Upstream README:** [README-upstream.en.md](README-upstream.en.md) (archived upstream copy, synced with
> upstream `master`) · [upstream on GitHub](https://github.com/fanhao375/microduck-replica/blob/master/README.en.md).
>
> Sourcing, printing, assembly, calibration and image flashing live in that document and in
> [Documentation](#documentation) below; this file only covers what the fork is, how it differs, and where to start.

<p align="center"><a href="software/studio/README.md"><img src="assets/studio-overview-20261025-demo.jpg" alt="Microduck Studio web console, readings are simulated" width="920"></a><br><sub>Studio web console, readings are simulated</sub></p>

## What this is

A fully replicable desktop quadruped duck: **15 Feetech HD-1910 servos + Radxa ZERO 3W + RPI Robot HAT**,
with standing, walking, roll-and-rise validated on real hardware upstream.

| Part | Location | Notes |
|---|---|---|
| System image and packages | `software/image/`, upstream [releases](https://github.com/fanhao375/microduck-replica/releases/latest) | Flashable image, OTA packages |
| Web console | [software/studio/](software/studio/README.md) | Wi-Fi setup, calibration, remote control, status, power |
| Voice pipeline | [software/duck-echo/](software/duck-echo/README.md) | On-board capture/playback, cloud ASR/LLM/TTS |
| Updater orchestration | `software/updater/` | Signature checks, atomic swap, rollback |

Not included: `robotd` (motion core) and `updaterd` (update engine) are closed-source binaries; only the
orchestration and deployment layers are here. CAD, print files and the assembly manual are in the separate
[CAD repository](https://github.com/fanhao375/microduck-replica-cad).

## What this branch adds

| Area | Change | Status |
|---|---|---|
| **Voice dialogue** | Hold R3, talk, cloud ASR → LLM → TTS reply; no fixed command list | Working on hardware |
| **Spoken motion** | "Sit down / stand up" executes immediately, no second confirmation | Verified on the robot |
| **No local race** | On-board command grammar is disabled in dialogue mode | Active |
| **Web power control** | Shutdown / reboot from Studio, root allow-list plus session, origin and CSRF checks | Live |
| **Updater reliability** | Fixed update self-lock, extracted-directory permissions, over-strict preflight | Fixed |
| **Docs licence** | Original documentation relaxed to CC BY 4.0 (commercial use allowed, attribution only) | Done |

Voice settings (endpoints, models, API keys) are injected through `/etc/default/duck-echo`; no credentials
are stored in this repository. See [software/duck-echo/](software/duck-echo/README.md) for the on-robot setup.

## Where to start

| Goal | Entry |
|---|---|
| First run: flash → Wi-Fi → calibration → remote | [First-use guide](docs/上手指南/README.md), [upstream README](README-upstream.en.md) |
| See what the duck can do | Upstream [hardware demos](README-upstream.en.md#the-ducks-in-action) |
| Printing and assembly | [CAD repository](https://github.com/fanhao375/microduck-replica-cad), [BOM](BOM.en.md), upstream [assembly videos](README-upstream.en.md#-assembly-videos) |
| Software work | [software/studio/](software/studio/README.md), [software/duck-echo/](software/duck-echo/README.md) |
| Pitfalls and debug notes | [踩坑记录](踩坑记录.md), [调试记录](调试记录.md), [构建日志](构建日志.md) (Chinese) |

## Documentation

Paths are relative to the repository root; the mechanical CAD lives in its own repository.

| Topic | Location | Entry |
|---|---|---|
| Use and assembly | [CAD repository](https://github.com/fanhao375/microduck-replica-cad) · [BOM/](BOM/README.md) | [First-use guide](docs/上手指南/README.md) · [BOM](BOM.en.md) · [Assembly videos](README-upstream.en.md#-assembly-videos) |
| Software and image | [software/](software/README.md) | [Studio](software/studio/README.md) · [Image build](software/image/README.md) · [Voice pipeline](software/duck-echo/README.md) |
| Training | [software/training-studio/](software/training-studio/README.md) · [software/training/](software/training/README.md) | Web workbench, simulation and model export |
| Electronics and firmware | [hardware/](hardware/README.md) | Board projects · IMU firmware |
| Tools | [tools/](tools/README.en.md) | Flashing, serial debugging, Feetech material, analysis scripts |
| Docs and records | [docs/](docs/README.en.md) | Full documentation index, pitfalls, debug and build logs |
| Upstream original | [README-upstream.en.md](README-upstream.en.md) | Sourcing, printing, assembly, calibration, training, community |

## Licence and attribution

Code is Apache-2.0, original documentation is CC BY 4.0 (commercial use and modification allowed with
attribution), upstream 3D models and imagery are CC BY-NC-SA 4.0 (non-commercial). See [NOTICE.md](NOTICE.md).

This project is a replica of [Pollen Robotics' Microduck](https://pollen-robotics.com/microduck/) and is
neither affiliated with nor endorsed by Pollen Robotics. The replica itself is maintained upstream by
[fanhao375](https://github.com/fanhao375/microduck-replica).

## Contributing

This is Runx's maintenance fork: file patches, hardware reports and issues in
[this repository](https://github.com/JohnnieLX/runx-microduck/issues). Changes to the replica itself belong
in [upstream](https://github.com/fanhao375/microduck-replica). See [CONTRIBUTING.md](CONTRIBUTING.md) and
[SECURITY.md](SECURITY.md).
