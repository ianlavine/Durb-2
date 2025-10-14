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
from durba.envs.custom_monitor import CustomMonitor, InfoMetricCallback
env = DurbaEnv(obs_type="gnn")


def mask_fn(env: gym.Env) -> np.ndarray:
    # Do whatever you'd like in this function to return the action mask
    # for the current env. In this example, we assume the env has a
    # helpful method we can rely on.
    return env.valid_action_mask()

policy_kwargs=dict(
        features_extractor_class=GraphFeatureExtractor,
        features_extractor_kwargs=dict(size=env.size, features_dim=256)
    )

checkpoint_callback = CheckpointCallback(
        save_freq=50000,  # Save every 10,000 steps
        save_path="ppomask_checkpoints/",
        name_prefix="rl_model_att_10_15_auto_expand_attack_3",
        save_replay_buffer=True,  # Optional: Save the replay buffer
        save_vecnormalize=True,  # Optional: Save VecNormalize statistics
    )
info_callback = InfoMetricCallback()
env = CustomMonitor(env)
env = ActionMasker(env, mask_fn) # Wrap to enable masking

# MaskablePPO behaves the same as SB3's PPO unless the env is wrapped
# with ActionMasker. If the wrapper is detected, the masks are automatically
# retrieved and used when learning. Note that MaskablePPO does not accept
# a new action_mask_fn kwarg, as it did in an earlier draft.
model = MaskablePPO("MultiInputPolicy", env, gamma=0.999, learning_rate=1e-4,  n_steps=4096, policy_kwargs= gnn_policy_kwargs, verbose=1, tensorboard_log="logs/durba_maskppo_single_discrete/")
model.learn(total_timesteps=4000000, tb_log_name="run1_att_10_15_auto_expand_attack_rew", callback=[checkpoint_callback, info_callback])
model.save("ppo_mask_bot_durba_10_15")