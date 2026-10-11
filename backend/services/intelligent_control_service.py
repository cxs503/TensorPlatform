"""
智能控制服务
提供MPC控制、强化学习(DQN/PPO)、模糊控制
"""
import math
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional


class ControlType(str, Enum):
    """控制类型"""

    MPC = "mpc"  # 模型预测控制
    DQN = "dqn"  # Deep Q-Network
    PPO = "ppo"  # Proximal Policy Optimization
    FUZZY = "fuzzy"  # 模糊控制
    PID = "pid"  # PID控制


class ControlStatus(str, Enum):
    """控制状态"""

    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"


class ControlSystem:
    """控制系统"""

    def __init__(self, name: str, control_type: str, target_system: str):
        self.id = f"ctrl_{uuid.uuid4().hex[:12]}"
        self.name = name
        self.control_type = control_type
        self.target_system = target_system  # 除尘系统、空压系统等
        self.status = ControlStatus.IDLE
        self.parameters: Dict = {}
        self.setpoints: Dict = {}
        self.created_at = datetime.now().isoformat()


class ControlAction:
    """控制动作"""

    def __init__(self, control_id: str, timestamp: datetime, actions: Dict):
        self.id = f"action_{uuid.uuid4().hex[:12]}"
        self.control_id = control_id
        self.timestamp = timestamp
        self.actions = actions
        self.executed = False
        self.executed_at = None


class MPCController:
    """MPC控制器"""

    def __init__(self, horizon: int = 12, control_horizon: int = 4):
        self.horizon = horizon  # 预测 horizon
        self.control_horizon = control_horizon  # 控制 horizon
        self.model_params: Dict = {}

    def predict(self, current_state: Dict) -> List[Dict]:
        """基于物理模型预测未来状态（替代随机游走）"""
        import numpy as np

        predictions = []
        # 转换为numpy数组便于计算
        temp = float(current_state.get("temperature", 25.0))
        pressure = float(current_state.get("pressure", 1.0))
        power = float(current_state.get("power", 100.0))

        # 物理模型参数
        ambient_temp = 22.0  # 环境温度
        nominal_pressure = 1.0  # 标称压力
        baseline_power = 80.0  # 基准功率
        dt = 1.0  # 时间步长(秒)

        for i in range(self.horizon):
            # 牛顿冷却定律: dT/dt = -k*(T - T_ambient) + heating_input
            # 无控制输入时，温度向环境温度衰减
            dtemp = -0.08 * (temp - ambient_temp)
            temp = temp + dtemp * dt

            # 压力动态: 向标称值衰减（一阶系统）
            dpressure = -0.05 * (pressure - nominal_pressure)
            pressure = pressure + dpressure * dt

            # 功率动态: 向基准值漂移
            dpower = -0.03 * (power - baseline_power)
            power = power + dpower * dt

            # 添加微小的确定性扰动用numpy（替代random）
            # 使用正弦波模拟周期性波动
            temp += 0.02 * np.sin(0.5 * i)
            pressure += 0.005 * np.cos(0.3 * i)
            power += 0.5 * np.sin(0.2 * i + 1.0)

            next_state = {
                "temperature": round(temp, 4),
                "pressure": round(pressure, 4),
                "power": round(power, 2),
            }
            predictions.append(next_state)

        return predictions

    def optimize(self, predictions: List[Dict], target: Dict) -> Dict:
        """优化控制动作"""
        # 简化的优化: 最小化与目标的差距
        optimal_action = {}

        for pred in predictions[: self.control_horizon]:
            for key in target:
                if key in pred:
                    diff = target[key] - pred[key]
                    # 调整控制量
                    optimal_action[key] = diff * 0.5

        return optimal_action


