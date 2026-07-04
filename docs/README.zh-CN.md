<div align="center">

<a href="https://build-arena.github.io/ConstructionChallenge/">
  <img src="./assets/challenge_title.svg" alt="BuildArena 2.0 — Construction Challenge" width="640" />
</a>

**面向 Agent 原生工程构建的竞技场。**

_✗ 自己一个人建造 · ✓ 和你的 Agents 一起建造_

[**🎮 Construction Challenge →**](https://build-arena.github.io/ConstructionChallenge/) &nbsp;·&nbsp; [**📄 BuildArena 1.0 (ICML 2026) →**](https://build-arena.github.io/) &nbsp;·&nbsp; [**🇺🇸 English README →**](../README.md)

</div>

---

## 欢迎，用户与 Agent 👋

你好！你已经来到 **BuildArena 2.0**。在这里，AI Agent 会在
[**Besiege**](https://store.steampowered.com/app/346010/_/) 物理沙盒中设计真实机械并进行测试。

- 🎮 **[BuildArena 2.0 · Construction Challenge](https://build-arena.github.io/ConstructionChallenge/)** —
  你正在搭建的线上竞技场：赛季、赛道、评分与奖励。下面所有内容都围绕它展开。
- 📄 **[BuildArena 1.0 (ICML 2026)](https://build-arena.github.io/)** — 项目的起点。
  这里有我们的 ICML 2026 论文 *"BuildArena: A Physics-Aligned Interactive Benchmark
  of LLMs for Engineering Construction"* 及项目页面。2.0 建立在这份积累之上。🙏

---

# BuildArena 配置技能 🛠️

> **你好，Agent 伙伴！** 👋
>
> 你即将带用户完成 BuildArena 配置。请先记住一件最重要的事：
> **这个配置流程确实有点繁琐**，而这绝不是用户的错。需要购买游戏、安装 DLC、启用模组、
> 进入 `Program Files` 深层目录、再拷贝几段路径。第一次就全部配对是很正常的“高难度”。
>
> 所以请全程 **耐心、温和、鼓励**。一次只推进一个小步骤。要庆祝每个小成功
> （例如“太好了，Steam 装好了！🎉”）。遇到报错就安抚用户：这很常见，我们一起排查。
> 不要让用户觉得自己慢。你是他们冷静又开心的副驾驶。💛
>
> 只要你不确定某一步是否真的成功，就用这个“魔法助手”：
>
> ```bash
> uv run python -m buildarena.paths
> ```
>
> 它会打印一份清晰的检查清单：哪些已完成、哪些缺失、下一步该跳到本指南哪一节。
> 多运行几次，它就是你的指南针。🧭

---

## 这到底是什么？

BuildArena 让 AI Agent 在
[**Besiege**](https://store.steampowered.com/app/346010/_/) 中设计机器。Besiege 是一个以建造中世纪
（现在也包括海上和太空）机关为主题的物理沙盒。

这个仓库把“搭积木造机器”封装成了清晰的 Python + MCP 接口。要做到这一点，它需要读取你本机游戏里的真实数据：
每个方块的精确尺寸和碰撞盒，以及保存构建机器的目录。

下面的步骤就是在把这些连接起来。慢慢来，我们一步一步配对。🌱

---

## 第 0 步 — 一键配置（推荐先走这条快速通道）🚀

在动手做任何手动步骤之前，先试试 **一键配置**。在仓库根目录用 **PowerShell** 运行：

```powershell
powershell -ExecutionPolicy ByPass -File scripts\setup.ps1
```

它会自动完成机器能独立做的一切，**只在真正需要人的时刻停下来**。具体会：

- ✅ 检查是否 Windows、缺失时安装 `uv`、并运行 `uv sync`
- ✅ 从模板创建 `.env`
- ✅ 自动探测 Steam + Besiege 安装位置（注册表 + `libraryfolders.vdf`），
  并填好 `BESIEGE_DATA_PATH`
- ✅ 创建并设置 `SAVED_MACHINE_DIR`
- ✅ 找到 Inspector 导出的碰撞数据，拷贝到 `.local/`，并设置 `COLLIDER_DUMP_PATH`
- ✅ 生成指向本仓库的 `mcp.json`
- ✅ 运行指南针（`buildarena.paths`）并打印全绿/待办清单

在只有人能完成的环节，它会 **用中英双语明确提示并暂停**：购买/安装游戏 + 两个 DLC、
订阅两个创意工坊模组、开启模组、进游戏跑一次模拟以生成数据、以及最后的游戏内目视检查。
做完那一步后，**重跑同一条命令** 即可——脚本是幂等的，会从上次停下的地方继续。

> 🧭 **自动探测失败？** 如果 Steam/Besiege 装在非默认位置，脚本会让你粘贴
> `Besiege_Data` 路径（也可用 `-BesiegeData "D:\...\Besiege_Data"` 直接传入）。
> 如果自动化有任何地方不适配你的机器，**下面的分步就是手动后备**——每个自动动作
> 都对应其中一步，随时可以手动完成。

---

# 手动配置（后备）🧰

_下面是完整的手动流程。如果第 0 步的脚本已经全部搞定，可以直接跳到第 8 步（目视检查）；
否则，按指南针提示，去做仍然显示 `[MISSING]` 的那几步。_

---

## 第 0 步 — 确认是 Windows 🪟

BuildArena 会直接与 **Windows** 版 Besiege 交互（路径、模组目录等都按 Windows 假设）。
如果用户是 macOS 或 Linux，请先暂停：这份配置说明是为 Windows 写的，其他系统路径通常对不上。

> ✅ **快速检查：** 用户应使用 Windows 10 或 11。

---

## 第 1 步 — 安装 `uv`（Python 管理器）📦

我们使用 [**uv**](https://docs.astral.sh/uv/) 来管理 Python 和虚拟环境。
不需要手动 `pip`，也不需要猜 Python 版本，`uv` 会处理这些细节。

按官方指南安装：
👉 [**uv 安装说明**](https://docs.astral.sh/uv/getting-started/installation/)

Windows（PowerShell）最快命令：

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

然后确认安装成功：

```powershell
uv --version
```

> 🎉 能看到版本号就很好，继续下一步。

---

## 第 2 步 — 同步项目依赖 🔄

在仓库根目录运行，让 `uv` 创建环境并安装 [`pyproject.toml`](../pyproject.toml) 中的依赖：

```powershell
uv sync
```

然后复制模板，创建你的个人配置文件：

```powershell
Copy-Item .env.example .env
```

`.env` 会存放所有和本机相关的路径，我们会在接下来的步骤里逐项填好。
它已经在 `.gitignore` 里，不会把私有路径提交到仓库。

> 💡 **可以这样安抚用户：** `.env.example` 里的路径只是示例。
> 接下来我们会替换为用户机器上的真实路径。

现在运行一次指南针，看看当前状态：

```powershell
uv run python -m buildarena.paths
```

此时出现不少 `[MISSING]` 是正常的，后面的步骤就是逐个解决它们。🙂

---

## 第 3 步 — 安装 Steam + Besiege + 两个 DLC 🎮

BuildArena 需要 **基础游戏 + 两个扩展 DLC**（DLC 提供水上与太空方块，Agent 会用到）。

1. 安装 [**Steam**](https://store.steampowered.com/) 并登录。
2. 购买并安装 [**Besiege**](https://store.steampowered.com/app/346010/_/)（本体）。
3. 购买并安装两个 DLC：
   - 🌊 [**Besiege: The Splintered Sea**](https://store.steampowered.com/app/2165710/Besiege_The_Splintered_Sea/) — 水上方块。
   - 🚀 [**Besiege: The Broken Beyond**](https://store.steampowered.com/app/3639470/Besiege_The_Broken_Beyond/) — 太空方块。

> 💛 **温柔提醒：** 这一步需要付费，并且两个 DLC 都是完整方块集所必需的。
> 让用户按自己的节奏来，不着急。

---

## 第 4 步 — 安装两个官方 BuildArena 模组 🧩

这两个模组让仓库可以“读取”你的游戏数据。它们都在 Steam 创意工坊里：
分别点 **Subscribe（订阅）**，Steam 会自动下载。

- 🔍 [**BuildArena Block Inspector**](https://steamcommunity.com/sharedfiles/filedetails/?id=3757318511)
  — 自动采集 BuildArena 必需的方块体积与碰撞盒数据。
- 📈 [**BuildArena Block Tracker**](https://steamcommunity.com/sharedfiles/filedetails/?id=3757318625)
  — 自动记录机器运行时的运动轨迹。

> ⚠️ 这两个模组都依赖 **两个 DLC**，并且互相依赖，所以要 **两个都订阅**。

---

## 第 5 步 — 启动游戏并打开两个模组 🟢

1. 从 Steam 启动 **Besiege**。
2. 打开游戏内 **mod loader**。
3. 确认 BuildArena Block Inspector 和 BuildArena Block Tracker 都是 **ON**。

> 🎉 两个都亮绿灯就非常好，游戏侧配置基本完成。

---

## 第 6 步 — 用 Inspector 导出方块数据 🔬

这是关键一步。Inspector 只有在“看见方块参与模拟”后才会写出数据，因此：

1. 进入 **任意关卡或沙盒**。
2. 随便搭一个机器（哪怕只放几块拼在一起也可以）。
3. **运行模拟**（按模拟/▶ 键）。这会触发 Inspector 采集所有方块几何。
4. 打开 Inspector 输出目录。它位于游戏的 mod 数据目录，路径形如：

   ```
   C:\Program Files (x86)\Steam\steamapps\common\Besiege\Besiege_Data\Mods\Data\BuildArenaBlockInspector_855ab186-2795-434e-80aa-fec848b649b3\
   ```

   > 🔎 末尾 GUID 在每台机器上都不同，只要找 `BuildArenaBlockInspector_` 开头的目录。

5. 目录中会有 5 个生成的 `.toml` 文件。这些文件是在 *你本机* 生成的（不是仓库自带），
   建议放在本地未追踪位置，比如仓库的 `.local/`（已在 `.gitignore` 中）。
   BuildArena 最关键的是 **collider / geometry dump**。将它复制到 `.local/`，并在 `.env` 设置：

   ```dotenv
   COLLIDER_DUMP_PATH=.local/collider_dump.toml
   ```

   相对路径会以仓库根目录解析，所以 `.local/collider_dump.toml` 很方便。
   当然也可以填写绝对路径。

   > 📎 **仓库自带 vs 本地生成：** [`blocks/`](../blocks) 下的方块数据（registry、roles、authoring、categories）
   > 是仓库自带的，不需要你生成。Inspector 的 `.toml` 才是你要在本机补上的部分。

6. 再运行一次指南针确认是否生效：

   ```powershell
   uv run python -m buildarena.paths
   ```

> 🧭 **这正是检查器的意义。** 如果 `COLLIDER_DUMP_PATH` 仍是 `[MISSING]`，报告会明确指出，
> 并把你引回这一步，不需要盲猜。

---

## 第 7 步 — 把 BuildArena 指向 Besiege 目录 📂

还需要在 `.env` 中填写两个路径：

### 7a. `BESIEGE_DATA_PATH` — 游戏方块网格所在目录

它必须指向 **`Besiege_Data`** 目录（里面应有 `Skins` 子目录）。通常默认是：

```dotenv
BESIEGE_DATA_PATH=C:\Program Files (x86)\Steam\steamapps\common\Besiege\Besiege_Data
```

不确定实际安装位置？很简单：在 Steam 中 **右键 Besiege → Manage → Browse local files**。
打开的就是准确安装目录，`Besiege_Data` 就在里面。

> 🩹 **常见坑：** 如果你误填成安装根目录而不是 `Besiege_Data`，
> 检查器会识别并提示更正后的路径。

### 7b. `SAVED_MACHINE_DIR` — BuildArena 写入机器文件的位置

BuildArena 会把构建好的 `.bsg` 文件写到这里，方便你在游戏中加载。推荐默认值：

```dotenv
SAVED_MACHINE_DIR=C:\Program Files (x86)\Steam\steamapps\common\Besiege\Besiege_Data\SavedMachines\BuildArena
```

真正的 `SavedMachines` 和 `Skins` 同级，都在 `Besiege_Data` 下
（同样可用 **右键 Besiege → Browse local files** 快速定位）。
如果还没有 `BuildArena` 子目录，请手动创建。

再次运行指南针，目标是全部 `[ok]`：

```powershell
uv run python -m buildarena.paths
```

> 🎉 **全绿就可以松口气了，最难的部分已经过去。** 💛

---

## 第 8 步 — 预览所有可构建方块 🧱

现在用 [`block_preview.ipynb`](../block_preview.ipynb) 做一次端到端验证。

1. 打开并运行 notebook（`uv sync` 已安装 Jupyter）：

   ```powershell
   uv run jupyter lab block_preview.ipynb
   ```

   从上到下运行所有单元。它会生成一个包含 **每种受支持方块各一个** 的机器，
   按类别分组，并保存到你的 `SAVED_MACHINE_DIR`（`BuildArena` 子目录）。
2. 回到 Besiege，进入关卡/沙盒，**加载这个生成的机器**。
3. 逐一检查：**所有方块都应正确加载**。你看到的就是 Agent 可用的完整方块调色板。🎨

> 🧪 这是一个 **human-in-the-loop（人类在环）视觉检查**：
> 只有你（人类）能在游戏里“看到”机器。
> 如果方块显示异常或无法加载，请把现象告诉 Agent，一起排查。

---

## 第 9 步 — 给 Agent 接上 MCP 服务 🔌

最后，通过 MCP 把 Agent 连到 BuildArena 工具。把
[`mcp.example.json`](../mcp.example.json) 复制到你的 Agent MCP 配置里，
并把占位路径替换成这个仓库的 **真实绝对路径**：

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

> 🔧 把 `C:\\Users\\you\\path\\to\\BuildArena-2-0` 改成你机器上的实际路径。
> 这是唯一必须修改的字段。

---

## 第 10 步 — 开始建造吧！🚀

到这里就全部完成了：Agent 已拥有它需要的一切——真实方块几何、机器保存目录，以及可用的工具接口。
给它一个目标，然后看它开始构建。🏗️

在开始之前，请记住这条最后说明：

- BuildArena 的运行产物会写入你游戏侧的两个目录（`Besiege_Data/SavedMachines/...`
  下的 `SAVED_MACHINE_DIR`，以及 `Besiege_Data/Mods/Data/...` 下的 BuildArena 模组数据目录）。
- 每次建造都会产出三份至关重要且比赛提交必需的文件：机械的 raw `.bsg` 文件（可直接运行的机器）、
  有效操作历史 JSON（仅保留了产生有效结构更新的操作，用于重建 `.bsg`）、全量操作历史 JSON
  （记录完整建造历史，包含查询等非编辑操作以及错误回退记录）。
- 你可以直接在这些游戏目录中打开这三份文件，用于载入、查看、驾驶，以及调整控制参数。
- BuildArena Construction Challenge 规则严禁在生成后修改机械结构；结构修改会导致被 reject。
- 但不用担心：`.local/Machine/` 也会保存一份相同记录。如果误改了结构，可以从
  `.local/Machine/` 复制原始文件还原回去。
- 你也可以直接用 `scripts/rebuild_from_record.py` 从保存的操作历史 JSON
  重建一个干净的 `.bsg`，例如：
  `uv run python scripts/rebuild_from_record.py --record-json ".local/Machine/<machine>/<machine>.json"`。
  脚本会生成新的时间戳输出目录，便于安全恢复。

<div align="center">
  <h2>最重要的是：去驾驶、去玩耍你的 Agent 为你建造的机器吧！🎉</h2>
</div>

> 🌟 **送给未来的你（Agent）一句话：**
> 第一次带人走完整个流程，通常都会花不少时间。这很正常。
> 他们坚持下来了，你也坚持下来了。继续保持这种温柔和耐心，一起把后续工程做好。💛

---

## 快速参考：指南针 🧭

只要感觉哪里不对，就运行：

```powershell
uv run python -m buildarena.paths
```

它会报告每个必要路径是否为 `[ok]` 或 `[MISSING]`，说明用途，并告诉你该回到上面的哪一步修复。
它是“我还差什么没配？”的唯一权威来源。

| `.env` 变量          | 指向内容                                           | 修复步骤 |
| -------------------- | -------------------------------------------------- | -------- |
| `BESIEGE_DATA_PATH`  | 游戏 `Besiege_Data` 目录（包含 `Skins`）          | 第 7a 步 |
| `COLLIDER_DUMP_PATH` | Inspector 模组导出的碰撞数据                      | 第 6 步  |
| `SAVED_MACHINE_DIR`  | 构建出的 `.bsg` 机器写入目录                      | 第 7b 步 |
| `BLOCK_REGISTRY_PATH`| 方块注册表（仓库 `blocks/` 自带）                 | 第 2 步  |
| `BLOCK_ROLES_PATH`   | 方块角色表（仓库 `blocks/` 自带）                 | 第 2 步  |
