# BlackHost 免费服保活

## 窗口对齐强制重启

当前到期 `12:28:45Z`、剩余 3h15m，续期窗口在剩不到 2h 时开启（`10:28:45Z`，还有 74 分钟）。

Actions 定时为 `28 */6 * * *`（UTC :28 每 6 小时），每次落在窗口开点上：

- PAT 读到期 → 停服到 `stopped` → 开机到 `running`
- 窗口内续期 +8h 下一个窗口就是 +6h，所以不是 +8h

手动跑一次：

```bash
BH_PAT=bh_pat_xxx PYTHONUTF8=1 python3 github-action/power_cycle.py
```

通过标准：日志出现 `✅ 已停`、`✅ 已运行`，最后 `status=ready`。

## GitHub Actions 用法

工作流位于 `.github/workflows/blackhost-keepalive.yml`，定时执行：

```yaml
- cron: "28 */6 * * *"
```

仓库 `Settings → Secrets and variables → Actions` 配置：

| Secret | 内容 |
|--------|------|
| `BH_PAT` | BlackHost PAT（`bh_pat_...`） |
| `BH_TG_TOKEN` / `BH_TG_CHAT` | Telegram 通知（可选） |

详细说明见 [github-action/README.md](./github-action/README.md)。

## 注意事项

- 停止/开始不会延长到期时间；真正延期要靠 Turnstile 那步。到期前 2 小时内如果重启后仍临近到期，建议手动走一次浏览器续期。
- 不要把真实 PAT、Cookie、sing-box 凭证提交到仓库。
