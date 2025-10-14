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
from matplotlib.animation import FFMpegWriter 

from sb3_contrib.common.maskable.policies import MaskableMultiInputActorCriticPolicy
from sb3_contrib.common.wrappers import ActionMasker
from sb3_contrib.ppo_mask import MaskablePPO

from stable_baselines3.common.torch_layers import BaseFeaturesExtractor
from stable_baselines3.common.vec_env import DummyVecEnv
from stable_baselines3.common.callbacks import CheckpointCallback
from feature_extractors import GraphFeatureExtractor, gnn_policy_kwargs

TICK_INTERVAL_SECONDS = 0.5

def mask_fn(env: gym.Env) -> np.ndarray:
    return env.valid_action_mask()

env = DurbaEnv(obs_type="gnn")
env = ActionMasker(env, mask_fn)
model = MaskablePPO.load("ppomask_checkpoints/rl_model_att_10_15_auto_expand_attack_3_1700000_steps.zip")

wins = 0
draws = 0

for n in range(500):
    print("Game No. ", n+1)
    obs, _ = env.reset()
    steps = 0

    while True:
        action, _states = model.predict(obs, action_masks=env.env.valid_action_mask())
        obs, rewards, dones, trunc, info = env.step(int(action))
        steps += 1
        #print(info)
        if dones:
            if info["r_winner_loser"]==0:
                draws += 1
            elif info["r_winner_loser"]==100:
                wins += 1
            break
    print(" Win Rate: ", wins/(n+1), "\n Draw Rate: ", draws/(n+1))

print("Win Rate: ", wins/(n+1), "\n Draw Rate: ", draws/(n+1))
