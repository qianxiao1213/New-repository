import asyncio
import socket
import time
import qrcode
import io
from typing import Dict, Any
from pydglab_ws import DGLabWSConnect, StrengthData, FeedbackButton, Channel, StrengthOperationType, RetCode
from config import PULSE_DATA, CURRENT_WAVEFORM_A, CURRENT_WAVEFORM_B, CONNECTION_TIMEOUT


class SimpleControl:
    def __init__(self):
        self.manual_offset_a = 0
        self.manual_offset_b = 0
        self.a_limit = 0
        self.b_limit = 0
        self.current_strength_a = 0
        self.current_strength_b = 0

    def update_limits(self, a_limit, b_limit):
        if a_limit != self.a_limit or b_limit != self.b_limit:
            self.a_limit = a_limit
            self.b_limit = b_limit
            return True
        return False


class DGLabController:
    def __init__(self):
        self.client = None
        self.simple_control = SimpleControl()
        self.available_waveforms = list(PULSE_DATA.keys())
        self.current_waveform_index_a = self.available_waveforms.index(
            CURRENT_WAVEFORM_A) if CURRENT_WAVEFORM_A in self.available_waveforms else 0
        self.current_waveform_index_b = self.available_waveforms.index(
            CURRENT_WAVEFORM_B) if CURRENT_WAVEFORM_B in self.available_waveforms else 0
        self.auto_target_a = 1
        self.auto_target_b = 1
        self.death_timer_end = 0.0
        self.debug_output = True
        self._connection_start_time = 0
        self._strength_send_interval = 0.05
        self._waveform_send_interval = 1
        self.monitor_life_enabled = True
        # 权重系数（默认100，表示 (1-health_percent)*100）
        self.health_weight = 100
        # 通道选择（1=仅A, 2=仅B, 3=AB同时）
        self.channel_mode = 3  # 1:A, 2:B, 3:AB
        # 玩家是否真正进入游戏世界（靠 spatial.district 判断）
        self.player_in_game = False

    def set_debug_output(self, enabled: bool):
        self.debug_output = enabled

    def set_monitor_life(self, enabled: bool):
        self.monitor_life_enabled = enabled

    def set_health_weight(self, weight: int):
        """设置生命值权重系数（0-200）"""
        self.health_weight = max(0, min(weight, 200))

    def set_channel_mode(self, mode: int):
        """设置通道模式: 1=仅A, 2=仅B, 3=AB同时"""
        self.channel_mode = mode

    def _show_qrcode(self, url: str):
        try:
            qr_ascii = qrcode.QRCode()
            qr_ascii.add_data(url)
            f = io.StringIO()
            qr_ascii.print_ascii(out=f)
            f.seek(0)
            print("请用 DG-Lab App 扫描以下二维码:")
            print(f.read())
        except Exception as e:
            print(f"生成ASCII二维码出错: {e}")
        qr_png = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=4)
        qr_png.add_data(url)
        qr_png.make(fit=True)
        img = qr_png.make_image(fill_color="black", back_color="white")
        img.save("dg_lab_qrcode.png")
        print("二维码PNG文件已保存为: dg_lab_qrcode.png")

    def get_host_ip(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
        return ip

    async def send_waveform(self, channel: Channel, waveform_name: str = None):
        if not self.client:
            return
        if waveform_name is None:
            if channel == Channel.A:
                waveform_name = self.available_waveforms[self.current_waveform_index_a % len(self.available_waveforms)]
            else:
                waveform_name = self.available_waveforms[self.current_waveform_index_b % len(self.available_waveforms)]
        pulse_data = PULSE_DATA.get(waveform_name)
        if not pulse_data:
            return
        if self.debug_output:
            print(f"📤 [波形] 发送到{channel.name}通道: {waveform_name}, 数据段数: {len(pulse_data)}")
        try:
            await self.client.clear_pulses(channel)
            await asyncio.sleep(0.05)
            chunk_size = 100
            for i in range(0, len(pulse_data), chunk_size):
                chunk = pulse_data[i:i + chunk_size]
                await self.client.add_pulses(channel, *chunk)
                await asyncio.sleep(0.05)
            if self.debug_output:
                print(f"✅ [波形] {channel.name}通道波形发送完成")
        except Exception as e:
            print(f"❌ [波形] 发送失败: {e}")

    async def _set_strength(self, channel: Channel, strength: int):
        if not self.client:
            return

        # 根据通道模式决定是否发送以及发送什么值
        if self.channel_mode == 1:  # 仅A通道
            if channel == Channel.A:
                # A通道正常发送
                pass
            else:
                # B通道发送0来关闭
                strength = 0

        elif self.channel_mode == 2:  # 仅B通道
            if channel == Channel.B:
                # B通道正常发送
                pass
            else:
                # A通道发送0来关闭
                strength = 0

        # channel_mode == 3 时，A和B都正常发送，不做额外处理

        limit = self.simple_control.a_limit if channel == Channel.A else self.simple_control.b_limit

        # 重要：即使 limit <= 0，也要发送强度 0 来关闭通道
        # 只有当 strength > 0 且 limit <= 0 时才等待
        if strength > 0 and limit <= 0:
            if self.debug_output:
                print(f"⚠️ [强度] {channel.name}通道上限未就绪 (limit={limit})，等待...")
            return

        final = max(0, min(int(strength), limit if limit > 0 else 200))

        if self.debug_output:
            print(f"📤 [强度] {channel.name}通道: 计算值={strength}, 上限={limit}, 最终发送={final}")

        try:
            await self.client.set_strength(channel, StrengthOperationType.SET_TO, final)
            if channel == Channel.A:
                self.simple_control.current_strength_a = final
            else:
                self.simple_control.current_strength_b = final
            if self.debug_output:
                print(f"✅ [强度] {channel.name}通道设置成功: {final}")
        except Exception as e:
            print(f"❌ [强度] {channel.name}通道设置失败: {e}")

    async def _handle_button(self, btn: FeedbackButton):
        if self.debug_output:
            print(f"🔘 [按钮] 收到: {btn.name}")
        if btn == FeedbackButton.A1:
            self.current_waveform_index_a += 1
            await self.send_waveform(Channel.A)
        elif btn == FeedbackButton.A2:
            self.simple_control.manual_offset_a += 1
            print(f"➕ A通道手动偏移 +1，当前偏移={self.simple_control.manual_offset_a}")
        elif btn == FeedbackButton.A3:
            self.simple_control.manual_offset_a -= 1
            print(f"➖ A通道手动偏移 -1，当前偏移={self.simple_control.manual_offset_a}")
        elif btn == FeedbackButton.B1:
            self.current_waveform_index_b += 1
            await self.send_waveform(Channel.B)
        elif btn == FeedbackButton.B2:
            self.simple_control.manual_offset_b += 1
            print(f"➕ B通道手动偏移 +1，当前偏移={self.simple_control.manual_offset_b}")
        elif btn == FeedbackButton.B3:
            self.simple_control.manual_offset_b -= 1
            print(f"➖ B通道手动偏移 -1，当前偏移={self.simple_control.manual_offset_b}")

    def update_auto_target(self, health_percent: float, is_combat: bool, is_dead: bool, stamina: float,
                           config: Dict[str, Any], player_in_game: bool = False):
        now = time.time()
        if self._connection_start_time == 0:
            return
        if now - self._connection_start_time < 5:
            return
        self.player_in_game = player_in_game
        if self.debug_output:
            print(f"📊 [游戏状态] 生命={health_percent * 100:.1f}%, 战斗={is_combat}, 死亡={is_dead}, "
                  f"体力={stamina}, 游戏中={player_in_game}")

        # 最高优先级：未进入游戏世界（district 为 Unknown/空），强度直接归零
        if not self.player_in_game:
            self.auto_target_a = 0
            self.auto_target_b = 0
            if self.debug_output:
                print(f"ℹ️ [过滤] 未进入游戏世界（district 不可用），强度设为0")
            return

        # 其次：死亡或体力耗尽
        if is_dead or stamina <= 0:
            self.death_timer_end = now + config.get('death_duration', 3)
            target = config.get('death_max_strength', 99)
            self.auto_target_a = target
            self.auto_target_b = target
            if self.debug_output:
                print(f"💀 [特殊] 强度拉满至 {target}")
            return

        # 死亡持续期内（保持高强度）
        if now < self.death_timer_end:
            return

        # 正常游戏逻辑
        base = config.get('combat_base' if is_combat else 'idle_base', 10)
        if self.monitor_life_enabled:
            add = (1 - health_percent) * self.health_weight
        else:
            add = 0
        target = base + add
        target = max(0, min(int(target), 200))
        self.auto_target_a = target
        self.auto_target_b = target
        if self.debug_output:
            print(f"🎯 [自动目标] 基础={base}, 附加={add:.0f}, 权重={self.health_weight}, 目标={target}")

    async def connect_and_run(self):
        ip = self.get_host_ip()
        ws_url = f'ws://{ip}:5678'
        try:
            async with DGLabWSConnect(ws_url, CONNECTION_TIMEOUT) as client:
                self.client = client
                url = client.get_qrcode()
                self._show_qrcode(url)
                await client.bind()
                print(f"✅ 已绑定 App {client.target_id}")
                self._connection_start_time = time.time()
                print("🔌 连接已建立，5秒稳定期...")

                await self.send_waveform(Channel.A)
                await self.send_waveform(Channel.B)


                # 独立任务：波形循环
                async def waveform_loop():
                    while True:
                        await asyncio.sleep(self._waveform_send_interval)
                        if self.client:
                            try:
                                await self.send_waveform(Channel.A)
                                await self.send_waveform(Channel.B)
                            except Exception as e:
                                print(f"波形发送异常: {e}")

                # 独立任务：强度循环
                async def strength_loop():
                    while True:
                        await asyncio.sleep(self._strength_send_interval)
                        if self.client and (time.time() - self._connection_start_time >= 5):
                            # 修改：不再要求两个通道都有上限，直接发送
                            a = self._get_target_strength(Channel.A)
                            b = self._get_target_strength(Channel.B)
                            await self._set_strength(Channel.A, a)
                            await self._set_strength(Channel.B, b)

                waveform_task = asyncio.create_task(waveform_loop())
                strength_task = asyncio.create_task(strength_loop())

                async for data in client.data_generator():
                    if isinstance(data, StrengthData):
                        a_limit = data.a_limit if data.a_limit >= 0 else 0
                        b_limit = data.b_limit if data.b_limit >= 0 else 0
                        self.simple_control.update_limits(a_limit, b_limit)
                        if a_limit > 0 and b_limit > 0:
                            print(f"📱 [设备] 上限 A={a_limit} B={b_limit} | 当前强度 A={data.a} B={data.b}")
                    elif isinstance(data, FeedbackButton):
                        await self._handle_button(data)
                    elif data == RetCode.CLIENT_DISCONNECTED:
                        print("⚠️ App 断开")
                        break

                waveform_task.cancel()
                strength_task.cancel()
                await asyncio.gather(waveform_task, strength_task, return_exceptions=True)

        except Exception as e:
            print(f"❌ DGLab 连接失败: {e}")
        finally:
            await self.cleanup()

    def _get_target_strength(self, channel: Channel):
        """获取目标强度（这个方法应该在类内部，不在 connect_and_run 内部）"""
        if channel == Channel.A:
            raw = self.auto_target_a + self.simple_control.manual_offset_a
            limit = self.simple_control.a_limit if self.simple_control.a_limit > 0 else 200
        else:
            raw = self.auto_target_b + self.simple_control.manual_offset_b
            limit = self.simple_control.b_limit if self.simple_control.b_limit > 0 else 200
        final = max(0, min(int(raw), limit))
        if self.debug_output:
            auto = self.auto_target_a if channel == Channel.A else self.auto_target_b
            offset = self.simple_control.manual_offset_a if channel == Channel.A else self.simple_control.manual_offset_b
            print(f"🔢 [计算] {channel.name}通道: 自动={auto}, 偏移={offset}, 最终={final}")
        return final

    async def cleanup(self):
        if self.client:
            try:
                await self.client.set_strength(Channel.A, StrengthOperationType.SET_TO, 0)
                await self.client.set_strength(Channel.B, StrengthOperationType.SET_TO, 0)
                print("🔌 强度归零")
            except:
                pass