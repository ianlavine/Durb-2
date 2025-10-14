import gymnasium as gym
from gymnasium import spaces
import numpy as np
import torch as th
import torch.nn as nn

from sb3_contrib.common.maskable.policies import MaskableMultiInputActorCriticPolicy
from sb3_contrib.common.wrappers import ActionMasker
from sb3_contrib.ppo_mask import MaskablePPO

from stable_baselines3.common.torch_layers import BaseFeaturesExtractor
from stable_baselines3.common.vec_env import DummyVecEnv
from stable_baselines3.common.callbacks import CheckpointCallback

from feature_extractors import GraphFeatureExtractor, gnn_policy_kwargs
from durba.envs.durba_env import DurbaEnv


# --- Define environment and masking ---
env = DurbaEnv(obs_type="gnn")

def mask_fn(env: gym.Env) -> np.ndarray:
    return env.valid_action_mask()

env = ActionMasker(env, mask_fn)


# --- Define checkpoint callback ---
checkpoint_callback = CheckpointCallback(
    save_freq=50000,
    save_path="ppomask_checkpoints/",
    name_prefix="rl_model_att_10_15_auto_expand_attack__resume_1",
    save_replay_buffer=True,
    save_vecnormalize=True,
)


# --- Load from checkpoint ---
# Example: the most recent checkpoint file might look like:
# "ppomask_checkpoints/rl_model_att_6_250000_steps.zip"

checkpoint_path = "ppomask_checkpoints/rl_model_att_10_15_auto_expand_attack__resume_1_1400000_steps.zip"

# Recreate the environment with ActionMasker
env = ActionMasker(DurbaEnv(obs_type="gnn"), mask_fn)

# Load the model from the checkpoint
model = MaskablePPO.load(checkpoint_path, env=env)

# OPTIONAL: Adjust hyperparameters if desired before resuming
model.learning_rate = 0.8e-4

# --- Continue training ---
model.learn(
    total_timesteps=4_000_000,  # Continue for more steps
    tb_log_name="run1_att_10_15_auto_expand_attack_rew_resume",
    callback=checkpoint_callback
)

# --- Save final model ---
model.save("ppo_mask_bot_durba_resumed")