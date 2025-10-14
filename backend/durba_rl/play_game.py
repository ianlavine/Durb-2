from durba.envs.durba_env import DurbaEnv

import matplotlib.pyplot as plt
import matplotlib.cm as cm
import time
import gymnasium as gym
from gymnasium import spaces
import numpy as np
import torch as th
import torch.nn as nn
import os
import glob
import datetime
from matplotlib.animation import FFMpegWriter  # ✅ for video saving

from sb3_contrib.common.maskable.policies import MaskableMultiInputActorCriticPolicy
from sb3_contrib.common.wrappers import ActionMasker
from sb3_contrib.ppo_mask import MaskablePPO

from stable_baselines3.common.torch_layers import BaseFeaturesExtractor
from stable_baselines3.common.vec_env import DummyVecEnv
from stable_baselines3.common.callbacks import CheckpointCallback
from feature_extractors import GraphFeatureExtractor, gnn_policy_kwargs

TICK_INTERVAL_SECONDS = 0.5

# ---------------------- MODIFIED plot_graph ----------------------
def plot_graph(nodes, edges, step, ax):
    ax.clear()

    # Collect unique owners (ignoring None) for coloring
    owners = [1, 2]
    color_map = {owner: cm.tab10(i % 10) for i, owner in enumerate(owners)}

    for node_id, node in nodes.items():
        color = "lightgray" if node.owner is None else color_map[node.owner]
        size = node.juice
        ax.scatter(node.x, node.y, s=size, c=color, edgecolors="black", zorder=3)
        ax.text(node.x, node.y + 1.5, str(node_id)+"_"+str(round(node.juice, 2)), fontsize=8, ha="center")

    for edge_id, edge in edges.items():
        src = nodes[edge.source_node_id]
        tgt = nodes[edge.target_node_id]
        if edge.flowing and edge.on:
            ax.annotate("",
                        xy=(tgt.x, tgt.y),
                        xytext=(src.x, src.y),
                        arrowprops=dict(arrowstyle="->", color=color_map[src.owner], lw=1),
                        zorder=2)
            ax.text((src.x+tgt.x)/2, (src.y+tgt.y)/2, str(edge_id), fontsize=8, ha="center")
        else:
            ax.annotate("",
                        xy=(tgt.x, tgt.y),
                        xytext=(src.x, src.y),
                        arrowprops=dict(arrowstyle="->", color="gray", lw=1),
                        zorder=2)
            ax.text((src.x+tgt.x)/2, (src.y+tgt.y)/2, str(edge_id), fontsize=8, ha="center")

    ax.set_aspect("equal")
    ax.set_xlabel("X")
    ax.set_ylabel("Y")
    ax.set_title(f"Directed Graph (Step {step})")

    for owner, color in color_map.items():
        ax.scatter([], [], c=color, label=f"Owner {owner}", s=100, edgecolors="black")
    ax.scatter([], [], c="lightgray", label="Owner None", s=100, edgecolors="black")
    ax.legend(title="Owners", loc="upper left", bbox_to_anchor=(1.05, 1))

    plt.tight_layout()
# -----------------------------------------------------------------

def mask_fn(env: gym.Env) -> np.ndarray:
    return env.valid_action_mask()

env = DurbaEnv(obs_type="gnn")
env = ActionMasker(env, mask_fn)
model = MaskablePPO.load("ppomask_checkpoints/rl_model_att_10_15_auto_expand_attack_3_1700000_steps.zip")

wins = 0

# ---------------------- SETUP VIDEO WRITER ----------------------
timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
output_dir = "outputs"
os.makedirs(output_dir, exist_ok=True)
video_filename = os.path.join(output_dir, f"graph_video_{timestamp}.mp4")

fig, ax = plt.subplots(figsize=(14, 9))
writer = FFMpegWriter(fps=1, metadata=dict(artist='Durba Simulation'), bitrate=1800)
# ----------------------------------------------------------------
lines = []
tot_rew = 0
obs, _ = env.reset()
steps = 0

with writer.saving(fig, video_filename, dpi=100):  # ✅ Save video context
    while True:
        print("Step :", str(steps) + "\n")
        lines.append("Step :"+str(steps) + "\n")
        plot_graph(env.env.game_manager.game_engine.state.nodes,
                    env.env.game_manager.game_engine.state.edges, steps, ax)
        writer.grab_frame()  # ✅ Capture current frame

        action, _states = model.predict(obs, action_masks=env.env.valid_action_mask())
        #print("Valid Act: ", env.env.valid_action_mask())
        lines.append("Action :"+str(env.env.game_manager.ai_player.actions_dict[int(action)]) + "\n")
        obs, rewards, dones, trunc, info = env.step(int(action))
        lines.append(str(info)+"\n")
        steps += 1
        tot_rew += rewards

        if dones:
            if rewards > 50:
                wins += 1
            break

with open("out_logs.txt", "w") as f:
    f.writelines(lines)
print(f"🎥 Video saved to: {video_filename}")
print(f"🎥 Video saved to: out_logs.txt")
print("Win Rate: ", wins, "\nTotal_rew: ", tot_rew)
