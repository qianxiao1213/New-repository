import os
import sys
import time
import threading
import asyncio
import json
import glob
import re
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QLabel, QProgressBar, QTextEdit,
    QCheckBox, QComboBox, QLineEdit, QPushButton, QMessageBox, QRadioButton, QFileDialog
)
from PySide6.QtCore import QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QDoubleSpinBox
from pydglab_ws import Channel
from pydglab_ws.server import DGLabWSServer

# 导入模块
from dglab_controller import DGLabController
from config import PULSE_DATA, CURRENT_WAVEFORM_A, CURRENT_WAVEFORM_B


class PathConfig:
    """路径配置管理"""
    def __init__(self):
        self.config_file = "game_paths.json"
        self.pos_logger_dir = ""
        self.health_stamina_path = ""
        self.load()

    def load(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    self.pos_logger_dir = config.get('pos_logger_dir', '')
                    self.health_stamina_path = config.get('health_stamina_path', '')
                    if self.pos_logger_dir and not os.path.exists(self.pos_logger_dir):
                        self.pos_logger_dir = ""
                    if self.health_stamina_path and not os.path.exists(self.health_stamina_path):
                        self.health_stamina_path = ""
            except:
                pass

    def save(self):
        try:
            config = {
                'pos_logger_dir': self.pos_logger_dir,
                'health_stamina_path': self.health_stamina_path
            }
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2)
            return True
        except:
            return False

    def set_pos_logger_dir(self, path):
        if path and os.path.exists(path):
            self.pos_logger_dir = path
            self.save()
            return True
        return False

    def set_health_stamina_path(self, path):
        if path and os.path.exists(path):
            self.health_stamina_path = path
            self.save()
            return True
        return False


