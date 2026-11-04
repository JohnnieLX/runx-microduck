# 贡献指南 / Contributing

本仓库是 [fanhao375/microduck-replica](https://github.com/fanhao375/microduck-replica)
的 fork，由润象科技维护 `our-fork` 分支。默认分支即 `our-fork`。

提交前请先读 [LICENSE](LICENSE) 与 [NOTICE.md](NOTICE.md)。**许可边界比代码风格更重要。**

## 许可边界（必须遵守）

| 你要改的路径 | 适用许可 | 可接受 |
|---|---|---|
| `software/studio/`、`software/updater/`、`software/image/`、`software/duck-echo/` 的代码 | Apache-2.0 | 是 |
| `scripts/`、`tools/` 的原创脚本 | Apache-2.0 | 是 |
| `*.md` 文档 | CC BY 4.0 | 可以改，保留署名即可 |
| `software/studio/web/model/`、`tools/servo-web/model/` 的 3D 网格 | CC BY-NC-SA 4.0 | 只改代码不改模型 |
| 上游 MJCF / STL 派生的新渲染图或模型 | CC BY-NC-SA 4.0 | 需在 NOTICE.md 补署名 |

**不要提交：**

- 任何私钥、令牌、出厂凭据、内网地址或序列号
- 来自他人但许可不明的 3D 模型、动作模型、图片、音频
- 试图把 CC BY-NC-SA 内容改成宽松许可的改动（违反 ShareAlike）
- 收费产品中的镜像分发产物

## 提交流程

1. 从 `our-fork` 拉分支：`git switch -c feat/your-topic our-fork`
2. 提交，信息用英文 Conventional Commits：
   `feat(studio): ...` / `fix(updater): ...` / `docs: ...`
3. 一个提交只做一件事，便于回滚
4. 提交信息末尾签署 DCO：

```
Signed-off-by: Your Name <you@example.com>
```

用 `git commit -s` 自动添加。这表示你确认自己有权提交该内容，并同意以上许可条款。

5. 推送到你的 fork，向本仓库 `our-fork` 分支发起 PR，说明改动动机与验证方式。

## 与上游同步

```bash
git fetch origin
git log --oneline our-fork..origin/master   # 看上游新增
git merge origin/master                      # 或 rebase
```

冲突解决时**不要**为了合并方便改动 LICENSE / NOTICE.md 的许可划分。

## 代码约定

- Studio 后端改动后跑一次 `python3 -m compileall software/studio` 做语法自检
- 涉及系统权限的动词（如 `power`）必须带白名单、调用者记录和权限校验
- 涉及机器人运动的改动，PR 中写明急停可用性与断电后的安全状态