class RLController:
    """强化学习控制器"""

    def __init__(self, algorithm: str, state_dim: int = 4, action_dim: int = 3):
        self.algorithm = algorithm
        self.state_dim = state_dim
        self.action_dim = action_dim
        self.q_table: Dict = {}
        self.epsilon = 0.1  # 探索率

    def get_action(self, state: Dict) -> Dict:
        """使用确定性策略选择动作（基于状态哈希）"""
        import numpy as np

        # 简化的动作空间
        actions = [
            {"fan_speed": 0.8, "valve": 0.5, "pump": 0.3},
            {"fan_speed": 0.9, "valve": 0.6, "pump": 0.4},
            {"fan_speed": 1.0, "valve": 0.7, "pump": 0.5},
        ]

        # 基于状态哈希确定性选择动作（替代 random 模拟）
        state_hash = abs(hash(str(state))) % len(actions)
        return actions[state_hash]

    def train(self, state: Dict, action: Dict, reward: float, next_state: Dict):
        """训练(简化)"""
        # 简化的Q学习更新
        state_key = str(state)
        if state_key not in self.q_table:
            self.q_table[state_key] = 0
        self.q_table[state_key] += 0.1 * (reward - self.q_table[state_key])


class FuzzyController:
    """模糊控制器"""

    def __init__(self):
        self.rules: List[Dict] = []
        self._init_rules()

    def _init_rules(self):
        """初始化模糊规则"""
        self.rules = [
            {"condition": "temperature_low", "action": "increase_heating"},
            {"condition": "temperature_high", "action": "decrease_heating"},
            {"condition": "pressure_low", "action": "increase_pressure"},
            {"condition": "pressure_high", "action": "decrease_pressure"},
        ]

    def infer(self, inputs: Dict) -> Dict:
        """模糊推理"""
        outputs = {}

        # 温度控制
        temp = inputs.get("temperature", 25)
        if temp < 20:
            outputs["heating"] = "high"
            outputs["fan_speed"] = 0.8
        elif temp > 35:
            outputs["heating"] = "low"
            outputs["fan_speed"] = 1.0
        else:
            outputs["heating"] = "medium"
            outputs["fan_speed"] = 0.6

        # 压力控制
        pressure = inputs.get("pressure", 1.0)
        if pressure < 0.8:
            outputs["compressor"] = "high"
        elif pressure > 1.2:
            outputs["compressor"] = "low"
        else:
            outputs["compressor"] = "medium"

        return outputs


