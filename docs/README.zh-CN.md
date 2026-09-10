<div align="center">

<a href="https://build-arena.github.io/ConstructionChallenge/">
  <img src="./assets/challenge_title.svg" alt="BuildArena 2.0 — Construction Challenge" width="640" />
</a>

**从机器设计到自主执行。**

[Construction Challenge](https://build-arena.github.io/ConstructionChallenge/) · [BuildArena 1.0](https://build-arena.github.io/) · [English README](../README.md)

</div>

BuildArena 2.0 为 AI Agent 在
[Besiege](https://store.steampowered.com/app/346010/_/) 中提供端到端工程工作流：
**自动启动并运行游戏、机器建造、闭环控制、遥测与时间线回放**，并提供
Python 接口和 **MCP 工具**。借助
[BuildArena ToolKit](https://steamcommunity.com/sharedfiles/filedetails/?id=3795335349)
与 Besiege CLI，Agent 可以在仿真中建造机器、运行、观察行为并实施控制。

- **[Construction Challenge](https://build-arena.github.io/ConstructionChallenge/)**：赛道、评分、规则与提交。
- **[BuildArena 1.0 (ICML 2026)](https://build-arena.github.io/)**：原始基准、论文与项目页面。
- **[控制指南](../control/README.md)**：运行命令、控制器、遥测与回放。

## 三种自动控制案例

完成一键配置后，在仓库根目录运行，无需 PowerShell 启动脚本：

```powershell
# 重型火箭入轨、绕行一圈并返回着陆
uv run python control/examples/rocket_orbit_return/run.py
# 变形汽车行驶、起飞、空中特技和着陆续跑
uv run python control/examples/transforming_car_aerobatics/run.py
# 航天飞机轨道飞行、双助推器返回与滑翔回收
uv run python control/examples/shuttle_booster_recovery/run.py
```

每个目录只含一份 `machine.json` MCP 建造历史与 Python 启动/控制代码。
每次先从历史重建；航天飞机的限位和刀翼翻转在开始模拟前设置，不改变结构。
生成的 BSG、GUID 绑定和遥测等写入 `datacache/manual_cases/`，不会写进案例目录。
一键配置中的环绕任务也调用相同的 Python 准备和启动逻辑。
汽车脚本会先重新进入场景，清理残留的建造对象，再加载机器；此步骤在可选的 camera 编辑等待之前完成。

任一命令追加 `--edit-before-start --camera-bsg MyCameraMachine`，即可预载后添加 camera，
在游戏中另存为 `MyCameraMachine`，回到终端输入 `yes` 再自动运行。
下次去掉 `--edit-before-start`、保留 `--camera-bsg MyCameraMachine` 即可复用。
同一历史的重建 GUID 稳定，允许重复重建后复用 camera。用游戏镜头和 OBS 等自行录制，
不启用 CLI 屏幕录像或自动跟随。重建时显示 tqdm 进度条，完成后无倒计时或录制提示；控制器结束后留 20 个仿真秒。
可用 `--tail-seconds` 调整，或用 `--prepare-only` 仅离线重建。所有生成文件都保存在 Git 忽略的 `datacache` 内。

## 一键配置

**在 Windows 10 或 11 上，于仓库根目录用 PowerShell 运行：**

```powershell
uv run python scripts/setup.py
```

**尚未安装 [uv](https://docs.astral.sh/uv/)？** 使用包装脚本，它会安装 `uv`、运行 `uv sync`，
并启动同一套配置脚本：

```powershell
powershell -ExecutionPolicy ByPass -File scripts\setup.ps1
```

配置会自动完成全部设置，并在 Besiege 中运行两台机器测试。
坐下来看屏幕上的演示即可。

### 需要人工介入的情况

自动配置仅在以下三种预期情况下需要人工输入：

1. **通过 Steam 购买并安装游戏及两个 DLC：**
   [Besiege](https://store.steampowered.com/app/346010/_/)、
   [The Splintered Sea](https://store.steampowered.com/app/2165710/Besiege_The_Splintered_Sea/)
   和 [The Broken Beyond](https://store.steampowered.com/app/3639470/Besiege_The_Broken_Beyond/)。
2. **订阅 [BuildArena ToolKit](https://steamcommunity.com/sharedfiles/filedetails/?id=3795335349)
   并等待 Steam 下载。** 必须使用已发布的 Workshop 条目；
   没有本地模组回退路径。
3. **若 Besiege 不在默认 Steam 路径下，请提供游戏数据目录：**

   ```powershell
   uv run python scripts/setup.py --besiege-data "D:\SteamLibrary\steamapps\common\Besiege\Besiege_Data"
   ```

   使用包装脚本时，传入 `-BesiegeData "D:\SteamLibrary\steamapps\common\Besiege\Besiege_Data"`。
   请指向 **`Besiege_Data`**，而不是安装根目录。定位方法：
   **Steam → Besiege → Manage → Browse local files**。

补齐缺失前提后重新运行配置。脚本会在继续前检查真实产物。

## 诊断

```powershell
uv run python -m buildarena.paths
```

该命令会报告已配置与缺失的路径、ToolKit 安装情况以及配置状态。
验证结果见 `.local/setup-report.json`；成功配置会记录
`status=passed`。

## 手动配置（后备）

需要自行配置路径或排查某一阶段时，使用以下步骤。
Inspector 初始化与游戏内验收仍由配置脚本完成。

1. **准备 Windows 与 Steam。** 安装上文列出的游戏、两个 DLC 以及 ToolKit
   Workshop 条目。移除或关闭已退役的 Controller、Block
   Tracker、Collider Dumper 和 Block Inspector 模组，确保仅当前 ToolKit
   处于启用状态。
2. **安装 [uv](https://docs.astral.sh/uv/getting-started/installation/)，然后**
   在仓库根目录 **准备环境**：

   ```powershell
   uv sync
   Copy-Item .env.example .env
   ```

   仅在 `.env` 尚不存在时复制模板。
3. **在 `.env` 中设置本机路径**，按实际游戏位置调整：

   ```dotenv
   BESIEGE_DATA_PATH=C:\Program Files (x86)\Steam\steamapps\common\Besiege\Besiege_Data
   SAVED_MACHINE_DIR=C:\Program Files (x86)\Steam\steamapps\common\Besiege\Besiege_Data\SavedMachines\BuildArena
   COLLIDER_DUMP_PATH=.local/collider_dump.toml
   ```

   如有需要，请创建 `SavedMachines\BuildArena` 文件夹。相对路径
   以仓库根目录解析；碰撞数据会在下一步生成。
4. **在 Besiege 的 mod loader 中启用 ToolKit**，然后运行
   `uv run python scripts/setup.py` 以初始化 Inspector、采集产物，
   并验收控制链路。不要再使用已退役的点击方块流程或
   `block_preview.ipynb`。用上面的诊断命令进行验证。
5. **通过 MCP 连接 Agent**，配置见下文。

对于较旧的检出，配置脚本不会迁移遗留的生成配置。
重新运行前请清理过时的配置产物；保留 `datacache/` 以及
`.local/Machine/` 中的机器记录。

## 通过 MCP 连接

配置会生成带有本仓库绝对路径的 **`mcp.json`**。将其导入
Agent 的 MCP 配置，或使用 [`mcp.example.json`](../mcp.example.json)
并替换仓库路径：

```json
{
  "mcpServers": {
    "build-arena": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "C:\\Users\\you\\path\\to\\BuildArena-2-0",
        "python",
        "-m",
        "buildarena.mcp_server"
      ]
    }
  }
}
```

直接使用 CLI 及控制示例，见 [`control/README.md`](../control/README.md)：

```powershell
cd control
uv run python -m besiege_cli --help
```

## 挑战产物与提交

请保留以下三份必需的建造产物：

| 产物 | 用途 |
| --- | --- |
| 原始 `.bsg` 机器 | 可运行的机器。 |
| 有效操作历史 JSON | 产生有效结构更新的操作，用于重建机器。 |
| 全量操作历史 JSON | 完整历史，包含查询以及回滚/错误恢复轨迹。 |

游戏侧产物位于 `SAVED_MACHINE_DIR` 以及 `Besiege_Data/Mods/Data/` 下的
ToolKit 数据目录。建造记录也会保留在 `.local/Machine/`。

**请勿手动修改生成后的机器结构。** 结构改动会使
[Construction Challenge 提交](https://build-arena.github.io/ConstructionChallenge/)
失效。你可以载入、查看、驾驶机器，并调整控制参数。

若要恢复原始机器，从 `.local/Machine/` 还原记录，或从保存的操作历史重建：

```powershell
uv run python scripts/rebuild_from_record.py --record-json ".local/Machine/<machine>/<machine>.json"
```

重建会生成带新时间戳的机器输出。
