# Optimization rounds / 优化记录

GOO Bridge 0.1.0 was hardened in ten small, independently testable rounds.
Each round has its own commit and was exercised by the local suite and the
cross-platform CI workflow.

GOO Bridge 0.1.0 通过十轮可独立验证的小改动完成加固。每轮都有单独提交，并
经过本地测试与三平台 CI。

| Round | Change | Commit |
|---|---|---|
| 1 | Cache Blender 5.2.3 downloads in CI; cache hits verified on all three operating systems | `f69255f` |
| 2 | Add a Python 3.11 CI job to verify the declared minimum version | `78c5cfe` |
| 3 | Write `config.json` through a temporary file and atomic replace | `c4a7caf` |
| 4 | Write JSON/HTML analysis reports atomically | `b7aa0b1` |
| 5 | Hash external assets with bounded parallelism for large scenes | `e14a7db` |
| 6 | Write compatibility JSON/HTML reports atomically | `4687566` |
| 7 | Write render-job files atomically | `3b8e542` |
| 8 | Cancel superseded CI runs with a concurrency group | `f8cdc2b` |
| 9 | Write the remote bundle asset manifest atomically | `a22c218` |
| 10 | Validate numeric config fields; corrupt ports/versions fall back safely | `97463f4` |

## Why these changes

- **Performance:** Blender downloads are reused across runs; large asset sets
  are hashed with a bounded thread pool; stale CI runs are cancelled.
- **Reliability:** configuration, reports, render jobs and remote bundle
  manifests survive a crash or full disk without leaving a truncated file.
- **Compatibility:** the Python 3.11 job keeps the declared floor honest.
- **Robustness:** malformed numeric fields in `config.json` no longer crash the
  CLI; invalid ports fall back to 22.

## Verification

Local suite (Blender-bundled Python 3.13.13):

```text
Ran 96 tests in 19.558s

OK (skipped=2)
```

CI run [45052199623](https://github.com/Toobe53/goo-bridge/actions/runs/45052199623)
(head `97463f4`): all seven jobs passed. Unit jobs ran 94 tests; each Blender
job ran the full 96-test suite. The Blender jobs also restore the cached 5.2.3
download on Windows, Linux and macOS.

CI 运行 [45052199623](https://github.com/Toobe53/goo-bridge/actions/runs/45052199623)
（`97463f4`）七个 job 全部通过：单元任务 94 项，Blender 任务完整 96 项；三个
平台的 Blender 下载缓存均成功命中。

## Compatibility note

The optimizations do not change the CLI surface or the 39-item matrix
(**13 L2 / 15 L1 / 11 L0**). They are internal quality improvements and can be
reviewed independently through the commits above.