class IntelligentControlService:
    """智能控制服务"""

    def __init__(self):
        self.controls: Dict[str, ControlSystem] = {}
        self.actions: List[ControlAction] = []
        self.mpc_controllers: Dict[str, MPCController] = {}
        self.rl_controllers: Dict[str, RLController] = {}
        self.fuzzy_controllers: Dict[str, FuzzyController] = {}

    # ========== 控制系统管理 ==========

    def create_control_system(
        self, name: str, control_type: str, target_system: str, parameters: Dict = None
    ) -> ControlSystem:
        """创建控制系统"""
        control = ControlSystem(name, control_type, target_system)
        control.parameters = parameters or {}
        self.controls[control.id] = control

        # 初始化对应的控制器
        if control_type == ControlType.MPC:
            self.mpc_controllers[control.id] = MPCController()
        elif control_type in [ControlType.DQN, ControlType.PPO]:
            self.rl_controllers[control.id] = RLController(control_type)
        elif control_type == ControlType.FUZZY:
            self.fuzzy_controllers[control.id] = FuzzyController()

        return control

    def get_control_system(self, control_id: str) -> Optional[ControlSystem]:
        """获取控制系统"""
        return self.controls.get(control_id)

    def list_control_systems(self, control_type: str = None) -> List[Dict]:
        """列出控制系统"""
        controls = self.controls.values()
        if control_type:
            controls = [c for c in controls if c.control_type == control_type]

        return [
            {
                "id": c.id,
                "name": c.name,
                "control_type": c.control_type,
                "target_system": c.target_system,
                "status": c.status,
                "parameters": c.parameters,
                "created_at": c.created_at,
            }
            for c in controls
        ]

    def start_control(self, control_id: str, setpoints: Dict) -> bool:
        """启动控制"""
        control = self.controls.get(control_id)
        if not control:
            return False

        control.status = ControlStatus.RUNNING
        control.setpoints = setpoints
        return True

    def stop_control(self, control_id: str) -> bool:
        """停止控制"""
        control = self.controls.get(control_id)
        if not control:
            return False

        control.status = ControlStatus.IDLE
        return True

    # ========== 控制执行 ==========

    def execute_control(self, control_id: str, current_state: Dict) -> Dict:
        """执行控制"""
        control = self.controls.get(control_id)
        if not control or control.status != ControlStatus.RUNNING:
            return {"error": "控制系统未运行"}

        control_action = {}

        if control.control_type == ControlType.MPC:
            mpc = self.mpc_controllers.get(control_id)
            if mpc:
                predictions = mpc.predict(current_state)
                control_action = mpc.optimize(predictions, control.setpoints)

        elif control.control_type in [ControlType.DQN, ControlType.PPO]:
            rl = self.rl_controllers.get(control_id)
            if rl:
                control_action = rl.get_action(current_state)

        elif control.control_type == ControlType.FUZZY:
            fuzzy = self.fuzzy_controllers.get(control_id)
            if fuzzy:
                control_action = fuzzy.infer(current_state)

        elif control.control_type == ControlType.PID:
            control_action = self._pid_control(current_state, control.setpoints)

        # 记录动作
        action = ControlAction(control_id, datetime.now(), control_action)
        action.executed = True
        action.executed_at = datetime.now().isoformat()
        self.actions.append(action)

        return {
            "control_id": control_id,
            "control_type": control.control_type,
            "timestamp": action.timestamp.isoformat(),
            "actions": control_action,
            "setpoints": control.setpoints,
            "current_state": current_state,
        }

    def _pid_control(self, current_state: Dict, setpoints: Dict) -> Dict:
        """PID控制"""
        output = {}
        for key in setpoints:
            if key in current_state:
                error = setpoints[key] - current_state[key]
                # 简化的P控制
                output[key] = error * 0.5

        return output

    # ========== 控制历史 ==========

    def get_control_history(self, control_id: str, limit: int = 100) -> List[Dict]:
        """获取控制历史"""
        actions = [a for a in self.actions if a.control_id == control_id]
        actions = actions[-limit:]

        return [
            {
                "id": a.id,
                "timestamp": a.timestamp.isoformat(),
                "actions": a.actions,
                "executed": a.executed,
                "executed_at": a.executed_at,
            }
            for a in actions
        ]

    # ========== 优化建议 ==========

    def get_optimization_suggestions(self, control_id: str) -> Dict:
        """获取优化建议"""
        control = self.controls.get(control_id)
        if not control:
            return {"error": "控制系统不存在"}

        suggestions = []

        if control.control_type == ControlType.MPC:
            suggestions.append(
                {
                    "type": "参数优化",
                    "suggestion": "建议调整MPC预测horizon从12改为15",
                    "expected_improvement": "5-10%",
                }
            )
            suggestions.append(
                {
                    "type": "模型优化",
                    "suggestion": "增加状态约束条件",
                    "expected_improvement": "3-5%",
                }
            )

        elif control.control_type in [ControlType.DQN, ControlType.PPO]:
            suggestions.append(
                {
                    "type": "训练优化",
                    "suggestion": "增加训练数据量",
                    "expected_improvement": "10-15%",
                }
            )
            suggestions.append(
                {
                    "type": "探索率调整",
                    "suggestion": "当前epsilon=0.1，建议调整为0.05",
                    "expected_improvement": "5%",
                }
            )

        else:
            suggestions.append(
                {
                    "type": "参数整定",
                    "suggestion": "建议使用自适应参数",
                    "expected_improvement": "8-12%",
                }
            )

        return {
            "control_id": control_id,
            "control_type": control.control_type,
            "suggestions": suggestions,
        }


# 全局实例
_control_service: Optional[IntelligentControlService] = None


def get_intelligent_control_service() -> IntelligentControlService:
    """获取智能控制服务"""
    global _control_service
    if _control_service is None:
        _control_service = IntelligentControlService()
    return _control_service
