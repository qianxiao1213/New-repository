# 赛博朋克2077 郊狼控制器（Python 版）

## 项目结构

```
PythonProject3/
├── main.py                    # 主程序入口（UI + 游戏监控 + DGLab 控制）
├── main_window.ui             # Qt UI 布局文件
├── dglab_controller.py        # DGLab WebSocket 控制器（强度/波形发送）
├── config.py                  # 波形数据 + 默认配置
├── waveform_converter.py      # 波形数据格式转换工具
├── build.py                   # PyInstaller 打包脚本
├── CyberpunkDGLab.spec        # PyInstaller 打包配置
├── software_icon.ico          # 程序图标
├── game_paths.json            # 运行后自动生成（游戏文件路径配置）
│
├── dist/cy2077-DG-LAB/        # HealthMonitor Lua 脚本（需安装到 CET）
│   └── init.lua               # 监控生命值/体力，输出到 health_stamina_status.json
│
├── dist/PosLogger/            # PosLogger Lua 脚本（需安装到 CET，Rezo Agwe）
│   └── init.lua               # 输出玩家位置/状态/属性等到 ndjson 文件
│
└── dist/CyberpunkDGLab.exe    # 编译后的可执行程序
```

## 通信机制

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        赛博朋克2077 + CET 脚本                               │
│  ┌──────────────────────────┐        ┌─────────────────────────────────┐    │
│  │   HealthMonitor.lua      │        │   PosLogger.lua (Rezo Agwe)     │    │
│  │   - 读取生命值/体力       │        │   - 读取玩家位置/状态/属性       │    │
│  │   - 每秒写入 JSON 文件    │        │   - 每秒写入 NDJSON 文件         │    │
│  └──────────┬───────────────┘        └──────────────┬──────────────────┘    │
│             │                                        │                        │
│             ▼                                        ▼                        │
│  health_stamina_status.json          player_data_<timestamp>.ndjson          │
│  [时间戳] Health: 100.000, ...       {"spatial":{...,"district":"沃森区"},...} │
└─────────────────────────────────────────────────────────────────────────────┘
                          │                        │
                          │  轮询读取 (QTimer 1s)   │
                          ▼                        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    CyberpunkDGLab.exe (Python 主程序)                        │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                        GameStateMonitor                              │    │
│  │  ┌─────────────────┐          ┌────────────────────────────────┐    │    │
│  │  │ read_health_     │          │  read_pos_logger               │    │    │
│  │  │ stamina()        │          │  - 扫描 *.ndjson 文件          │    │    │
│  │  │ - 解析 JSON 文件  │          │  - 按 mtime 取最新文件          │    │    │
│  │  │ - 提取 Health     │          │  - 首次检查 >5s → 旧会话残留   │    │    │
│  │  │ - 提取 Stamina    │          │  - 读取最后一行 JSON            │    │    │
│  │  └────────┬─────────┘          │  - 提取 spatial.district       │    │    │
│  │           │                    └──────────────┬─────────────────┘    │    │
│  │           └──────────┬───────────────────────┘                       │    │
│  │                      ▼                                               │    │
│  │  ┌─────────────────────────────────────────────────────────────┐    │    │
│  │  │  get_status()                                               │    │    │
│  │  │  - 合并两路数据                                              │    │    │
│  │  │  - 计算 player_in_game (ndjson_active + district ≠ Unknown) │    │    │
│  │  │  - 输出 health/combat/dead/stamina/district/player_in_game  │    │    │
│  │  └──────────────────────────┬──────────────────────────────────┘    │    │
│  │                             │                                       │    │
│  │                             ▼                                       │    │
│  │  ┌─────────────────────────────────────────────────────────────┐    │    │
│  │  │  DGLabController.update_auto_target()                       │    │    │
│  │  │                                                             │    │    │
│  │  │  ① player_in_game = False? → 强度归零，不发送               │    │    │
│  │  │  ② is_dead / stamina=0?    → 死亡惩罚，强度拉满              │    │    │
│  │  │  ③ 正常游戏?               → 基础强度 + (1-血量%)×权重      │    │    │
│  │  └──────────────────────────┬──────────────────────────────────┘    │    │
│  │                             │                                       │    │
│  └─────────────────────────────┼───────────────────────────────────────┘    │
│                                │                                           │
│                                ▼                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  WebSocket 服务端 → 二维码 → DG-Lab APP 扫码连接                     │    │
│  │  强度发送循环 (0.2s) + 波形发送循环 (1s)                             │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
                                │
                                │ WebSocket 协议
                                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    DG-Lab APP → 蓝牙 → 郊狼设备                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 核心模块说明

