"""强化学习API — Q-Learning/DQN 策略优化"""
import json, uuid, threading, time, numpy as np
from datetime import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/rl", tags=["强化学习"])

_jobs = {}

class RLTrainReq(BaseModel):
    algorithm: str = "qlearning"  # qlearning | dqn
    env: str = "cartpole"
    episodes: int = 500
    learning_rate: float = 0.1

@router.get("/status")
def rl_status():
    try: import gym; has_gym = True
    except: has_gym = False
    return {"gym_available": has_gym, "algorithms": ["qlearning","dqn","ppo"], "environments": ["cartpole","mountain_car","energy_storage"]}

@router.post("/train")
def train_rl(req: RLTrainReq):
    job_id = str(uuid.uuid4())[:8]
    _jobs[job_id] = {"job_id": job_id, "config": req.dict(), "status": "created", "progress": 0, "created_at": datetime.now().isoformat()}
    def simulate():
        try:
            # gym 缺失时退化为纯 numpy Q-learning 模拟(演示态),保证 completed
            try:
                import gym  # noqa: F401
                _gym_ok = True
            except ImportError:
                _gym_ok = False
            rewards = []
            eps = max(int(req.episodes), 1)
            if not _gym_ok:
                # 简化的离散状态 Q-learning:状态/动作空间各 10/2,Q 表逐步更新,reward 随训练上升
                Q = np.zeros((10, 2))
                for ep in range(eps):
                    s = 0; total_reward = 0
                    for _step in range(100):
                        a = int(np.argmax(Q[s])) if np.random.random() > 0.1 else np.random.randint(2)
                        ns = min(9, max(0, s + (1 if a == 1 else -1) + np.random.randint(-1, 2)))
                        reward = 1.0 if ns >= 7 else 0.1
                        Q[s, a] += req.learning_rate * (reward + 0.99 * np.max(Q[ns]) - Q[s, a])
                        s = ns; total_reward += reward
                    rewards.append(total_reward)
                    if (ep + 1) % max(1, eps // 10) == 0:
                        _jobs[job_id]["progress"] = int((ep + 1) / eps * 100)
                _jobs[job_id]["status"] = "completed"
                _jobs[job_id]["progress"] = 100
                _jobs[job_id]["mode"] = "simulated"
                _jobs[job_id]["metrics"] = {"avg_reward_last100": round(float(np.mean(rewards[-100:])), 2), "best_reward": round(float(max(rewards)), 2), "mode": "simulated", "note": "依赖 gym 未安装,使用 numpy 模拟训练。指标非真实值。"}
                _jobs[job_id]["note"] = "依赖 gym 未安装,使用 numpy 模拟训练。指标非真实值。"
                return
            import gym
            env = gym.make("CartPole-v1")
            Q = np.zeros((10, 10, 2))
            for ep in range(req.episodes):
                reset_ret = env.reset()
                state = reset_ret[0] if isinstance(reset_ret, tuple) else reset_ret
                state_idx = min(int(np.digitize(state, np.linspace(-2.4, 2.4, 9))), 9)
                total_reward = 0
                for _ in range(200):
                    action = np.argmax(Q[state_idx]) if np.random.random() > 0.1 else env.action_space.sample()
                    next_state, reward, done, _, _ = env.step(action)
                    next_idx = min(int(np.digitize(next_state, np.linspace(-2.4, 2.4, 9))), 9)
                    Q[state_idx, next_idx, action] += req.learning_rate * (reward + 0.99 * np.max(Q[next_idx]) - Q[state_idx, next_idx, action])
                    state_idx = next_idx; total_reward += reward
                    if done: break
                rewards.append(total_reward)
                if (ep+1) % 100 == 0:
                    _jobs[job_id]["progress"] = int((ep+1)/req.episodes*100)
            env.close()
            _jobs[job_id]["status"] = "completed"
            _jobs[job_id]["metrics"] = {"avg_reward_last100": round(np.mean(rewards[-100:]), 2), "best_reward": round(max(rewards), 2)}
        except Exception as e:
            _jobs[job_id]["status"] = "failed"
            _jobs[job_id]["error"] = str(e)
    threading.Thread(target=simulate, daemon=True).start()
    return {"job_id": job_id, "status": "created"}

@router.get("/jobs")
def list_jobs(): return {"jobs": list(_jobs.values())}

@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    if job_id not in _jobs: raise HTTPException(404)
    return _jobs[job_id]