class GameStateMonitor:
    """游戏状态监控"""
    def __init__(self, path_config):
        self.path_config = path_config
        self.last_health = 100.0
        self.last_stamina = 100.0
        self.last_pos_data = {}
        self.latest_ndjson_path = None
        self.latest_ndjson_mtime = 0.0
        self.ndjson_first_check_done = False

    def read_health_stamina(self):
        if not self.path_config.health_stamina_path:
            return self.last_health, self.last_stamina
        try:
            with open(self.path_config.health_stamina_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                if not lines:
                    return self.last_health, self.last_stamina
                last_line = lines[-1].strip()
                match = re.search(r'Health:\s*(\d+(?:\.\d+)?),\s*Stamina:\s*(\d+(?:\.\d+)?)', last_line)
                if match:
                    self.last_health = float(match.group(1))
                    self.last_stamina = float(match.group(2))
        except:
            pass
        return self.last_health, self.last_stamina

    def read_pos_logger(self):
        if not self.path_config.pos_logger_dir:
            self.latest_ndjson_mtime = 0.0
            self.last_pos_data = {}
            return {}

        try:
            files = glob.glob(os.path.join(self.path_config.pos_logger_dir, "*.ndjson"))
            if not files:
                self.latest_ndjson_path = None
                self.latest_ndjson_mtime = 0.0
                self.last_pos_data = {}
                return {}

            latest = max(files, key=os.path.getmtime)
            mtime = os.path.getmtime(latest)
            now = time.time()

            # 首次检查：若文件修改时间超过5秒，视为旧会话残留数据
            if not self.ndjson_first_check_done:
                self.ndjson_first_check_done = True
                if now - mtime > 5.0:
                    self.latest_ndjson_path = latest
                    self.latest_ndjson_mtime = mtime
                    self.last_pos_data = {}
                    print(f"⏰ [ndjson] 文件超过5秒未更新 ({(now - mtime):.1f}s)，视为旧会话残留")
                    return {}

            # 文件有新写入时更新数据
            if latest != self.latest_ndjson_path or mtime != self.latest_ndjson_mtime:
                self.latest_ndjson_path = latest
                self.latest_ndjson_mtime = mtime
                with open(latest, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                    if lines:
                        last_line = lines[-1].strip()
                        if last_line:
                            self.last_pos_data = json.loads(last_line)
        except:
            pass

        return self.last_pos_data

    def get_status(self):
        health_percent_raw, stamina = self.read_health_stamina()
        pos = self.read_pos_logger()
        status = pos.get('status', {})
        is_combat = status.get('isCombat', False)
        is_dead = status.get('isDead', False)
        max_health = status.get('health', 495)
        health_percent = health_percent_raw / 100.0
        health_current = health_percent * max_health

        spatial = pos.get('spatial', {})
        district = spatial.get('district', '')
        now = time.time()
        ndjson_active = bool(self.latest_ndjson_mtime > 0 and now - self.latest_ndjson_mtime <= 1.0)
        player_in_game = ndjson_active and bool(district) and district != 'Unknown'

        return {
            'health_percent': health_percent,
            'health_current': health_current,
            'stamina': stamina,
            'is_combat': is_combat,
            'is_dead': is_dead,
            'max_health': max_health,
            'pos_data': pos,
            'district': district,
            'ndjson_active': ndjson_active,
            'player_in_game': player_in_game
        }


class CyberpunkDGLabApp(QMainWindow):
    def __init__(self):
        super().__init__()

        # 获取当前脚本所在目录
        current_dir = os.path.dirname(os.path.abspath(__file__))
        ui_file_path = os.path.join(current_dir, 'main_window.ui')

        if not os.path.exists(ui_file_path):
            print(f"❌ 错误: 找不到 UI 文件 {ui_file_path}")
            return

        # 加载 UI 文件
        loader = QUiLoader()
        self.ui = loader.load(ui_file_path)
        if self.ui is None:
            print(f"❌ 错误: 无法加载 UI 文件")
            return

        # ========== 添加窗口图标 ==========
        icon_path = os.path.join(current_dir, 'software_icon.ico')
        if os.path.exists(icon_path):
            from PySide6.QtGui import QIcon
            self.ui.setWindowIcon(QIcon(icon_path))
            print("✅ 窗口图标已设置")
        else:
            print("⚠️ 未找到图标文件 software_icon.ico")
        # =================================

        self.ui.show()

        # 初始化路径配置
        self.path_config = PathConfig()

        # 获取所有控件
        self.get_widgets()

        # 显示已保存的路径
        self.load_saved_paths()

        # 初始化游戏监控
        self.monitor = GameStateMonitor(self.path_config)

        # 服务器相关
        self.server_port = 5678
        self.server_running = False

        # 初始化 DGLab 控制器
        self.dglab = DGLabController()
        self.dglab_thread = None
        self.dglab_loop = None

        # 连接信号槽
        self.connect_signals()

        # 初始化下拉框
        self.init_comboboxes()

        # 设置默认值
        self.set_default_values()

        # 自动启动服务器
        self.start_server()

        # 启动 DGLab 线程
        self.start_dglab_thread()

        # 启动定时器
        self.start_timers()

        print("✅ 程序启动完成")

    def load_saved_paths(self):
        if self.pos_logger_path_edit:
            self.pos_logger_path_edit.setText(self.path_config.pos_logger_dir)
        if self.health_stamina_path_edit:
            self.health_stamina_path_edit.setText(self.path_config.health_stamina_path)

    def start_server(self):
        def run_server():
            asyncio.run(self._async_server())
        self.server_thread = threading.Thread(target=run_server, daemon=True)
        self.server_thread.start()
        self.server_running = True
        print(f"✅ 服务器已启动，端口: {self.server_port}")

    async def _async_server(self):
        try:
            async with DGLabWSServer("0.0.0.0", self.server_port, 60) as server:
                print(f"服务器启动在端口 {self.server_port}")
                while self.server_running:
                    await asyncio.sleep(5)
        except Exception as e:
            print(f"服务器错误: {e}")
        finally:
            self.server_running = False

    def select_pos_logger_dir(self):
        folder = QFileDialog.getExistingDirectory(
            self.ui, "选择 PosLogger 文件夹",
            os.path.expanduser("~")
        )
        if folder:
            if glob.glob(os.path.join(folder, "*.ndjson")) or glob.glob(os.path.join(folder, "*.json")):
                self.path_config.set_pos_logger_dir(folder)
                self.pos_logger_path_edit.setText(folder)
                self.monitor = GameStateMonitor(self.path_config)
                print(f"✅ PosLogger 目录已设置: {folder}")
            else:
                QMessageBox.warning(self.ui, "警告", "选择的文件夹中没有找到 JSON 数据文件")

    def select_health_stamina_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self.ui, "选择 health_stamina_status.json 文件",
            os.path.expanduser("~"),
            "JSON文件 (*.json)"
        )
        if file_path:
            self.path_config.set_health_stamina_path(file_path)
            self.health_stamina_path_edit.setText(file_path)
            self.monitor = GameStateMonitor(self.path_config)
            print(f"✅ 生命值文件已设置: {file_path}")

    def set_default_values(self):
        if self.combat_base_spin and self.combat_base_spin.value() == 0:
            self.combat_base_spin.setValue(5)
        if self.idle_base_spin and self.idle_base_spin.value() == 0:
            self.idle_base_spin.setValue(5)
        if self.death_duration_spin and self.death_duration_spin.value() == 0:
            self.death_duration_spin.setValue(3)
        if self.debug_check:
            self.debug_check.setChecked(True)
        if self.monitor_life_check:
            self.monitor_life_check.setChecked(True)
        if self.health_weight_spin and self.health_weight_spin.value() == 0:
            self.health_weight_spin.setValue(100)
        if self.radio_ab:
            self.radio_ab.setChecked(True)

    def get_widgets(self):
        # 游戏监控页
        self.health_bar = self.ui.findChild(QProgressBar, 'healthBar')
        self.health_label = self.ui.findChild(QLabel, 'healthLabel')
        self.combat_label = self.ui.findChild(QLabel, 'combatLabel')
        self.pos_label = self.ui.findChild(QLabel, 'posLabel')
        self.quest_title = self.ui.findChild(QLabel, 'questTitle')
        self.quest_objective = self.ui.findChild(QLabel, 'questObjective')
        self.attr_text = self.ui.findChild(QTextEdit, 'attrText')
        self.money_label = self.ui.findChild(QLabel, 'moneyLabel')

        # 路径设置控件
        self.pos_logger_path_edit = self.ui.findChild(QLineEdit, 'posLoggerPath')
        self.health_stamina_path_edit = self.ui.findChild(QLineEdit, 'healthStaminaPath')
        self.btn_select_pos = self.ui.findChild(QPushButton, 'btnSelectPosLogger')
        self.btn_select_health = self.ui.findChild(QPushButton, 'btnSelectHealthStamina')

        # DGLab 控制页
        self.debug_check = self.ui.findChild(QCheckBox, 'debugCheck')
        self.monitor_life_check = self.ui.findChild(QCheckBox, 'monitorLifeCheck')
        self.combat_base_spin = self.ui.findChild(QDoubleSpinBox, 'combatBaseSpin')
        self.idle_base_spin = self.ui.findChild(QDoubleSpinBox, 'idleBaseSpin')
        self.death_duration_spin = self.ui.findChild(QDoubleSpinBox, 'deathDurationSpin')
        self.wave_a_combo = self.ui.findChild(QComboBox, 'waveACombo')
        self.wave_b_combo = self.ui.findChild(QComboBox, 'waveBCombo')
        self.offset_a_label = self.ui.findChild(QLabel, 'offsetALabel')
        self.offset_b_label = self.ui.findChild(QLabel, 'offsetBLabel')
        self.strength_label = self.ui.findChild(QLabel, 'strengthLabel')
        self.qr_label = self.ui.findChild(QLabel, 'qrLabel')

        # 通道选择
        self.radio_a = self.ui.findChild(QRadioButton, 'radioA')
        self.radio_b = self.ui.findChild(QRadioButton, 'radioB')
        self.radio_ab = self.ui.findChild(QRadioButton, 'radioAB')

        # 生命值权重系数
        self.health_weight_spin = self.ui.findChild(QDoubleSpinBox, 'doubleSpinBox')

    def init_comboboxes(self):
        waveforms = list(PULSE_DATA.keys())
        if self.wave_a_combo:
            self.wave_a_combo.addItems(waveforms)
            if CURRENT_WAVEFORM_A in waveforms:
                self.wave_a_combo.setCurrentText(CURRENT_WAVEFORM_A)
        if self.wave_b_combo:
            self.wave_b_combo.addItems(waveforms)
            if CURRENT_WAVEFORM_B in waveforms:
                self.wave_b_combo.setCurrentText(CURRENT_WAVEFORM_B)

    def connect_signals(self):
        if self.debug_check:
            self.debug_check.toggled.connect(self.toggle_debug)
        if self.monitor_life_check:
            self.monitor_life_check.toggled.connect(self.toggle_monitor_life)
        if self.wave_a_combo:
            self.wave_a_combo.currentTextChanged.connect(lambda t: self.change_waveform('A', t))
        if self.wave_b_combo:
            self.wave_b_combo.currentTextChanged.connect(lambda t: self.change_waveform('B', t))
        if self.radio_a:
            self.radio_a.toggled.connect(self.set_channel_mode)
        if self.radio_b:
            self.radio_b.toggled.connect(self.set_channel_mode)
        if self.radio_ab:
            self.radio_ab.toggled.connect(self.set_channel_mode)
        if self.health_weight_spin:
            self.health_weight_spin.valueChanged.connect(self.set_health_weight)
        if self.btn_select_pos:
            self.btn_select_pos.clicked.connect(self.select_pos_logger_dir)
        if self.btn_select_health:
            self.btn_select_health.clicked.connect(self.select_health_stamina_file)

    def set_channel_mode(self):
        if self.radio_a and self.radio_a.isChecked():
            self.dglab.set_channel_mode(1)
            print("🔧 [模式] 仅A通道")
        elif self.radio_b and self.radio_b.isChecked():
            self.dglab.set_channel_mode(2)
            print("🔧 [模式] 仅B通道")
        else:
            self.dglab.set_channel_mode(3)
            print("🔧 [模式] A/B通道同时")

    def set_health_weight(self, value):
        weight = int(value)
        self.dglab.set_health_weight(weight)
        print(f"🔧 [权重] 生命值权重系数设置为 {weight}")

    def start_dglab_thread(self):
        def run_loop():
            self.dglab_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.dglab_loop)
            self.dglab_loop.run_until_complete(self.dglab.connect_and_run())
        self.dglab_thread = threading.Thread(target=run_loop, daemon=True)
        self.dglab_thread.start()

    def start_timers(self):
        self.game_timer = QTimer()
        self.game_timer.timeout.connect(self.refresh_game_data)
        self.game_timer.start(1000)
        self.dglab_timer = QTimer()
        self.dglab_timer.timeout.connect(self.refresh_dglab_display)
        self.dglab_timer.start(500)
        self.qr_timer = QTimer()
        self.qr_timer.timeout.connect(self.update_qr_display)
        self.qr_timer.start(2000)

    def toggle_debug(self, checked):
        self.dglab.set_debug_output(checked)

    def toggle_monitor_life(self, checked):
        self.dglab.set_monitor_life(checked)

    def change_waveform(self, channel, waveform_name):
        if channel == 'A':
            self.dglab.current_waveform_index_a = list(PULSE_DATA.keys()).index(waveform_name)
            if self.dglab_loop:
                asyncio.run_coroutine_threadsafe(
                    self.dglab.send_waveform(Channel.A), self.dglab_loop
                )
        else:
            self.dglab.current_waveform_index_b = list(PULSE_DATA.keys()).index(waveform_name)
            if self.dglab_loop:
                asyncio.run_coroutine_threadsafe(
                    self.dglab.send_waveform(Channel.B), self.dglab_loop
                )

    def refresh_game_data(self):
        status = self.monitor.get_status()
        hp_current = status['health_current']
        hp_max = status['max_health']
        hp_percent = status['health_percent']

        if self.health_bar:
            self.health_bar.setValue(int(hp_percent * 100))
        if self.health_label:
            self.health_label.setText(f"{hp_current:.0f} / {hp_max:.0f} ({hp_percent * 100:.1f}%)")

        if self.combat_label:
            if status['is_combat']:
                self.combat_label.setText("⚔️ 战斗中")
                self.combat_label.setStyleSheet("color: red;")
            else:
                self.combat_label.setText("✅ 非战斗状态")
                self.combat_label.setStyleSheet("color: green;")

        player_in_game = status['player_in_game']
        district = status['district']
        pos = status['pos_data']
        if self.pos_label:
            spatial = pos.get('spatial', {})
            self.pos_label.setText(
                f"X: {spatial.get('x', 0):.1f}  Y: {spatial.get('y', 0):.1f}  "
                f"Z: {spatial.get('z', 0):.1f}  速度: {spatial.get('speed', 0):.1f}  "
                f"区域: {district}"
            )

        quest = pos.get('narrative', {})
        if self.quest_title:
            self.quest_title.setText(f"任务: {quest.get('questTitle', '')}")
        if self.quest_objective:
            self.quest_objective.setText(f"目标: {quest.get('questObjective', '')}")

        attr = pos.get('attributes', {})
        prof = pos.get('proficiency', {})
        attr_str = (
            f"体能: {attr.get('body', 0)}  智力: {attr.get('intelligence', 0)}  "
            f"反应: {attr.get('reflexes', 0)}\n"
            f"技术: {attr.get('tech', 0)}  镇定: {attr.get('cool', 0)}  "
            f"街头声望: {prof.get('streetCred', 0)}"
        )
        if self.attr_text:
            self.attr_text.setPlainText(attr_str)

        economy = pos.get('economy', {})
        if self.money_label:
            self.money_label.setText(f"欧元: {economy.get('euroDollars', 0):,}")

        combat_base = self.combat_base_spin.value() if self.combat_base_spin else 20
        idle_base = self.idle_base_spin.value() if self.idle_base_spin else 5
        death_duration = self.death_duration_spin.value() if self.death_duration_spin else 3

        dglab_config = {
            'combat_base': combat_base,
            'idle_base': idle_base,
            'death_max_strength': 99,
            'death_duration': death_duration
        }
        self.dglab.update_auto_target(
            status['health_percent'],
            status['is_combat'],
            status['is_dead'],
            status['stamina'],
            dglab_config,
            player_in_game
        )

    def refresh_dglab_display(self):
        if self.dglab.simple_control:
            a = round(self.dglab.simple_control.current_strength_a)
            a_limit = round(self.dglab.simple_control.a_limit)
            b = round(self.dglab.simple_control.current_strength_b)
            b_limit = round(self.dglab.simple_control.b_limit)
            if self.strength_label:
                self.strength_label.setText(f"A: {a}/{a_limit}   B: {b}/{b_limit}")
            offset_a = self.dglab.simple_control.manual_offset_a
            offset_b = self.dglab.simple_control.manual_offset_b
            if self.offset_a_label:
                self.offset_a_label.setText(f"A偏移: {offset_a:+d}")
            if self.offset_b_label:
                self.offset_b_label.setText(f"B偏移: {offset_b:+d}")

    def update_qr_display(self):
        qr_path = "dg_lab_qrcode.png"
        if os.path.exists(qr_path):
            try:
                pixmap = QPixmap(qr_path)
                pixmap = pixmap.scaled(200, 200)
                if self.qr_label:
                    self.qr_label.setPixmap(pixmap)
                    self.qr_label.setText("")
            except Exception as e:
                if self.qr_label:
                    self.qr_label.setText(f"加载失败: {e}")
                    self.qr_label.setPixmap(None)
        else:
            if self.qr_label:
                self.qr_label.setText("未生成二维码\n请连接 DG-Lab")
                self.qr_label.setPixmap(None)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = CyberpunkDGLabApp()
    sys.exit(app.exec())