### 1. 主程序入口 [main.py](file:///e:/daima11111111111/PythonProject3/main.py)

| 类/方法 | 功能 |
|---------|------|
| `PathConfig` | 管理游戏文件路径配置（持久化到 game_paths.json） |
| `GameStateMonitor` | 监控游戏状态：读取 health JSON + ndjson 玩家数据 |
| `CyberpunkDGLabApp` | Qt 主窗口，UI 控制，定时器刷新，DGLab 控制协调 |

### 2. 进入游戏判定逻辑（ndjson 文件 mtime + district）

**核心原则**：以 PosLogger.lua 输出的 ndjson 文件修改时间和 `spatial.district` 字段作为"是否进入游戏世界"的唯一判定依据。

```python
# main.py GameStateMonitor
ndjson_active = now - latest_ndjson_mtime <= 1.0       # 文件在最近1秒内写入
player_in_game = ndjson_active and district有效 and district != "Unknown"
```

| 场景 | ndjson 状态 | district | player_in_game | 强度行为 |
|------|------------|----------|----------------|---------|
| 软件刚启动，游戏未开 | 无 ndjson 文件 | — | False | 归零 |
| 软件启动，有旧文件(>5s) | 视为旧会话残留 | 不读取 | False | 归零 |
| 游戏加载界面/主菜单 | 无写入（文件 inactive） | — | False | 归零 |
| 游戏中正常游玩 | 每秒写入，active | "沃森区"等 | True | 正常计算 |
| 游戏中死亡 | 仍在写入，active | "沃森区" | True | 死亡惩罚 |
| 退出到主菜单 | 停止写入 → inactive | — | False | 归零 |

### 3. DGLab 控制器 [dglab_controller.py](file:///e:/daima11111111111/PythonProject3/dglab_controller.py)

```python
class DGLabController:
    # 强度计算流程（优先级从高到低）
    update_auto_target(health_percent, is_combat, is_dead, stamina, config, player_in_game)

    ① player_in_game == False → auto_target = 0           # 未进入游戏
    ② is_dead or stamina <= 0 → auto_target = 99          # 死亡惩罚（可配置时长）
    ③ 死亡持续期内            → 保持高强度                  # 死亡惩罚持续
    ④ 正常游戏                → base + (1-health%)*weight  # 动态强度
```

### 4. CET Lua 脚本

#### HealthMonitor ([dist/cy2077-DG-LAB/init.lua](file:///e:/daima11111111111/PythonProject3/dist/cy2077-DG-LAB/init.lua))
- 每秒读取玩家 Health 和 Stamina
- 追加写入 `health_stamina_status.json`
- 格式：`[2026-04-25 12:00:00] Health: 100.000, Stamina: 100.000`

#### PosLogger ([dist/PosLogger/init.lua](file:///e:/daima11111111111/PythonProject3/dist/PosLogger/init.lua))
- 每秒输出完整玩家状态到 `player_data_<timestamp>.ndjson`
- 包含：spatial（位置/区域）、status（战斗/死亡）、attributes、proficiency、economy、narrative 等
- **关键字段**：`spatial.district` — 标记当前区域，`"Unknown"` 表示未进入游戏世界

## 关键特性

- **ndjson 文件活性检测**：依赖文件修改时间判断玩家是否在游戏中，而非 health 值
- **旧会话残留过滤**：启动时检测 ndjson 文件 mtime，超过 5 秒视为旧数据跳过
- **district 区域判定**：仅当 spatial.district 为有效区域名时才认为玩家已进入游戏世界
- **双重数据源**：health JSON 用于强度计算，ndjson 用于状态判定，互不干扰
- **可配置强度**：基础强度（战斗/非战斗）、生命值权重、通道模式（A/B/AB）
- **波形发送**：支持多种波形模式，定时轮换

## 使用流程

1. 将 `dist/cy2077-DG-LAB/init.lua` 安装到 CET（Cyber Engine Tweaks）脚本目录
2. 将 `dist/PosLogger/init.lua` 安装到 CET 脚本目录
3. 启动赛博朋克 2077，确认两个 Lua 脚本正常运行
4. 运行 `CyberpunkDGLab.exe`（或 `python main.py`）
5. 在软件中配置 HealthMonitor JSON 文件和 PosLogger 目录路径
6. 点击连接，用 DG-Lab APP 扫描二维码
7. 进入游戏，强度自动根据游戏状态动态调整
