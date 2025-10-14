import numpy as np
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.callbacks import BaseCallback


class CustomMonitor(Monitor):
    def __init__(self, env, filename=None, allow_early_resets=False):
        super().__init__(env, filename, allow_early_resets)
        
        # --- Episode-level metrics ---
        self.episode_rewards = []
        self.episode_lengths = []
        self.episode_opponent_nodes = []
        self.episode_agent_nodes = []
        self.episode_wins_count = []
        self.episode_draws_count = []

        # --- Reward component tracking ---
        self.reward_components = {
            "r_attack": [],
            "r_neutral": [],
            "r_defence": [],
            "r_winner_loser": [],
            "r_own": [],
            "r_dead_end": [],
            "r_act": [],
            "r_enemy_control": [],
        }

        self.reward_components_count = {
            "r_attack": [],
            "r_neutral": [],
            "r_defence": [],
            "r_winner_loser": [],
            "r_own": [],
            "r_dead_end": [],
            "r_act": [],
            "r_enemy_control": [],
        }

    def step(self, action):
        obs, reward, terminated, truncated, info = super().step(action)

        # --- Track reward components (from info["rew_info"]) ---
        for key in self.reward_components.keys():
            self.reward_components[key].append(info.get(key, 0))

        # --- When episode ends, record all summary info ---
        if terminated or truncated:
            # New metrics from info dict
            self.episode_opponent_nodes.append(info.get("opponent_nodes", 0))
            self.episode_agent_nodes.append(info.get("agent_nodes", 0))
            self.episode_wins_count.append(1 if info["r_winner_loser"]==100 else 0)
            self.episode_draws_count.append(1 if info["r_winner_loser"]==0 else 0)
            info.update(self.get_episode_stats())
            for key in self.reward_components.keys():
                self.reward_components_count[key].append(sum(self.reward_components.get(key, 0)))
            
            for k in self.reward_components:
                self.reward_components[k].clear()
            

        return obs, reward, terminated, truncated, info

    def get_episode_stats(self):
        # --- Base performance metrics ---
        stats = {
            "env/ep_mean_opponent_nodes": np.mean(self.episode_opponent_nodes) if self.episode_opponent_nodes else 0,
            "env/ep_mean_agent_nodes": np.mean(self.episode_agent_nodes) if self.episode_agent_nodes else 0,
            "env/ep_mean_wins": np.mean(self.episode_wins_count) if self.episode_wins_count else 0,
            "env/ep_mean_draws": np.mean(self.episode_draws_count) if self.episode_draws_count else 0,
        }

        # --- Reward component means ---
        for key, values in self.reward_components_count.items():
            stats[f"rewards/ep_mean_{key}"] = np.mean(values) if values else 0

        self.episode_opponent_nodes.clear()
        self.episode_agent_nodes.clear()
        self.episode_wins_count.clear()
        self.episode_draws_count.clear()
        for k in self.reward_components_count:
            self.reward_components_count[k].clear()

        return stats
    
    def valid_action_mask(self):
        return self.env.valid_action_mask()
    

class InfoMetricCallback(BaseCallback):
    """
    Logs numeric values from `info` dict and CustomMonitor episode stats.
    """
    def _on_step(self) -> bool:
        infos = self.locals.get("infos", [])
        if infos:
            # Log numeric info values
            for info in infos:
                for key, value in info.items():
                    if "ep_mean" in key:
                        if isinstance(value, (int, float)):
                            self.logger.record(f"info/{key}", value)

        return True